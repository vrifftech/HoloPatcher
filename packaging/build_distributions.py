"""Build HoloPatcher distributions with an already verified, freshly built PyInstaller.

No backend or PyInstaller is fetched by this script. CI checks out both first.
Only the two pinned AppImage assembly tools are downloaded on Linux.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
import urllib.request

from smoke_test import smoke_test
from build_policy import require_onefile, verify_onefile_payload, verify_pyinstaller

ROOT = Path(__file__).resolve().parent.parent


def run(command: list[str], **kwargs) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, check=True, **kwargs)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        result = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
        return result.hexdigest()


def checkout_commit(root: Path) -> str:
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def download_checked(url: str, expected_hash: str, destination: Path) -> None:
    if not url.startswith("https://github.com/AppImage/"):
        raise ValueError("AppImage tools must come from the configured upstream AppImage releases")
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "HoloPatcher-build"})
    with urllib.request.urlopen(request, timeout=90) as response, destination.open("wb") as stream:
        shutil.copyfileobj(response, stream)
    actual_hash = sha256(destination)
    if actual_hash != expected_hash:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded tool checksum mismatch for {url}: {actual_hash}")
    destination.chmod(0o755)


def freeze() -> Path:
    require_onefile()
    mode = "onefile"
    env = os.environ.copy()
    env["FRONTEND_BUILD_MODE"] = mode
    dist = ROOT / "dist" / mode
    run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
         "--distpath", str(dist), "--workpath", str(ROOT / ".pyinstaller-build" / mode),
         str(ROOT / "packaging/HoloPatcher.spec")], cwd=ROOT, env=env)
    return dist


def build_appimage(payload: Path, release: Path, basename: str, *, gui: bool) -> dict:
    lock = json.loads((ROOT / "packaging/appimage-tools.json").read_text(encoding="utf-8"))
    tools = ROOT / ".packaging-tools"
    appimagetool = tools / "appimagetool-x86_64.AppImage"
    runtime = tools / "runtime-x86_64"
    for key, path in (("appimagetool", appimagetool), ("runtime", runtime)):
        download_checked(lock[key]["url"], lock[key]["sha256"], path)
    appdir = ROOT / "dist/HoloPatcher.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    (appdir / "usr/bin").mkdir(parents=True)
    # AppImage is the outer container; its application payload is ONE executable.
    verify_onefile_payload(payload)
    shutil.copy2(payload, appdir / "usr/bin/HoloPatcher")
    (appdir / "usr/bin/HoloPatcher").chmod(0o755)
    (appdir / "AppRun").write_text(
        '#!/bin/sh\nset -eu\n'
        'HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\n'
        'export APPDIR="$HERE"\n'
        'exec "$HERE/usr/bin/HoloPatcher" "$@"\n', encoding="utf-8"
    )
    (appdir / "AppRun").chmod(0o755)
    # Do not chdir: relative CLI arguments must remain relative to the caller.
    (appdir / "holopatcher.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=HoloPatcher\n"
        "Comment=KotOR mod patcher\nExec=HoloPatcher\nIcon=holopatcher\n"
        "Terminal=false\nCategories=Utility;\n", encoding="utf-8"
    )
    icon = ROOT / "src/holopatcher/resources/icons/patcher_icon_v2.png"
    shutil.copy2(icon, appdir / "holopatcher.png")
    (appdir / ".DirIcon").symlink_to("holopatcher.png")
    run(["desktop-file-validate", str(appdir / "holopatcher.desktop")])
    executable = release / f"{basename}.AppImage"
    env = os.environ.copy()
    env.update({"ARCH": "x86_64", "APPIMAGE_EXTRACT_AND_RUN": "1"})
    # Explicit --runtime-file prevents appimagetool fetching a moving runtime.
    run([str(appimagetool), "--runtime-file", str(runtime), str(appdir), str(executable)], env=env)
    executable.chmod(0o755)
    smoke_test(executable, gui=gui, appimage=True)
    # Keep a permission-preserving copy as well as the requested .AppImage.
    with tarfile.open(release / f"{basename}.AppImage.tar.gz", "w:gz") as archive:
        archive.add(executable, arcname=executable.name)
    return lock


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=("windows-x64", "linux-x86_64", "macos-x86_64", "macos-arm64"))
    parser.add_argument("--no-gui-smoke", action="store_true", help="Local diagnostics only; CI runs GUI tests.")
    args = parser.parse_args()
    machine = platform.machine().lower()
    expected = {
        "windows-x64": ("Windows", {"amd64", "x86_64"}),
        "linux-x86_64": ("Linux", {"amd64", "x86_64"}),
        "macos-x86_64": ("Darwin", {"amd64", "x86_64"}),
        "macos-arm64": ("Darwin", {"arm64", "aarch64"}),
    }
    system, machines = expected[args.target]
    if platform.system() != system or machine not in machines:
        raise RuntimeError(f"Target {args.target} does not match {platform.system()}/{machine}")
    backend = Path(os.environ["HOLOPATCHER_PYKOTOR_ROOT"]).resolve()
    manifest = Path(os.environ["FRESH_PYINSTALLER_MANIFEST"]).resolve()
    # Match the PyInstaller version family supported by this application's spec.
    import PyInstaller
    require_onefile()
    provenance = verify_pyinstaller(Path(PyInstaller.__file__).resolve().parent, PyInstaller.PLATFORM, PyInstaller.__version__)
    if not PyInstaller.__version__.startswith("6."):
        raise RuntimeError("This build integration targets PyInstaller 6.x; review the spec before upgrading its major version")
    release = ROOT / "release"
    release.mkdir(exist_ok=True)
    if any(release.iterdir()):
        raise RuntimeError("Use an empty release/ directory to avoid publishing stale files")
    gui = not args.no_gui_smoke
    version_namespace: dict = {}
    # Identity module has no frontend/backend side effects.
    exec((ROOT / "src/holopatcher/__init__.py").read_text(encoding="utf-8"), version_namespace)
    version = version_namespace["CURRENT_VERSION"]
    basename = f"HoloPatcher-{version}-{args.target}"
    appimage_lock = None
    frozen = freeze()
    if system == "Windows":
        onefile = frozen / "HoloPatcher.exe"
        payload_report = verify_onefile_payload(onefile)
        smoke_test(onefile, gui=gui)
        shutil.copy2(onefile, release / f"{basename}.exe")
    elif system == "Linux":
        onefile = frozen / "HoloPatcher"
        payload_report = verify_onefile_payload(onefile)
        smoke_test(onefile, gui=gui)
        appimage_lock = build_appimage(onefile, release, basename, gui=gui)
    else:
        app = frozen / "HoloPatcher.app"
        executable = app / "Contents/MacOS/HoloPatcher"
        payload_report = verify_onefile_payload(executable)
        smoke_test(executable, gui=gui)
        run(["codesign", "--verify", "--deep", "--strict", str(app)])
        # .app remains a bundle directory; its program is a onefile executable.
        run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
             str(app), str(release / f"{basename}.app.zip")])

    metadata = {
        "application": "HoloPatcher", "version": version, "target": args.target,
        "packaging_policy": payload_report,
        "frontend_commit": checkout_commit(ROOT), "backend_commit": checkout_commit(backend),
        "pyinstaller": provenance, "pyinstaller_version": PyInstaller.__version__,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version, "os": platform.platform(),
        "macos_deployment_target": os.environ.get("MACOSX_DEPLOYMENT_TARGET") or None,
        "appimage_tools": appimage_lock,
        "gui_smoke_test_run": gui,
        "signing": "No release signing credentials supplied; macOS uses PyInstaller ad-hoc signing. No notarization.",
    }
    (release / "build-provenance.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    (release / "build-environment.txt").write_text(
        subprocess.check_output([sys.executable, "-m", "pip", "freeze", "--all"], text=True), encoding="utf-8"
    )
    shutil.copy2(manifest, release / "fresh-pyinstaller.json")
    checksums = [f"{sha256(path)}  {path.name}" for path in sorted(release.iterdir()) if path.is_file()]
    (release / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print(f"Verified distribution files: {release}")


if __name__ == "__main__":
    main()
