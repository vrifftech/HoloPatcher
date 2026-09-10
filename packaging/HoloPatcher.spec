# -*- mode: python ; coding: utf-8 -*-
"""Build using this frontend and a separate local backend, never an old donor copy."""
import os
import sys
import PyInstaller
from pathlib import Path

root = Path(SPEC).resolve().parent.parent

# Spec builds encode --onefile/--noupx HERE; do not pass makespec flags
# alongside a .spec filename. Refuse stock/unverified PyInstaller, even locally.
sys.path.insert(0, str(root / "packaging"))
from build_policy import require_onefile, verify_pyinstaller
require_onefile()
verify_pyinstaller(Path(PyInstaller.__file__).resolve().parent, PyInstaller.PLATFORM, PyInstaller.__version__)
backend = Path(os.environ["HOLOPATCHER_PYKOTOR_ROOT"]).expanduser().resolve()
hook_dir = backend / "Libraries/PyKotor/src/pykotor/__pyinstaller"
if not (hook_dir / "hook-pykotor.resource.formats.ncs.compiler.py").is_file():
    raise SystemExit("Apply the PyKotor packaging patch and select that backend commit first")
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
for library in libraries:
    license_path = backend / "Libraries" / library / "LICENSE"
    if license_path.is_file():
        datas.append((str(license_path), f"licenses/{library}"))
icon = root / "src/holopatcher/resources/icons/patcher_icon_v2.ico"
a = Analysis([str(root / "packaging/entry.py")], pathex=[str(p) for p in sources],
             binaries=[], datas=datas, hiddenimports=sorted(set(hidden)),
             hookspath=[str(hook_dir)], hooksconfig={}, runtime_hooks=[],
             excludes=["PyQt6", "PySide2", "PySide6"], noarchive=False)
pyz = PYZ(a.pure)
options = dict(name="HoloPatcher", debug=False, bootloader_ignore_signals=False,
               strip=False, upx=False, console=True, icon=str(icon) if os.name == "nt" else None)
# This is the --onefile --noupx equivalent for a checked-in spec:
# native libraries/data are inside EXE; there is no COLLECT/onedir target.
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          exclude_binaries=False, append_pkg=True, **options)

if sys.platform == "darwin":
    app = BUNDLE(exe,
                 icon=str(root / "src/holopatcher/resources/icons/patcher_icon_v2.icns"),
                 name="HoloPatcher.app",
                 bundle_identifier="org.openkotor.holopatcher",
                 info_plist={"LSBackgroundOnly": False,
                             "LSMinimumSystemVersion": os.environ.get("MACOSX_DEPLOYMENT_TARGET") or "15.0",
                             "CFBundleShortVersionString": "2.0.0",
                             "CFBundleVersion": "2.0.0",
                             "CFBundleGetInfoString": "HoloPatcher 2.0b",
                             "NSHighResolutionCapable": True})
