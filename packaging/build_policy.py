"""Fail-closed policy for onefile, no-UPX builds using verified bootloaders."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re


def require_onefile() -> None:
    # Reject old local/CI settings rather than quietly producing a different format.
    if os.environ.get("FRONTEND_BUILD_MODE", "onefile").strip().lower() != "onefile":
        raise RuntimeError("Only onefile builds are supported; remove FRONTEND_BUILD_MODE=onedir")


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
    return {"mode": "onefile", "embedded_native_binaries": len(binary_names), "upx": False}
