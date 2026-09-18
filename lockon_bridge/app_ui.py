from __future__ import annotations

import logging
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
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
from .i18n import EN, UK, Strings, strings_for
from .paths import PRODUCT_NAME, data_root, is_frozen, log_dir, log_file
from .settings import load_settings, update_settings
from .updater import (
    apply_update_and_restart,
    download_release_exe,
    fetch_latest_release,
    is_newer,
)

log = logging.getLogger("lockon_bridge")

BG = "#1a1d23"
PANEL = "#242830"
FG = "#e8eaed"
MUTED = "#9aa0a6"
ACCENT = "#c45c26"
ACCENT_DIM = "#8a3f1a"
OK = "#3d9a6a"
OFF = "#6b7280"


def _asset_path(name: str) -> Path | None:
    candidates = [
        Path(__file__).resolve().parent.parent / "assets" / name,
    ]
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.insert(0, Path(meipass) / "assets" / name)
        candidates.insert(0, Path(sys.executable).resolve().parent / "assets" / name)
    for path in candidates:
        if path.is_file():
            return path
    return None


def _load_brand_image(size: int = 64):
    from PIL import Image

    path = _asset_path("lockon_bridge.png")
    if path is not None:
        image = Image.open(path).convert("RGBA")
        if image.size != (size, size):
            image = image.resize((size, size), Image.Resampling.LANCZOS)
        return image
    return Image.new("RGBA", (size, size), (18, 20, 24, 255))


class BridgeApp:
    def __init__(self, *, start_hidden: bool = False) -> None:
        self.agent = BridgeAgent()
        self.settings = load_settings()
        self.strings: Strings = strings_for(self.settings.language)
        self._tray = None
        self._tray_thread: Optional[threading.Thread] = None
        self._closing = False
        self._last_agent_status = AgentStatus.DISABLED
        self._updating = False

        self.root = tk.Tk()
        self.root.title(f"{PRODUCT_NAME} {__version__}")
        self.root.configure(bg=BG)
        self.root.minsize(440, 560)
        self.root.geometry("460x600")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_window)
        self._apply_window_icon()

        # First open of the .exe installs a stable LocalAppData copy + Desktop shortcut.
        if is_frozen():
            ensure_install_copy()
            register_uninstall_entry()

        self.enabled_var = tk.BooleanVar(value=self.settings.enabled)
        self.port_var = tk.StringVar(value=str(self.settings.port))
        self.status_var = tk.StringVar(value="…")
        self.badge_var = tk.StringVar(value="")
        self.language_var = tk.StringVar(
            value=self.strings.lang_uk if self.settings.language == "uk" else self.strings.lang_en
        )

        self._build_ui()
        self.agent.set_status_callback(self._on_agent_status)

        if self.settings.enabled:
            self._start_enabled(persist=False)
        else:
            self._set_ui_enabled(False)
            self.status_var.set(self.strings.status_disabled)

        if start_hidden and self.settings.enabled:
            self.root.withdraw()
            self._ensure_tray()
        elif start_hidden and not self.settings.enabled:
            self.root.after(50, self._exit_clean)

    def run(self) -> int:
        self.root.mainloop()
        return 0

    def _apply_window_icon(self) -> None:
        ico = _asset_path("lockon_bridge.ico")
        png = _asset_path("lockon_bridge.png")
        try:
            if ico is not None and sys.platform == "win32":
                self.root.iconbitmap(default=str(ico))
            elif png is not None:
                self._window_icon = tk.PhotoImage(file=str(png))
                self.root.iconphoto(True, self._window_icon)
        except Exception as exc:  # noqa: BLE001
            log.debug("window icon skipped: %s", exc)

    def _build_ui(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()

        t = self.strings
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
        self.subtitle_label = tk.Label(
            header,
            text=t.subtitle,
            font=("Segoe UI", 10),
            fg=MUTED,
            bg=BG,
        )
        self.subtitle_label.pack(anchor="w")

        card = tk.Frame(self.root, bg=PANEL, highlightthickness=0)
        card.pack(fill="x", padx=20, pady=12)

        toggle_row = tk.Frame(card, bg=PANEL)
        toggle_row.pack(fill="x", padx=16, pady=(16, 8))
        self.enabled_label = tk.Label(
            toggle_row,
            text=t.bridge_enabled,
            font=("Segoe UI Semibold", 12),
            fg=FG,
            bg=PANEL,
        )
        self.enabled_label.pack(side="left")
        ttk.Checkbutton(
            toggle_row,
            variable=self.enabled_var,
            command=self._on_toggle,
        ).pack(side="right")

        self.status_label = tk.Label(
            card,
            textvariable=self.status_var,
            font=("Segoe UI", 10),
            fg=MUTED,
            bg=PANEL,
            wraplength=400,
            justify="left",
        )
        self.status_label.pack(anchor="w", padx=16, pady=(0, 8))

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

        self.tip_label = tk.Label(
            self.root,
            text=t.tip,
            font=("Segoe UI", 9),
            fg=MUTED,
            bg=BG,
            justify="left",
            wraplength=410,
        )
        self.tip_label.pack(anchor="w", padx=20, pady=(0, 8))

        port_row = tk.Frame(self.root, bg=BG)
        port_row.pack(fill="x", padx=20, pady=4)
        self.port_label = tk.Label(
            port_row,
            text=t.http_port,
            font=("Segoe UI", 10),
            fg=FG,
            bg=BG,
        )
        self.port_label.pack(side="left")
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

        lang_row = tk.Frame(self.root, bg=BG)
        lang_row.pack(fill="x", padx=20, pady=8)
        self.language_label = tk.Label(
            lang_row,
            text=t.language,
            font=("Segoe UI", 10),
            fg=FG,
            bg=BG,
        )
        self.language_label.pack(side="left")
        self.language_combo = ttk.Combobox(
            lang_row,
            textvariable=self.language_var,
            values=(t.lang_en, t.lang_uk),
            state="readonly",
            width=14,
        )
        self.language_combo.pack(side="right")
        self.language_combo.bind("<<ComboboxSelected>>", self._on_language_chosen)

        btns = tk.Frame(self.root, bg=BG)
        btns.pack(fill="x", padx=20, pady=12)
        self.btn_logs = self._btn(btns, t.open_logs, self._open_logs)
        self.btn_logs.pack(fill="x", pady=4)
        self.btn_tray = self._btn(btns, t.hide_to_tray, self._hide_to_tray)
        self.btn_tray.pack(fill="x", pady=4)
        self.btn_update = self._btn(btns, t.check_updates, self._check_updates)
        self.btn_update.pack(fill="x", pady=4)
        self.btn_uninstall = self._btn(btns, t.uninstall, self._uninstall, danger=True)
        self.btn_uninstall.pack(fill="x", pady=4)
        self.btn_quit = self._btn(btns, t.quit, self._quit_keep_enabled)
        self.btn_quit.pack(fill="x", pady=4)

        try:
            ttk.Style().theme_use("clam")
        except tk.TclError:
            pass

        self._refresh_badge_for_status(self._last_agent_status)

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

    def _on_language_chosen(self, _event=None) -> None:
        selected = self.language_var.get()
        language = "uk" if selected in (EN.lang_uk, UK.lang_uk) else "en"
        if language == self.settings.language:
            return
        self.settings = update_settings(language=language)
        self.strings = strings_for(language)
        self.language_var.set(self.strings.lang_uk if language == "uk" else self.strings.lang_en)
        was_enabled = self.settings.enabled
        self._build_ui()
        self.enabled_var.set(was_enabled)
        if was_enabled:
            self.status_var.set(self.strings.status_enabled_waiting)
            self._destroy_tray()
            self._ensure_tray()
        else:
            self.status_var.set(self.strings.status_disabled)
        self._refresh_badge_for_status(self._last_agent_status)

    def _on_toggle(self) -> None:
        if bool(self.enabled_var.get()):
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
            self.status_var.set(self.strings.status_enabled_waiting)
        log.info("Bridge enabled (port %s)", self.settings.port)

    def _disable_completely(self) -> None:
        self.settings = update_settings(enabled=False)
        self.agent.stop(join=True)
        unregister_autostart()
        self._set_ui_enabled(False)
        self.status_var.set(self.strings.status_disabled)
        self._refresh_badge_for_status(AgentStatus.DISABLED)
        self._destroy_tray()
        log.info("Bridge disabled")

    def _set_ui_enabled(self, enabled: bool) -> None:
        self.enabled_var.set(enabled)
        self._refresh_badge_for_status(
            AgentStatus.IDLE if enabled else AgentStatus.DISABLED
        )

    def _refresh_badge_for_status(self, status: AgentStatus) -> None:
        t = self.strings
        if status == AgentStatus.ACTIVE:
            self.badge_var.set(t.badge_active)
            self.badge.configure(bg=ACCENT)
        elif status == AgentStatus.IDLE:
            self.badge_var.set(t.badge_idle)
            self.badge.configure(bg=OK)
        elif status == AgentStatus.STOPPING:
            self.badge_var.set(t.badge_off)
            self.badge.configure(bg=OFF)
        else:
            if self.settings.enabled:
                self.badge_var.set(t.badge_on)
                self.badge.configure(bg=OK)
            else:
                self.badge_var.set(t.badge_off)
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
            self.agent.stop(join=True)
            self.agent.start(self.settings)
            self.status_var.set(self.strings.status_port_saved.format(port=port))

    def _on_agent_status(self, status: AgentStatus, _detail: str) -> None:
        def apply() -> None:
            if self._closing:
                return
            self._last_agent_status = status
            t = self.strings
            if status == AgentStatus.ACTIVE:
                self.status_var.set(t.status_active)
            elif status == AgentStatus.IDLE:
                self.status_var.set(
                    t.status_idle.format(seconds=self.settings.idle_poll_sec)
                )
            elif status == AgentStatus.STOPPING:
                self.status_var.set(t.status_stopping)
            else:
                self.status_var.set(
                    t.status_enabled_waiting
                    if self.settings.enabled
                    else t.status_disabled
                )
            self._refresh_badge_for_status(status)

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
            messagebox.showinfo(PRODUCT_NAME, self.strings.tray_need_enable)
            return
        self._ensure_tray()
        self.root.withdraw()

    def _ensure_tray(self) -> None:
        if self._tray is not None:
            return
        try:
            import pystray
        except ImportError:
            log.warning("pystray not available — tray disabled")
            return

        t = self.strings
        image = _load_brand_image(64)
        menu = pystray.Menu(
            pystray.MenuItem(t.tray_open, self._show_window, default=True),
            pystray.MenuItem(t.tray_disable, self._tray_disable),
            pystray.MenuItem(t.tray_quit, self._tray_quit),
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
        if self.settings.enabled:
            self.agent.stop(join=True)
        self._exit_clean()

    def _check_updates(self) -> None:
        if self._updating:
            return
        if not is_frozen():
            messagebox.showinfo(PRODUCT_NAME, self.strings.update_dev_only)
            return
        self._updating = True
        self.status_var.set(self.strings.update_checking)
        self.btn_update.configure(state="disabled")

        def work() -> None:
            try:
                release = fetch_latest_release()
                if not is_newer(release.version):
                    self.root.after(
                        0,
                        lambda: self._update_result_up_to_date(release.version),
                    )
                    return
                self.root.after(0, lambda: self._prompt_and_install(release))
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                self.root.after(0, lambda: self._update_failed(err))

        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _update_result_up_to_date(self, version: str) -> None:
        self._updating = False
        self.btn_update.configure(state="normal")
        messagebox.showinfo(
            PRODUCT_NAME,
            self.strings.update_up_to_date.format(version=version),
        )
        if self.settings.enabled:
            self.status_var.set(self.strings.status_enabled_waiting)
        else:
            self.status_var.set(self.strings.status_disabled)

    def _update_failed(self, error: str) -> None:
        self._updating = False
        self.btn_update.configure(state="normal")
        messagebox.showerror(
            PRODUCT_NAME,
            self.strings.update_failed.format(error=error),
        )

    def _prompt_and_install(self, release) -> None:
        t = self.strings
        ok = messagebox.askyesno(
            t.update_available_title,
            t.update_available_body.format(latest=release.version, current=__version__),
        )
        if not ok:
            self._updating = False
            self.btn_update.configure(state="normal")
            return

        self.status_var.set(t.update_downloading)

        def download() -> None:
            try:
                dest = data_root() / "LockOnBridge.zip"
                if release.download_url.lower().endswith(".exe"):
                    dest = data_root() / "LockOnBridge.exe.new"
                download_release_exe(release.download_url, dest)
                apply_update_and_restart(dest)

                def finish() -> None:
                    messagebox.showinfo(PRODUCT_NAME, t.update_restarting)
                    self._closing = True
                    self.agent.stop(join=True)
                    self._destroy_tray()
                    try:
                        self.root.destroy()
                    except tk.TclError:
                        pass
                    os._exit(0)

                self.root.after(0, finish)
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                self.root.after(0, lambda: self._update_failed(err))

        threading.Thread(target=download, name="update-download", daemon=True).start()

    def _uninstall(self) -> None:
        ok = messagebox.askyesno(PRODUCT_NAME, self.strings.uninstall_confirm)
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
