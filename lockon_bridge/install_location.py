"""First-run install folder picker — keeps the bulky tree off C: when possible."""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .i18n import detect_system_language
from .paths import (
    PRODUCT_NAME,
    cleanup_obsolete_localappdata_junk,
    data_root_configured,
    is_frozen,
    legacy_install_present,
    running_install_layout_root,
    suggest_install_root,
    write_data_root,
)

log = logging.getLogger("lockon_bridge")

_COPY = {
    "en": {
        "title": "Choose install folder",
        "body": (
            "Where should LockOn Bridge keep its files?\n\n"
            "The app copy, logs and OCR data can be large — pick a drive with free space "
            "(D: is recommended when available). Almost nothing will stay on C:."
        ),
        "path": "Folder",
        "browse": "Browse…",
        "install": "Install",
        "cancel": "Cancel",
        "bad_path": "Please choose a valid folder path.",
        "create_fail": "Could not create that folder:\n{error}",
    },
    "uk": {
        "title": "Оберіть папку встановлення",
        "body": (
            "Куди зберегти файли LockOn Bridge?\n\n"
            "Копія програми, логи та OCR можуть займати багато місця — оберіть диск "
            "з вільним місцем (рекомендовано D:, якщо є). На C: майже нічого не лишиться."
        ),
        "path": "Папка",
        "browse": "Огляд…",
        "install": "Встановити",
        "cancel": "Скасувати",
        "bad_path": "Вкажіть коректний шлях до папки.",
        "create_fail": "Не вдалося створити цю папку:\n{error}",
    },
    "ru": {
        "title": "Выберите папку установки",
        "body": (
            "Куда сохранить файлы LockOn Bridge?\n\n"
            "Копия программы, логи и OCR могут занимать много места — выберите диск "
            "со свободным местом (рекомендуется D:, если есть). На C: почти ничего не останется."
        ),
        "path": "Папка",
        "browse": "Обзор…",
        "install": "Установить",
        "cancel": "Отмена",
        "bad_path": "Укажите корректный путь к папке.",
        "create_fail": "Не удалось создать эту папку:\n{error}",
    },
}


def _strings() -> dict[str, str]:
    lang = detect_system_language()
    return _COPY.get(lang, _COPY["en"])


def _seed_existing_root() -> Path | None:
    layout = running_install_layout_root()
    if layout is not None:
        return write_data_root(layout)
    if legacy_install_present():
        from .paths import default_local_appdata_root

        return write_data_root(default_local_appdata_root())
    return None


def _ask_install_folder() -> Path | None:
    text = _strings()
    result: dict[str, Path | None] = {"path": None}

    root = tk.Tk()
    root.title(f"{PRODUCT_NAME} — {text['title']}")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass

    frame = ttk.Frame(root, padding=16)
    frame.grid(row=0, column=0, sticky="nsew")

    ttk.Label(frame, text=text["body"], wraplength=420, justify="left").grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 12)
    )

    ttk.Label(frame, text=text["path"]).grid(row=1, column=0, sticky="w")
    path_var = tk.StringVar(value=str(suggest_install_root()))
    entry = ttk.Entry(frame, textvariable=path_var, width=48)
    entry.grid(row=1, column=1, sticky="ew", padx=(8, 8))

    def browse() -> None:
        chosen = filedialog.askdirectory(
            parent=root,
            title=text["title"],
            initialdir=path_var.get() or str(Path.home()),
        )
        if chosen:
            path_var.set(chosen)

    ttk.Button(frame, text=text["browse"], command=browse).grid(row=1, column=2)

    def on_install() -> None:
        raw = path_var.get().strip().strip('"')
        if not raw:
            messagebox.showerror(PRODUCT_NAME, text["bad_path"], parent=root)
            return
        target = Path(raw)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(
                PRODUCT_NAME,
                text["create_fail"].format(error=exc),
                parent=root,
            )
            return
        result["path"] = target
        root.destroy()

    def on_cancel() -> None:
        result["path"] = None
        root.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=2, column=0, columnspan=3, sticky="e", pady=(16, 0))
    ttk.Button(buttons, text=text["cancel"], command=on_cancel).pack(side="right", padx=(8, 0))
    ttk.Button(buttons, text=text["install"], command=on_install).pack(side="right")

    root.protocol("WM_DELETE_WINDOW", on_cancel)
    entry.focus_set()
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    x = max(0, (root.winfo_screenwidth() - w) // 2)
    y = max(0, (root.winfo_screenheight() - h) // 3)
    root.geometry(f"+{x}+{y}")
    root.mainloop()
    return result["path"]


def ensure_data_root_ready(*, interactive: bool = True) -> bool:
    """
    Resolve and persist the data/install root.

    Returns False only when an interactive first-run dialog is cancelled.
    """
    cleanup_obsolete_localappdata_junk()

    if data_root_configured():
        seeded = _seed_existing_root()
        if seeded is not None:
            log.info("Data root → %s", seeded)
        return True

    if not is_frozen():
        # Dev / python -m: stay on the small LocalAppData default unless overridden.
        from .paths import default_local_appdata_root

        write_data_root(default_local_appdata_root())
        return True

    if interactive:
        chosen = _ask_install_folder()
        if chosen is None:
            return False
        write_data_root(chosen)
        log.info("User chose install root → %s", chosen)
        return True

    # Autostart / silent: pick the suggested non-C path without a dialog.
    root = write_data_root(suggest_install_root())
    log.info("Silent install root → %s", root)
    return True
