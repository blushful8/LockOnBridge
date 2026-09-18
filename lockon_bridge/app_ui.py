from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from . import __version__
from .agent import AgentStatus, BridgeAgent
from .autostart import (
    ensure_firewall_rule,
    ensure_install_copy,
    full_uninstall,
    register_autostart,
    register_uninstall_entry,
    stop_other_bridge_processes,
    unregister_autostart,
)
from .paths import PRODUCT_NAME, log_dir, log_file
from .settings import BridgeSettings, load_settings, save_settings, update_settings

log = logging.getLogger("lockon_bridge")

BG = "#1a1d23"
PANEL = "#242830"
FG = "#e8eaed"
MUTED = "#9aa0a6"
ACCENT = "#c45c26"
ACCENT_DIM = "#8a3f1a"
OK = "#3d9a6a"
OFF = "#6b7280"


class BridgeApp:
    def __init__(self, *, start_hidden: bool = False) -> None:
        self.agent = BridgeAgent()
        self.settings = load_settings()
        self._tray = None
        self._tray_thread: Optional[threading.Thread] = None
        self._closing = False

        self.root = tk.Tk()
        self.root.title(f"{PRODUCT_NAME} {__version__}")
        self.root.configure(bg=BG)
        self.root.minsize(420, 460)
        self.root.geometry("440x500")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_window)

        self._build_ui()
        self.agent.set_status_callback(self._on_agent_status)

        if self.settings.enabled:
            self._start_enabled(persist=False)
        else:
            self._set_ui_enabled(False)
            self.status_var.set("Disabled — Bridge will not start and uses no resources")

        if start_hidden and self.settings.enabled:
            self.root.withdraw()
            self._ensure_tray()
        elif start_hidden and not self.settings.enabled:
            # Autostart fired but user disabled — exit with zero footprint.
            self.root.after(50, self._exit_clean)

    def run(self) -> int:
        self.root.mainloop()
        return 0

    def _build_ui(self) -> None:
        pad = {"padx": 20, "pady": 8}

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", **pad)
        tk.Label(
            header,
            text=PRODUCT_NAME,
            font=("Segoe UI Semibold", 18),
            fg=FG,
            bg=BG,
        ).pack(anchor="w")
        tk.Label(
            header,
            text="OCR companion for LockOn (War Thunder)",
            font=("Segoe UI", 10),
            fg=MUTED,
            bg=BG,
        ).pack(anchor="w")

        card = tk.Frame(self.root, bg=PANEL, highlightthickness=0)
        card.pack(fill="x", padx=20, pady=12)

        self.enabled_var = tk.BooleanVar(value=self.settings.enabled)
        toggle_row = tk.Frame(card, bg=PANEL)
        toggle_row.pack(fill="x", padx=16, pady=(16, 8))
        tk.Label(
            toggle_row,
            text="Bridge enabled",
            font=("Segoe UI Semibold", 12),
            fg=FG,
            bg=PANEL,
        ).pack(side="left")
        self.toggle = ttk.Checkbutton(
            toggle_row,
            variable=self.enabled_var,
            command=self._on_toggle,
            style="Switch.TCheckbutton",
        )
        self.toggle.pack(side="right")

        self.status_var = tk.StringVar(value="…")
        self.status_label = tk.Label(
            card,
            textvariable=self.status_var,
            font=("Segoe UI", 10),
            fg=MUTED,
            bg=PANEL,
            wraplength=380,
            justify="left",
        )
        self.status_label.pack(anchor="w", padx=16, pady=(0, 8))

        self.badge_var = tk.StringVar(value="OFF")
        self.badge = tk.Label(
            card,
            textvariable=self.badge_var,
            font=("Segoe UI Semibold", 11),
            fg="#ffffff",
            bg=OFF,
            padx=10,
            pady=4,
        )
        self.badge.pack(anchor="w", padx=16, pady=(0, 16))

        tip = tk.Label(
            self.root,
            text=(
                "When enabled: starts with Windows, wakes with War Thunder, "
                "stops when the game exits.\n"
                "When disabled: no autostart, no background process, no CPU/RAM use."
            ),
            font=("Segoe UI", 9),
            fg=MUTED,
            bg=BG,
            justify="left",
            wraplength=400,
        )
        tip.pack(anchor="w", padx=20, pady=(0, 8))

        port_row = tk.Frame(self.root, bg=BG)
        port_row.pack(fill="x", padx=20, pady=4)
        tk.Label(port_row, text="HTTP port", font=("Segoe UI", 10), fg=FG, bg=BG).pack(
            side="left"
        )
        self.port_var = tk.StringVar(value=str(self.settings.port))
        port_entry = tk.Entry(
            port_row,
            textvariable=self.port_var,
            width=8,
            font=("Consolas", 11),
            bg=PANEL,
            fg=FG,
            insertbackground=FG,
            relief="flat",
        )
        port_entry.pack(side="right")
        port_entry.bind("<Return>", lambda _e: self._save_port())
        port_entry.bind("<FocusOut>", lambda _e: self._save_port())

        btns = tk.Frame(self.root, bg=BG)
        btns.pack(fill="x", padx=20, pady=16)

        self._btn(btns, "Open logs", self._open_logs).pack(fill="x", pady=4)
        self._btn(btns, "Hide to tray", self._hide_to_tray).pack(fill="x", pady=4)
        self._btn(btns, "Uninstall…", self._uninstall, danger=True).pack(fill="x", pady=4)
        self._btn(btns, "Quit", self._quit_keep_enabled).pack(fill="x", pady=4)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

    def _btn(self, parent: tk.Widget, text: str, command, *, danger: bool = False) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            font=("Segoe UI", 10),
            fg="#ffffff" if danger else FG,
            bg=ACCENT_DIM if danger else PANEL,
            activebackground=ACCENT if danger else "#2f343c",
            activeforeground="#ffffff",
            relief="flat",
            padx=12,
            pady=8,
            cursor="hand2",
        )

    def _on_toggle(self) -> None:
        want = bool(self.enabled_var.get())
        if want:
            self._start_enabled(persist=True)
        else:
            self._disable_completely()

    def _start_enabled(self, *, persist: bool) -> None:
        port = self._read_port()
        self.settings = update_settings(enabled=True, port=port)
        stop_other_bridge_processes()
        ensure_install_copy()
        register_autostart()
        register_uninstall_entry()
        ensure_firewall_rule(self.settings.port)
        self.agent.start(self.settings)
        self._set_ui_enabled(True)
        self._ensure_tray()
        if persist:
            self.status_var.set("Enabled — waiting for War Thunder")
        log.info("Bridge enabled (port %s)", self.settings.port)

    def _disable_completely(self) -> None:
        self.settings = update_settings(enabled=False)
        self.agent.stop(join=True)
        unregister_autostart()
        self._set_ui_enabled(False)
        self.status_var.set("Disabled — no autostart, no background work")
        self.badge_var.set("OFF")
        self.badge.configure(bg=OFF)
        self._destroy_tray()
        log.info("Bridge disabled")

    def _set_ui_enabled(self, enabled: bool) -> None:
        self.enabled_var.set(enabled)
        if enabled:
            self.badge_var.set("ON")
            self.badge.configure(bg=OK)
        else:
            self.badge_var.set("OFF")
            self.badge.configure(bg=OFF)

    def _read_port(self) -> int:
        try:
            port = int(self.port_var.get().strip())
            if 1 <= port <= 65535:
                return port
        except ValueError:
            pass
        self.port_var.set(str(self.settings.port))
        return self.settings.port

    def _save_port(self) -> None:
        port = self._read_port()
        if port == self.settings.port:
            return
        self.settings = update_settings(port=port)
        if self.settings.enabled:
            ensure_firewall_rule(port)
            self.agent.apply_settings(self.settings)
            # Restart agent loop with new port
            self.agent.stop(join=True)
            self.agent.start(self.settings)
            self.status_var.set(f"Port saved ({port})")

    def _on_agent_status(self, status: AgentStatus, detail: str) -> None:
        def apply() -> None:
            if self._closing:
                return
            self.status_var.set(detail)
            if status == AgentStatus.ACTIVE:
                self.badge_var.set("ACTIVE")
                self.badge.configure(bg=ACCENT)
            elif status == AgentStatus.IDLE:
                self.badge_var.set("IDLE")
                self.badge.configure(bg=OK)
            elif status == AgentStatus.DISABLED:
                if self.settings.enabled:
                    self.badge_var.set("ON")
                    self.badge.configure(bg=OK)
                else:
                    self.badge_var.set("OFF")
                    self.badge.configure(bg=OFF)

        try:
            self.root.after(0, apply)
        except tk.TclError:
            pass

    def _open_logs(self) -> None:
        path = log_dir()
        path.mkdir(parents=True, exist_ok=True)
        log_file().touch(exist_ok=True)
        os.startfile(path)  # noqa: S606

    def _hide_to_tray(self) -> None:
        if not self.settings.enabled:
            messagebox.showinfo(
                PRODUCT_NAME,
                "Enable Bridge first. While disabled there is nothing to keep in the tray.",
            )
            return
        self._ensure_tray()
        self.root.withdraw()

    def _ensure_tray(self) -> None:
        if self._tray is not None:
            return
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            log.warning("pystray/Pillow not available — tray disabled")
            return

        image = Image.new("RGB", (64, 64), ACCENT)
        draw = ImageDraw.Draw(image)
        draw.rectangle((12, 12, 52, 52), outline=(255, 255, 255), width=3)
        draw.line((20, 32, 44, 32), fill=(255, 255, 255), width=3)

        menu = pystray.Menu(
            pystray.MenuItem("Open", self._show_window, default=True),
            pystray.MenuItem("Disable Bridge", self._tray_disable),
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self._tray = pystray.Icon("lockon_bridge", image, PRODUCT_NAME, menu)
        self._tray_thread = threading.Thread(target=self._tray.run, name="tray", daemon=True)
        self._tray_thread.start()

    def _destroy_tray(self) -> None:
        tray = self._tray
        self._tray = None
        if tray is not None:
            try:
                tray.stop()
            except Exception:  # noqa: BLE001
                pass

    def _show_window(self, _icon=None, _item=None) -> None:
        def show() -> None:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()

        self.root.after(0, show)

    def _tray_disable(self, _icon=None, _item=None) -> None:
        self.root.after(0, self._disable_completely)
        self.root.after(100, self._show_window)

    def _tray_quit(self, _icon=None, _item=None) -> None:
        self.root.after(0, self._quit_keep_enabled)

    def _on_close_window(self) -> None:
        if self.settings.enabled:
            self._hide_to_tray()
        else:
            self._exit_clean()

    def _quit_keep_enabled(self) -> None:
        """Exit the process. If still enabled, autostart will bring it back next logon."""
        if self.settings.enabled:
            # Keep enabled+autostart; just stop this process.
            self.agent.stop(join=True)
        self._exit_clean()

    def _uninstall(self) -> None:
        ok = messagebox.askyesno(
            PRODUCT_NAME,
            "Remove LockOn Bridge completely?\n\n"
            "This disables autostart, stops the agent, and deletes local files.",
        )
        if not ok:
            return
        self._closing = True
        self.agent.stop(join=True)
        self._destroy_tray()
        full_uninstall()
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        os._exit(0)

    def _exit_clean(self) -> None:
        self._closing = True
        self.agent.stop(join=True)
        self._destroy_tray()
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def run_ui(*, start_hidden: bool = False) -> int:
    app = BridgeApp(start_hidden=start_hidden)
    return app.run()
