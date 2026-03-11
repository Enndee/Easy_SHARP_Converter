# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


project_dir = Path.cwd()
conda_root = Path.home() / "anaconda3" / "envs" / "sharp" / "Library" / "lib"
tcl_dir = conda_root / "tcl8.6"
tk_dir = conda_root / "tk8.6"

datas = []
if tcl_dir.exists():
    datas.append((str(tcl_dir), "tcl"))
if tk_dir.exists():
    datas.append((str(tk_dir), "tk"))

binaries = []
hiddenimports = [
    "plyfile",
    "numpy",
    "PIL",
    "PIL._tkinter_finder",
    "PIL.Image",
    "PIL.ImageOps",
    "PIL.ImageTk",
]

for package_name in ("plyfile", "PIL"):
    tmp_ret = collect_all(package_name)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]


a = Analysis(
    ["Easy_SHARP_GUI.py"],
    pathex=[str(project_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Easy_SHARP_Converter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
