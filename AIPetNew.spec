# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(SPECPATH)

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "assets"), "assets"),
        (str(project_root / "config.json"), "."),
    ],
    hiddenimports=[
        "openai",
        "memory.memory_candidate",
        "memory.memory_extractor",
        "memory.memory_retriever",
        "memory.memory_maintenance",
        "memory.memory_service",
        "memory.memory_store",
        "memory.memory_models",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Codex's development PATH contains Poppler ICU 78 binaries. PyInstaller can
# mistake them for Qt6Core dependencies and bundle an ABI-incompatible ICU,
# causing QtCore to fail with WinError 127. The supported Windows runtime uses
# the system ICU, matching the verified source and packaged execution paths.
_path_polluted_icu = {"icuuc.dll", "icudt78.dll"}
a.binaries = [
    entry
    for entry in a.binaries
    if Path(entry[0]).name.lower() not in _path_polluted_icu
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AIPetNew",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
