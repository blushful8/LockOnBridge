# -*- mode: python ; coding: utf-8 -*-
# onedir + no UPX: unsigned one-file/UPX builds are frequently false-positive'd by Defender.
# Two EXEs share one _internal folder: LockOnBridge.exe + uninstall.exe
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = [
    "pystray._win32",
    "PIL._tkinter_finder",
    "pytesseract",
    "numpy",
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
    datas=datas + [
        ("assets/lockon_bridge.png", "assets"),
        ("assets/lockon_bridge.ico", "assets"),
        ("lockon_bridge/roi_calibrated.json", "lockon_bridge"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["rapidocr_onnxruntime", "onnxruntime", "onnxruntime.capi"],
    noarchive=False,
)

u = Analysis(
    ["run_uninstall.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

MERGE((a, "LockOnBridge", "LockOnBridge"), (u, "uninstall", "uninstall"))

pyz = PYZ(a.pure)
pyz_u = PYZ(u.pure)

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
    manifest="assets/LockOnBridge.manifest",
)

uninstall_exe = EXE(
    pyz_u,
    u.scripts,
    [],
    exclude_binaries=True,
    name="uninstall",
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
    version="uninstall_version_info.txt",
)

coll = COLLECT(
    exe,
    uninstall_exe,
    a.binaries,
    a.datas,
    u.binaries,
    u.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LockOnBridge",
)
