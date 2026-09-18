# -*- mode: python ; coding: utf-8 -*-
# onedir + no UPX: unsigned one-file/UPX builds are frequently false-positive'd by Defender.
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = [
    "pystray._win32",
    "PIL._tkinter_finder",
]
for pkg in (
    "winrt",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.globalization",
    "winrt.windows.graphics",
    "winrt.windows.graphics.imaging",
    "winrt.windows.media.ocr",
    "winrt.windows.storage",
    "winrt.windows.storage.streams",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        hiddenimports += collect_submodules(pkg)

a = Analysis(
    ["run_bridge.py"],
    pathex=[],
    binaries=binaries,
    datas=datas + [("assets/lockon_bridge.png", "assets"), ("assets/lockon_bridge.ico", "assets")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LockOnBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/lockon_bridge.ico",
    version="file_version_info.txt",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LockOnBridge",
)
