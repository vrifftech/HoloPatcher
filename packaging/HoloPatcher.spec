# -*- mode: python ; coding: utf-8 -*-
"""Build using this frontend and a separate local backend, never an old donor copy."""
import os
import sys
from pathlib import Path

root = Path(SPEC).resolve().parent.parent
backend = Path(os.environ["HOLOPATCHER_PYKOTOR_ROOT"]).expanduser().resolve()
mode = os.environ.get("FRONTEND_BUILD_MODE", "onedir").lower()
if mode not in {"onedir", "onefile"}:
    raise SystemExit("FRONTEND_BUILD_MODE must be onedir or onefile")
libraries = ['PyKotor', 'Utility']
sources = [root / "src"] + [backend / "Libraries" / lib / "src" for lib in libraries]
for source in sources:
    if not source.is_dir():
        raise SystemExit(f"Missing source directory: {source}")
    sys.path.insert(0, str(source))
# Resolve and verify the selected namespace paths before analysis.
from holopatcher.bootstrap import bootstrap_backend
bootstrap_backend()

hidden = ["ply.lex", "ply.yacc"]
for path in (root / "src" / "holopatcher").rglob("*.py"):
    relative = path.relative_to(root / "src")
    if any(part in {"__pycache__", "help", "kits"} for part in relative.parts[:-1]):
        continue
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    hidden.append(".".join(parts))
hidden.append("utility.tkinter.rte_editor")
# PyInstaller's normal hooks collect Tcl/Tk or Qt plugins and binary dependencies.
datas = [(str(root / "src/holopatcher/resources"), "holopatcher/resources")]
datas.append((str(root / "LICENSE"), "licenses/frontend"))
if (backend / "LICENSE").is_file():
    datas.append((str(backend / "LICENSE"), "licenses/backend"))
icon = root / "src/holopatcher/resources/icons/patcher_icon_v2.ico"
a = Analysis([str(root / "packaging/entry.py")], pathex=[str(p) for p in sources],
             binaries=[], datas=datas, hiddenimports=sorted(set(hidden)),
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=["PyQt6", "PySide2", "PySide6"], noarchive=False)
pyz = PYZ(a.pure)
options = dict(name="HoloPatcher", debug=False, bootloader_ignore_signals=False,
               strip=False, upx=False, console=True, icon=str(icon) if os.name == "nt" else None)
if mode == "onefile":
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **options)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **options)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="HoloPatcher")

if sys.platform == "darwin":
    app = BUNDLE(exe if mode == "onefile" else coll,
                 name="HoloPatcher.app",
                 bundle_identifier="org.openkotor.holopatcher",
                 info_plist={"CFBundleShortVersionString": "2.0.0",
                             "CFBundleVersion": "2.0.0",
                             "CFBundleGetInfoString": "HoloPatcher 2.0b",
                             "NSHighResolutionCapable": True})
