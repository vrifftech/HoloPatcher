"""Select the separate audited PyKotor checkout before importing the application."""
from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import os
import sys
import types
from pathlib import Path

APP_ENV = "HOLOPATCHER_PYKOTOR_ROOT"
LIBRARIES = ['PyKotor', 'Utility']
_active_root: Path | None = None


def backend_sources(root: Path) -> list[Path]:
    sources = [root / "Libraries" / library / "src" for library in LIBRARIES]
    missing = [str(p) for p in sources if not p.is_dir()]
    if missing:
        raise RuntimeError("Incomplete PyKotor checkout; missing: " + ", ".join(missing))
    return sources


def _pin_namespace(name: str, directories: list[Path]) -> None:
    paths = [str(p.resolve()) for p in directories]
    existing = sys.modules.get(name)
    if existing is not None:
        actual = [str(Path(p).resolve()) for p in getattr(existing, "__path__", ())]
        if actual != paths:
            raise RuntimeError(f"{name} was already loaded from a different backend: {actual}")
        return
    if any((p / "__init__.py").is_file() for p in directories):
        module = importlib.import_module(name)
        if str(Path(module.__file__).resolve().parent) not in paths:
            raise RuntimeError(f"{name} resolved outside the selected backend: {module.__file__}")
        return
    # PyKotor's core and OpenGL libraries share a namespace package. Pin its
    # search path so an unrelated site-packages release cannot override it.
    spec = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
    spec.submodule_search_locations = paths
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = paths
    module.__spec__ = spec
    sys.modules[name] = module


def bootstrap_backend() -> Path | None:
    global _active_root
    if getattr(sys, "frozen", False):
        return None  # Use only the modules bundled by PyInstaller.
    if _active_root is not None:
        return _active_root
    value = os.environ.get(APP_ENV) or os.environ.get("PYKOTOR_ROOT")
    if value:
        root = Path(value).expanduser().resolve()
    else:
        parent = Path(__file__).resolve().parents[3]
        candidates = [parent / name for name in ("PyKotor", "PyKotor-master")]
        root = next((p.resolve() for p in candidates if (p / "Libraries/PyKotor/src/pykotor").is_dir()), None)
        if root is None:
            raise RuntimeError(f"Set {APP_ENV} to the audited PyKotor checkout (the directory containing Libraries).")
    sources = backend_sources(root)
    for source in reversed(sources):
        value = str(source)
        if value in sys.path:
            sys.path.remove(value)
        sys.path.insert(0, value)
    _pin_namespace("pykotor", [p / "pykotor" for p in sources if (p / "pykotor").is_dir()])
    _pin_namespace("utility", [p / "utility" for p in sources if (p / "utility").is_dir()])
    importlib.invalidate_caches()
    _active_root = root
    return root


def backend_info() -> dict:
    root = bootstrap_backend()
    modules = ("pykotor", "utility")
    info = {"frozen": bool(getattr(sys, "frozen", False)), "root": str(root) if root else None}
    for name in modules:
        module = importlib.import_module(name)
        info[name] = list(getattr(module, "__path__", ()))
    return info
