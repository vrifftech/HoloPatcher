"""Fail-closed policy for no-UPX builds using verified PyInstaller bootloaders."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import platform
import re


def configured_build_mode() -> str:
    return os.environ.get("FRONTEND_BUILD_MODE", "onefile").strip().lower()


def require_build_mode(*expected: str) -> str:
    """Require the configured mode to match the caller's explicit policy."""
    mode = configured_build_mode()
    allowed = {value.strip().lower() for value in expected}
    if mode not in allowed:
        choices = ", ".join(sorted(allowed))
        raise RuntimeError(f"FRONTEND_BUILD_MODE must be one of [{choices}], got {mode!r}")
    return mode


def require_onefile() -> None:
    """Compatibility wrapper used by the standalone Windows build helper."""
    require_build_mode("onefile")


def verify_pyinstaller(package: Path, native_platform: str, version: str) -> dict:
    """Verify ALL native bootloaders against the manifest produced by the fork action."""
    manifest_path = os.environ.get("FRESH_PYINSTALLER_MANIFEST")
    if not manifest_path:
        raise RuntimeError("FRESH_PYINSTALLER_MANIFEST is required; run the fresh-pyinstaller fork action first")
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    system = platform.system()
    if data.get("schema_version") != 1 or data.get("system") != system:
        raise RuntimeError("Wrong fresh-PyInstaller manifest schema or operating system")
    if data.get("pyinstaller_platform") != native_platform or data.get("pyinstaller_version") != version:
        raise RuntimeError("Installed PyInstaller platform/version does not match the fresh build")
    names = {"run", "run_d"}
    if system in {"Windows", "Darwin"}:
        names |= {"runw", "runw_d"}
    if system == "Windows":
        names = {name + ".exe" for name in names}
    hashes = data.get("bootloaders_sha256")
    if not isinstance(hashes, dict) or set(hashes) != names:
        raise RuntimeError("Manifest must contain exactly all expected native bootloaders")
    for filename, expected in hashes.items():
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise RuntimeError(f"Invalid bootloader SHA-256: {filename}")
        binary = package / "bootloader" / native_platform / filename
        if not binary.is_file() or hashlib.sha256(binary.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Fresh PyInstaller bootloader was replaced or is missing: {binary}")
    return data


def verify_onefile_payload(executable: Path) -> dict:
    """Check the built executable contains its binaries, not just an onedir launcher."""
    from PyInstaller.archive.readers import CArchiveReader

    if not executable.is_file():
        raise RuntimeError(f"Expected a single executable file: {executable}")
    archive = CArchiveReader(str(executable))
    binary_names = [name for name, entry in archive.toc.items() if entry[-1] == "b"]
    if not binary_names:
        raise RuntimeError(f"No embedded native binaries: this is not the required onefile payload: {executable}")
    if any(entry[-1] == "d" for entry in archive.toc.values()):
        raise RuntimeError("External multipackage dependencies are not allowed in this standalone payload")
    debug_suffixes = (".pdb", ".ilk", ".exp", ".lib")
    debug_artifacts = sorted(name for name in archive.toc if name.casefold().endswith(debug_suffixes))
    if debug_artifacts:
        raise RuntimeError(f"Debug/linker artifacts must not be distributed: {debug_artifacts}")
    return {
        "mode": "onefile",
        "embedded_native_binaries": len(binary_names),
        "debug_artifacts": 0,
        "upx": False,
    }


def verify_onedir_payload(directory: Path) -> dict:
    """Check an onedir payload has a launcher and external support files."""
    if not directory.is_dir():
        raise RuntimeError(f"Expected an onedir distribution: {directory}")
    launcher = directory / "HoloPatcher"
    if not launcher.is_file():
        raise RuntimeError(f"Missing onedir launcher: {launcher}")
    files = [path for path in directory.rglob("*") if path.is_file()]
    support_files = [path for path in files if path != launcher]
    if not support_files:
        raise RuntimeError(f"No onedir support files were collected: {directory}")
    return {
        "mode": "onedir",
        "files": len(files),
        "payload_bytes": sum(path.stat().st_size for path in files),
        "upx": False,
    }


def verify_app_bundle_payload(app: Path) -> dict:
    """Check a macOS .app contains an onedir payload rather than a onefile archive."""
    if not app.is_dir():
        raise RuntimeError(f"Expected a macOS app bundle: {app}")
    contents = app / "Contents"
    plist_path = contents / "Info.plist"
    if not plist_path.is_file():
        raise RuntimeError(f"Missing app bundle Info.plist: {plist_path}")
    with plist_path.open("rb") as stream:
        info = plistlib.load(stream)
    executable_name = info.get("CFBundleExecutable")
    if not isinstance(executable_name, str) or not executable_name:
        raise RuntimeError(f"Invalid CFBundleExecutable in {plist_path}")
    executable = contents / "MacOS" / executable_name
    if not executable.is_file():
        raise RuntimeError(f"Missing app bundle executable: {executable}")
    files = [path for path in contents.rglob("*") if path.is_file() and not path.is_symlink()]
    support_files = [path for path in files if path not in {executable, plist_path}]
    if not support_files:
        raise RuntimeError(f"No onedir support files were collected into app bundle: {app}")
    return {
        "mode": "app-onedir",
        "executable": str(executable.relative_to(app)),
        "files": len(files),
        "payload_bytes": sum(path.stat().st_size for path in files),
        "strip": True,
        "upx": False,
    }
