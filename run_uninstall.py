"""PyInstaller entry for uninstall.exe — lives next to LockOnBridge.exe."""
from __future__ import annotations

import os
import sys


def _quiet(argv: list[str]) -> bool:
    flags = {"--quiet", "-q", "/S", "/s", "/silent", "--silent"}
    return any(a in flags for a in argv)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    quiet = _quiet(args)

    from lockon_bridge.autostart import full_uninstall
    from lockon_bridge.i18n import EN, UK
    from lockon_bridge.paths import PRODUCT_NAME
    from lockon_bridge.settings import load_settings

    confirm = EN.uninstall_confirm
    try:
        lang = (load_settings().language or "en").lower()
        if lang.startswith("uk") or lang.startswith("ua"):
            confirm = UK.uninstall_confirm
    except Exception:  # noqa: BLE001 — settings optional during uninstall
        pass

    if not quiet:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        ok = messagebox.askyesno(PRODUCT_NAME, confirm)
        try:
            root.destroy()
        except tk.TclError:
            pass
        if not ok:
            return 0

    full_uninstall()
    # Delayed folder delete runs after exit; force-quit so files unlock.
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
