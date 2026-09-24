# -*- mode: python ; coding: utf-8 -*-
"""Build using this frontend and a separate local backend, never an old donor copy."""
import os
import sys
import PyInstaller
from pathlib import Path

root = Path(SPEC).resolve().parent.parent
build_mode = os.environ.get("FRONTEND_BUILD_MODE", "onefile").strip().lower()
if build_mode not in {"onefile", "onedir"}:
    raise SystemExit(f"Unsupported FRONTEND_BUILD_MODE={build_mode!r}")
if build_mode == "onedir" and not (sys.platform.startswith("linux") or sys.platform == "darwin"):
    raise SystemExit("The onedir build is supported only for Linux AppImage and macOS app payloads")

# Spec builds encode the distribution mode and no-UPX policy HERE. Refuse
# stock/unverified PyInstaller, even locally.
sys.path.insert(0, str(root / "packaging"))
from build_policy import require_build_mode, verify_pyinstaller

require_build_mode(build_mode)
verify_pyinstaller(Path(PyInstaller.__file__).resolve().parent, PyInstaller.PLATFORM, PyInstaller.__version__)
backend = Path(os.environ["HOLOPATCHER_PYKOTOR_ROOT"]).expanduser().resolve()
hook_dir = backend / "Libraries/PyKotor/src/pykotor/__pyinstaller"
if not (hook_dir / "hook-pykotor.resource.formats.ncs.compiler.py").is_file():
    raise SystemExit("Apply the PyKotor packaging patch and select that backend commit first")
libraries = ["PyKotor", "Utility"]
sources = [root / "src"] + [backend / "Libraries" / lib / "src" for lib in libraries]
for source in sources:
    if not source.is_dir():
        raise SystemExit(f"Missing source directory: {source}")
    sys.path.insert(0, str(source))

# Resolve and verify the selected namespace paths before analysis.
from holopatcher.bootstrap import bootstrap_backend

bootstrap_backend()

# Normal import analysis discovers the application packages. Keep only modules
# that are imported dynamically or that PLY addresses by name.
hidden = ["ply.lex", "ply.yacc", "utility.tkinter.rte_editor"]
runtime_icon = root / "src/holopatcher/resources/icons/patcher_icon_runtime.png"
datas = [(str(runtime_icon), "holopatcher/resources/icons")]
datas.append((str(root / "LICENSE"), "licenses/frontend"))
if (backend / "LICENSE").is_file():
    datas.append((str(backend / "LICENSE"), "licenses/backend"))
for library in libraries:
    license_path = backend / "Libraries" / library / "LICENSE"
    if license_path.is_file():
        datas.append((str(license_path), f"licenses/{library}"))

icon = root / "src/holopatcher/resources/icons/patcher_icon_v2.ico"
excludes = [
    "PyQt6",
    "PySide2",
    "PySide6",
    "IPython",
    "doctest",
    "holopatcher.__main__",
    "holopatcher._packaging_selftest",
    "holopatcher.cli",
    "pip",
    "pydoc",
    "pytest",
    "setuptools",
    "test",
    "tkinter.test",
    "unittest",
    "wheel",
]
a = Analysis(
    [str(root / "packaging/entry.py")],
    pathex=[str(p) for p in sources],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[str(hook_dir)],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
strip_enabled = sys.platform.startswith("linux") or sys.platform == "darwin"
options = dict(
    name="HoloPatcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=strip_enabled,
    upx=False,
    console=not (os.name == "nt" or sys.platform == "darwin"),
    icon=str(icon) if os.name == "nt" else None,
)
if os.name == "nt":
    from holopatcher import CURRENT_VERSION
    from windows_version_info import make_version_info

    options["version"] = make_version_info(CURRENT_VERSION)

if build_mode == "onefile":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        exclude_binaries=False,
        append_pkg=True,
        **options,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        append_pkg=True,
        **options,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=strip_enabled,
        upx=False,
        name="HoloPatcher",
    )

if sys.platform == "darwin":
    bundle_payload = coll if build_mode == "onedir" else exe
    app = BUNDLE(
        bundle_payload,
        icon=str(root / "src/holopatcher/resources/icons/patcher_icon_v2.icns"),
        name="HoloPatcher.app",
        bundle_identifier="org.openkotor.holopatcher",
        info_plist={
            "LSBackgroundOnly": False,
            "LSMinimumSystemVersion": os.environ.get("MACOSX_DEPLOYMENT_TARGET") or "15.0",
            "CFBundleShortVersionString": "2.0.0",
            "CFBundleVersion": "2.0.0",
            "CFBundleGetInfoString": "HoloPatcher 2.0b",
            "NSHighResolutionCapable": True,
        },
    )
