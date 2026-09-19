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
    firewall_rule_present,
    full_uninstall,
    prepare_enabled_runtime,
    register_uninstall_entry,
    unregister_autostart,
    _app_allow_present,
    _firewall_has_block_on_bridge,
)
from .i18n import EN, UK, Strings, strings_for
from .dpi import enable_windows_dpi_awareness, sync_tk_scaling
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
DANGER = "#b91c1c"
DANGER_BG = "#3f1515"

# Keep enough room for toggle, languages, port, primary actions, More menu,
# and the optional phone-access banner (UK strings wrap taller).
MIN_WINDOW_W = 520
MIN_WINDOW_H = 720
DEFAULT_WINDOW_W = 540
DEFAULT_WINDOW_H = 780
# Extra chrome so the last button is never flush against the bottom edge.
_WINDOW_CHROME_PAD = 28


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

        enable_windows_dpi_awareness()
        self.root = tk.Tk()
        sync_tk_scaling(self.root)
        self.root.title(f"{PRODUCT_NAME} {__version__}")
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_window)
        self._apply_window_icon()
        self._apply_window_size(initial=True)
        # Re-sync after the HWND exists (per-monitor DPI), then re-clamp size.
        self.root.after(50, self._after_dpi_ready)
        self._ocr_busy = False

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
        self._wt_code_by_label: dict[str, str] = {}
        self.wt_language_var = tk.StringVar(value="")
        self.ocr_backend_var = tk.StringVar(value="")
        self._ocr_backend_by_label: dict[str, str] = {}

        self._build_ui()
        self.agent.set_status_callback(self._on_agent_status)

        if self.settings.enabled:
            self._start_enabled(persist=False)
        else:
            self._set_ui_enabled(False)
            self.status_var.set(self.strings.status_disabled)

        self.root.after(400, self._refresh_phone_access_ui)
        self.root.after(5_000, self._poll_phone_access_loop)
        # Second fit after fonts/DPI finish measuring wrapped labels.
        self.root.after(200, lambda: self._apply_window_size(initial=False))

        if start_hidden and self.settings.enabled:
            self.root.withdraw()
            self._ensure_tray()
        elif start_hidden and not self.settings.enabled:
            self.root.after(50, self._exit_clean)

    def run(self) -> int:
        self.root.mainloop()
        return 0

    def _after_dpi_ready(self) -> None:
        sync_tk_scaling(self.root)
        self._apply_window_size(initial=False)

    def _content_req_size(self) -> tuple[int, int]:
        """Required width/height for all packed root children (post-layout)."""
        self.root.update_idletasks()
        width = 0
        height = 0
        for child in self.root.winfo_children():
            try:
                width = max(width, int(child.winfo_reqwidth()))
                height += int(child.winfo_reqheight())
                info = child.pack_info()
            except tk.TclError:
                continue
            padx = info.get("padx", 0)
            pady = info.get("pady", 0)
            if isinstance(padx, (tuple, list)):
                width += int(padx[0]) + int(padx[1])
            else:
                width += int(padx) * 2
            if isinstance(pady, (tuple, list)):
                height += int(pady[0]) + int(pady[1])
            else:
                height += int(pady) * 2
        # Fallback when children are not mapped yet.
        if width <= 1:
            width = DEFAULT_WINDOW_W
        if height <= 1:
            height = DEFAULT_WINDOW_H
        return width, height + _WINDOW_CHROME_PAD

    def _apply_window_size(self, *, initial: bool) -> None:
        """
        Size the window so every control fits.

        Fixed geometry alone fails on HiDPI / after the phone banner appears —
        measure packed content and grow (within the screen work area).
        """
        self.root.update_idletasks()
        req_w, req_h = self._content_req_size()
        need_w = max(MIN_WINDOW_W, req_w, DEFAULT_WINDOW_W if initial else 0)
        need_h = max(MIN_WINDOW_H, req_h, DEFAULT_WINDOW_H if initial else 0)

        try:
            screen_w = int(self.root.winfo_screenwidth())
            screen_h = int(self.root.winfo_screenheight())
        except tk.TclError:
            screen_w, screen_h = 1920, 1080
        # Leave room for taskbar / window chrome.
        max_w = max(MIN_WINDOW_W, screen_w - 48)
        max_h = max(MIN_WINDOW_H, screen_h - 96)
        final_w = min(need_w, max_w)
        final_h = min(need_h, max_h)

        self.root.minsize(min(MIN_WINDOW_W, final_w), min(MIN_WINDOW_H, final_h))
        try:
            cur_w = int(self.root.winfo_width())
            cur_h = int(self.root.winfo_height())
        except tk.TclError:
            cur_w, cur_h = 1, 1

        if initial or cur_w < 50 or cur_h < 50:
            self.root.geometry(f"{final_w}x{final_h}")
            return
        # Grow when content needs more room; never shrink below the fitted size
        # if the user already enlarged the window.
        target_w = max(cur_w, final_w) if cur_w >= MIN_WINDOW_W else final_w
        target_h = max(cur_h, final_h) if cur_h >= MIN_WINDOW_H else final_h
        # But if content grew past the current client area, always expand.
        if cur_w < final_w or cur_h < final_h:
            target_w = max(cur_w, final_w)
            target_h = max(cur_h, final_h)
        if target_w != cur_w or target_h != cur_h:
            self.root.geometry(f"{target_w}x{target_h}")

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
        pad = {"padx": 20, "pady": 6}

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
        card.pack(fill="x", padx=20, pady=10)

        toggle_row = tk.Frame(card, bg=PANEL)
        toggle_row.pack(fill="x", padx=16, pady=(14, 6))
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
            wraplength=MIN_WINDOW_W - 80,
            justify="left",
        )
        self.status_label.pack(anchor="w", padx=16, pady=(0, 6))

        self.badge = tk.Label(
            card,
            textvariable=self.badge_var,
            font=("Segoe UI Semibold", 11),
            fg="#ffffff",
            bg=OFF,
            padx=10,
            pady=3,
        )
        self.badge.pack(anchor="w", padx=16, pady=(0, 8))

        self.phone_banner = tk.Label(
            card,
            text="",
            font=("Segoe UI", 9),
            fg="#fecaca",
            bg=DANGER_BG,
            justify="left",
            wraplength=MIN_WINDOW_W - 90,
            padx=10,
            pady=8,
        )
        # Packed/unpacked by _refresh_phone_access_ui

        self.phone_url_label = tk.Label(
            card,
            text="",
            font=("Segoe UI", 9),
            fg=MUTED,
            bg=PANEL,
            justify="left",
            wraplength=MIN_WINDOW_W - 90,
        )
        self.phone_url_label.pack(anchor="w", padx=16, pady=(0, 12))

        self.tip_label = tk.Label(
            self.root,
            text=t.tip,
            font=("Segoe UI", 9),
            fg=MUTED,
            bg=BG,
            justify="left",
            wraplength=390,
        )
        self.tip_label.pack(anchor="w", padx=20, pady=(0, 4))

        port_row = tk.Frame(self.root, bg=BG)
        port_row.pack(fill="x", padx=20, pady=2)
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
        lang_row.pack(fill="x", padx=20, pady=2)
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
            width=18,
        )
        self.language_combo.pack(side="right")
        self.language_combo.bind("<<ComboboxSelected>>", self._on_language_chosen)

        from .wt_languages import WT_LANGUAGES

        self._wt_code_by_label = {}
        wt_labels: list[str] = []
        for item in WT_LANGUAGES:
            label = item.label_uk if self.settings.language == "uk" else item.label_en
            self._wt_code_by_label[label] = item.code
            wt_labels.append(label)
        current_wt = next(
            (lbl for lbl, code in self._wt_code_by_label.items() if code == self.settings.wt_ui_language),
            wt_labels[0],
        )
        self.wt_language_var.set(current_wt)
        wt_row = tk.Frame(self.root, bg=BG)
        wt_row.pack(fill="x", padx=20, pady=2)
        tk.Label(wt_row, text=t.wt_language, font=("Segoe UI", 10), fg=FG, bg=BG).pack(side="left")
        self.wt_language_combo = ttk.Combobox(
            wt_row,
            textvariable=self.wt_language_var,
            values=tuple(wt_labels),
            state="readonly",
            width=18,
        )
        self.wt_language_combo.pack(side="right")
        self.wt_language_combo.bind("<<ComboboxSelected>>", self._on_wt_language_chosen)

        # Primary actions stay visible; the rest go into a dropdown.
        btns = tk.Frame(self.root, bg=BG)
        btns.pack(fill="x", padx=20, pady=(8, 4))
        self.btn_test_ocr = self._btn(btns, t.test_ocr, self._test_ocr)
        self.btn_test_ocr.pack(fill="x", pady=3)

        more_row = tk.Frame(btns, bg=BG)
        more_row.pack(fill="x", pady=3)
        self.btn_tray = self._btn(more_row, t.hide_to_tray, self._hide_to_tray)
        self.btn_tray.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.btn_more = self._menubutton(more_row, t.menu_more)
        self.btn_more.pack(side="right", fill="x", expand=True, padx=(4, 0))

        more_menu = tk.Menu(
            self.btn_more,
            tearoff=0,
            bg=PANEL,
            fg=FG,
            activebackground=ACCENT,
            activeforeground="#ffffff",
            bd=0,
            font=("Segoe UI", 10),
        )
        more_menu.add_command(label=t.replay_log, command=self._replay_last_ocr)
        more_menu.add_command(label=t.setup_tesseract, command=self._setup_tesseract)
        more_menu.add_command(label=t.ocr_packs, command=self._ocr_packs_clicked)
        ocr_sub = tk.Menu(
            more_menu,
            tearoff=0,
            bg=PANEL,
            fg=FG,
            activebackground=ACCENT,
            activeforeground="#ffffff",
            bd=0,
            font=("Segoe UI", 10),
        )
        self._ocr_backend_by_label = {
            t.ocr_backend_auto: "auto",
            t.ocr_backend_windows: "windows",
            t.ocr_backend_tesseract: "tesseract",
        }
        for label, code in self._ocr_backend_by_label.items():
            ocr_sub.add_radiobutton(
                label=label,
                variable=self.ocr_backend_var,
                value=label,
                command=self._on_ocr_backend_chosen,
            )
        backend_label = next(
            (lbl for lbl, code in self._ocr_backend_by_label.items() if code == self.settings.ocr_backend),
            t.ocr_backend_auto,
        )
        self.ocr_backend_var.set(backend_label)
        more_menu.add_cascade(label=t.ocr_backend, menu=ocr_sub)
        more_menu.add_command(label=t.open_logs, command=self._open_logs)
        more_menu.add_command(label=t.firewall_menu, command=self._allow_phone_access)
        more_menu.add_command(label=t.check_updates, command=self._check_updates)
        more_menu.add_separator()
        more_menu.add_command(label=t.uninstall, command=self._uninstall)
        self.btn_more.configure(menu=more_menu)
        self._more_menu = more_menu

        self.btn_quit = self._btn(btns, t.quit, self._quit_keep_enabled)
        self.btn_quit.pack(fill="x", pady=(8, 3))

        try:
            ttk.Style().theme_use("clam")
        except tk.TclError:
            pass

        self._apply_window_size(initial=False)
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

    def _menubutton(self, parent: tk.Widget, text: str) -> tk.Menubutton:
        return tk.Menubutton(
            parent,
            text=text,
            font=("Segoe UI", 10),
            fg=FG,
            bg=PANEL,
            activebackground="#2f343c",
            activeforeground="#ffffff",
            relief="flat",
            padx=12,
            pady=8,
            cursor="hand2",
            direction="below",
            indicatoron=False,
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
        self._apply_window_size(initial=False)
        self._refresh_badge_for_status(self._last_agent_status)

    def _on_wt_language_chosen(self, _event=None) -> None:
        label = self.wt_language_var.get()
        code = self._wt_code_by_label.get(label, "en")
        if code == self.settings.wt_ui_language:
            return
        self.settings = update_settings(wt_ui_language=code)

    def _on_ocr_backend_chosen(self, _event=None) -> None:
        label = self.ocr_backend_var.get()
        code = self._ocr_backend_by_label.get(label, "auto")
        if code == self.settings.ocr_backend:
            return
        self.settings = update_settings(ocr_backend=code)

    def _setup_tesseract(self) -> None:
        from .ocr_backends import (
            describe_ocr_status,
            download_tessdata,
            install_tesseract_via_winget,
            missing_tessdata_for_wt,
            tesseract_available,
        )
        from .wt_languages import get_wt_language

        t = self.strings
        wt = self.settings.wt_ui_language or "uk"
        if not tesseract_available():
            if not messagebox.askyesno(PRODUCT_NAME, t.setup_tesseract_no_exe):
                return
            messagebox.showinfo(PRODUCT_NAME, t.setup_tesseract_installing)

            def install_work() -> None:
                ok, detail = install_tesseract_via_winget()
                self.root.after(
                    0,
                    lambda: messagebox.showinfo(
                        PRODUCT_NAME,
                        (t.setup_tesseract_done if ok else t.setup_tesseract_failed).format(
                            detail=detail
                        ),
                    ),
                )

            threading.Thread(target=install_work, name="tesseract-winget", daemon=True).start()
            return
        status = describe_ocr_status(wt)
        missing = missing_tessdata_for_wt(wt)
        lang = get_wt_language(wt)
        if not missing:
            messagebox.showinfo(
                PRODUCT_NAME,
                t.setup_tesseract_done.format(detail=status),
            )
            return
        body = t.setup_tesseract_body.format(status=status, lang=lang.code)
        if not messagebox.askyesno(PRODUCT_NAME, body):
            return

        def work() -> None:
            ok, detail = download_tessdata(missing)
            self.root.after(
                0,
                lambda: messagebox.showinfo(
                    PRODUCT_NAME,
                    (t.setup_tesseract_done if ok else t.setup_tesseract_failed).format(
                        detail=detail
                    ),
                ),
            )

        threading.Thread(target=work, name="tessdata-dl", daemon=True).start()

    def _maybe_offer_tesseract(self) -> None:
        from .ocr_backends import tesseract_available, tesseract_recommended_for

        wt = self.settings.wt_ui_language or "uk"
        if tesseract_available() or not tesseract_recommended_for(wt):
            return
        t = self.strings
        lang = wt.upper() if len(wt) <= 3 else wt
        if not messagebox.askyesno(
            t.setup_tesseract_offer_title,
            t.setup_tesseract_offer_body.format(lang=lang),
        ):
            return
        self._setup_tesseract()

    def _on_toggle(self) -> None:
        if bool(self.enabled_var.get()):
            self._start_enabled(persist=True)
        else:
            self._disable_completely()

    def _start_enabled(self, *, persist: bool) -> None:
        port = self._read_port()
        self.settings = update_settings(enabled=True, port=port)
        # Install/autostart first; firewall elevation only after a clear Yes/No.
        prepare_enabled_runtime(port=port, allow_firewall_elevate=False)
        self._set_ui_enabled(True)
        self.status_var.set(self.strings.status_enabled_waiting)
        self.agent.start(self.settings)
        self._ensure_tray()
        self.root.after(200, lambda: self._ensure_phone_access(interactive=True))
        # Offer official Microsoft OCR packs once (or when still missing).
        self.root.after(400, lambda: self._maybe_prompt_ocr_packs(force=False))

    def _allow_phone_access(self) -> None:
        self._ensure_phone_access(interactive=True, force_prompt=True)

    def _phone_access_ok(self, port: int | None = None) -> bool:
        p = int(port if port is not None else self._read_port())
        return (
            firewall_rule_present(p)
            and _app_allow_present()
            and not _firewall_has_block_on_bridge()
        )

    @staticmethod
    def _lan_ipv4() -> str | None:
        import socket

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            sock.close()
            return ip
        except OSError:
            return None

    def _phone_test_url(self) -> str:
        port = self._read_port()
        ip = self._lan_ipv4() or "PC_IP"
        return f"http://{ip}:{port}/v1/health"

    def _refresh_phone_access_ui(self) -> None:
        t = self.strings
        enabled = bool(self.enabled_var.get())
        port = self._read_port()
        url = self._phone_test_url()
        if not enabled:
            try:
                self.phone_banner.pack_forget()
            except tk.TclError:
                pass
            self.phone_url_label.configure(text="")
            self.status_label.configure(fg=MUTED)
            self.root.after_idle(lambda: self._apply_window_size(initial=False))
            return

        ok = self._phone_access_ok(port)
        if ok:
            try:
                self.phone_banner.pack_forget()
            except tk.TclError:
                pass
            self.phone_url_label.configure(
                text=t.phone_access_ok.format(url=url),
                fg=OK,
            )
            self.status_label.configure(fg=MUTED)
        else:
            self.status_var.set(t.status_firewall_needed)
            self.status_label.configure(fg="#fca5a5")
            self.phone_banner.configure(
                text=t.phone_access_banner.format(port=port),
                bg=DANGER_BG,
                fg="#fecaca",
            )
            try:
                self.phone_banner.pack_forget()
            except tk.TclError:
                pass
            self.phone_banner.pack(fill="x", padx=16, pady=(0, 8), before=self.phone_url_label)
            self.phone_url_label.configure(
                text=t.phone_test_hint.format(url=url),
                fg="#fca5a5",
            )
        self.root.after_idle(lambda: self._apply_window_size(initial=False))

    def _poll_phone_access_loop(self) -> None:
        if self._closing:
            return
        try:
            self._refresh_phone_access_ui()
        except Exception:  # noqa: BLE001
            pass
        self.root.after(5_000, self._poll_phone_access_loop)

    def _ensure_phone_access(self, *, interactive: bool, force_prompt: bool = False) -> None:
        port = self._read_port()
        # Port Allow alone is not enough — Defender Block on the exe still drops :8112.
        already_ok = self._phone_access_ok(port)
        if already_ok:
            if force_prompt:
                messagebox.showinfo(
                    PRODUCT_NAME,
                    self.strings.firewall_ok.format(port=port),
                )
            self._refresh_phone_access_ui()
            return
        if not interactive:
            self._refresh_phone_access_ui()
            return
        t = self.strings
        if not messagebox.askyesno(
            t.firewall_prompt_title,
            t.firewall_prompt_body.format(port=port),
        ):
            self.status_var.set(t.status_firewall_needed)
            self._refresh_phone_access_ui()
            return
        ok = ensure_firewall_rule(port, allow_elevate=True)
        if ok:
            self.status_var.set(t.status_enabled_waiting)
            messagebox.showinfo(PRODUCT_NAME, t.firewall_ok.format(port=port))
        else:
            self.status_var.set(t.status_firewall_needed)
            messagebox.showwarning(PRODUCT_NAME, t.firewall_denied)
        self._refresh_phone_access_ui()

    def _ocr_packs_clicked(self) -> None:
        self._maybe_prompt_ocr_packs(force=True)

    def _maybe_prompt_ocr_packs(self, *, force: bool) -> None:
        from .ocr_setup import OCR_TAG_LABELS, advise_ocr_packs, install_ocr_packs

        # Prefer Bridge UI language as a hint for WT UI (user can change later).
        wt_lang = self.settings.wt_ui_language or self.settings.language or "uk"
        advice = advise_ocr_packs(wt_lang)
        t = self.strings

        def label_list(tags: tuple[str, ...]) -> str:
            lines = []
            for tag in tags:
                lines.append(f"• {OCR_TAG_LABELS.get(tag, tag)} ({tag})")
            return "\n".join(lines) if lines else "—"

        if not advice.missing_tags:
            if force:
                messagebox.showinfo(
                    PRODUCT_NAME,
                    t.ocr_packs_ok.format(packs=label_list(advice.recommended_tags)),
                )
            update_settings(ocr_setup_done=True, wt_ui_language=wt_lang)
            if not force:
                self.root.after(200, self._maybe_offer_tesseract)
            return

        if not force and self.settings.ocr_setup_done:
            return

        note = t.ocr_packs_uk_note if advice.ui_language == "uk" else (advice.note + "\n\n" if advice.note else "")
        body = t.ocr_packs_missing_body.format(
            lang=advice.ui_language,
            packs=label_list(advice.recommended_tags),
            missing=label_list(advice.missing_tags),
            note=note,
        )
        # askyesno: Yes = install, No = skip
        install = messagebox.askyesno(t.ocr_packs_missing_title, body)
        update_settings(ocr_setup_done=True, wt_ui_language=wt_lang)
        if not install:
            if not force:
                self.root.after(200, self._maybe_offer_tesseract)
            return

        messagebox.showinfo(PRODUCT_NAME, t.ocr_packs_installing)

        def work() -> None:
            ok, detail = install_ocr_packs(list(advice.missing_tags))
            self.root.after(0, lambda: self._ocr_install_finished(ok, detail, offer_tess=not force))

        threading.Thread(target=work, name="ocr-pack-install", daemon=True).start()

    def _ocr_install_finished(self, ok: bool, detail: str, *, offer_tess: bool = False) -> None:
        t = self.strings
        if ok:
            messagebox.showinfo(PRODUCT_NAME, t.ocr_packs_done.format(detail=detail or ""))
        else:
            messagebox.showerror(PRODUCT_NAME, t.ocr_packs_failed.format(detail=detail or ""))
        if offer_tess:
            self.root.after(200, self._maybe_offer_tesseract)

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
            self._ensure_phone_access(interactive=True)
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
            self._refresh_phone_access_ui()

        try:
            self.root.after(0, apply)
        except tk.TclError:
            pass

    def _test_ocr(self) -> None:
        if getattr(self, "_ocr_busy", False):
            return
        self._ocr_busy = True
        countdown = 5
        # Fully hide Bridge so its own UI is never OCR'd.
        try:
            self.root.withdraw()
        except tk.TclError:
            try:
                self.root.iconify()
            except tk.TclError:
                pass

        def tick(left: int) -> None:
            if left > 0:
                # Status is hidden with the window; still advance the timer.
                self.root.after(1000, lambda: tick(left - 1))
                return

            def work() -> None:
                try:
                    from .selftest import ocr_once

                    text, report, _dump = ocr_once(save_dump=True)
                    self.root.after(0, lambda: self._show_ocr_result(text, report, None))
                except Exception as exc:  # noqa: BLE001
                    self.root.after(0, lambda: self._show_ocr_result("", None, str(exc)))

            threading.Thread(target=work, name="ocr-test", daemon=True).start()

        tick(countdown)

    def _show_ocr_result(self, text: str, report, error: str | None) -> None:
        self._ocr_busy = False
        try:
            self.root.deiconify()
            self.root.lift()
        except tk.TclError:
            pass
        t = self.strings
        if error:
            messagebox.showerror(PRODUCT_NAME, t.test_ocr_error.format(error=error))
            return
        if report is None:
            from .ocr_parse import summarize_ocr_text

            messagebox.showwarning(
                PRODUCT_NAME,
                t.test_ocr_fail.format(preview=summarize_ocr_text(text, limit=240)),
            )
            return
        messagebox.showinfo(
            PRODUCT_NAME,
            t.test_ocr_ok.format(
                rp=report.research_points,
                sl=report.silver_lions,
                outcome=self._localize_outcome(report.outcome),
                conf=report.confidence,
            ),
        )

    def _localize_outcome(self, outcome: str) -> str:
        t = self.strings
        key = (outcome or "").strip().lower()
        if key == "victory":
            return t.outcome_victory
        if key == "defeat":
            return t.outcome_defeat
        return t.outcome_undecided

    def _replay_last_ocr(self) -> None:
        from .ocr_parse import parse_rewards_from_ocr_text

        path = log_dir() / "last_ocr.txt"
        t = self.strings
        if not path.is_file():
            messagebox.showwarning(PRODUCT_NAME, t.replay_fail)
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            messagebox.showwarning(PRODUCT_NAME, t.replay_fail)
            return
        report = parse_rewards_from_ocr_text(text)
        if report is None:
            messagebox.showwarning(PRODUCT_NAME, t.replay_fail)
            return
        messagebox.showinfo(
            PRODUCT_NAME,
            t.replay_ok.format(rp=report.research_points, sl=report.silver_lions),
        )

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

    def _set_update_controls_busy(self, busy: bool) -> None:
        """Update used to disable a dedicated button; it now lives in More ▾."""
        self._updating = busy

    def _check_updates(self) -> None:
        if self._updating:
            return
        if not is_frozen():
            messagebox.showinfo(PRODUCT_NAME, self.strings.update_dev_only)
            return
        self._set_update_controls_busy(True)
        self.status_var.set(self.strings.update_checking)

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
                log.exception("update check failed")
                self.root.after(0, lambda: self._update_failed(err))

        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _update_result_up_to_date(self, version: str) -> None:
        self._set_update_controls_busy(False)
        messagebox.showinfo(
            PRODUCT_NAME,
            self.strings.update_up_to_date.format(version=version),
        )
        if self.settings.enabled:
            self.status_var.set(self.strings.status_enabled_waiting)
        else:
            self.status_var.set(self.strings.status_disabled)

    def _update_failed(self, error: str) -> None:
        self._set_update_controls_busy(False)
        if self.settings.enabled:
            self.status_var.set(self.strings.status_enabled_waiting)
        else:
            self.status_var.set(self.strings.status_disabled)
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
            self._set_update_controls_busy(False)
            if self.settings.enabled:
                self.status_var.set(self.strings.status_enabled_waiting)
            else:
                self.status_var.set(self.strings.status_disabled)
            return

        self.status_var.set(t.update_downloading)

        def download() -> None:
            try:
                dest = data_root() / "LockOnBridge.zip"
                if release.download_url.lower().endswith(".exe"):
                    dest = data_root() / "LockOnBridge.exe.new"
                download_release_exe(release.download_url, dest)

                def finish() -> None:
                    # Stop agent/tray first so files under app\ unlock before replace.
                    self._closing = True
                    try:
                        self.agent.stop(join=True)
                    except Exception:  # noqa: BLE001
                        pass
                    self._destroy_tray()
                    try:
                        apply_update_and_restart(dest)
                    except Exception as exc:  # noqa: BLE001
                        self._closing = False
                        self._update_failed(str(exc))
                        return
                    messagebox.showinfo(PRODUCT_NAME, t.update_restarting)
                    try:
                        self.root.destroy()
                    except tk.TclError:
                        pass
                    os._exit(0)

                self.root.after(0, finish)
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                log.exception("update download/install failed")
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
