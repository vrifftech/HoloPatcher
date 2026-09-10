"""Test a built HoloPatcher without a source backend, user input, or game files."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def smoke_test(executable: Path, *, gui: bool = True, appimage: bool = False) -> None:
    executable = executable.resolve()
    env = os.environ.copy()
    # Frozen code must use its bundled backend, even if these point nowhere.
    for key in ("PYTHONPATH", "PYTHONHOME", "TCL_LIBRARY", "TK_LIBRARY"):
        env.pop(key, None)
    env["HOLOPATCHER_PYKOTOR_ROOT"] = "__invalid_backend_must_not_be_used__"
    env["PYKOTOR_ROOT"] = "__invalid_backend_must_not_be_used__"
    if appimage:
        # Exercise the actual AppImage without requiring a FUSE mount in CI.
        env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
    else:
        env.pop("APPIMAGE_EXTRACT_AND_RUN", None)

    with tempfile.TemporaryDirectory(prefix="HoloPatcher smoke ") as directory:
        work = Path(directory)
        data = work / "Example Mod" / "tslpatchdata"
        data.mkdir(parents=True)
        (data / "changes.ini").write_text(
            "[Settings]\nWindowCaption=Packaging smoke test\nLogLevel=3\n", encoding="utf-8"
        )

        def invoke(*arguments: str, expected: int = 0, graphical: bool = False) -> str:
            invocation_env = env.copy()
            if not graphical:
                # CLI commands must not need a display even when a GUI is bundled.
                invocation_env.pop("DISPLAY", None)
                invocation_env.pop("WAYLAND_DISPLAY", None)
            result = subprocess.run(
                [str(executable), *arguments], cwd=work, env=invocation_env,
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
            )
            print(f"$ {executable.name} {' '.join(arguments)}\n{result.stdout}", flush=True)
            if result.stderr:
                print(result.stderr, flush=True)
            if result.returncode != expected:
                raise RuntimeError(f"Smoke test {arguments!r}: exit {result.returncode}, expected {expected}")
            return result.stdout

        if "HoloPatcher 2.0b" not in invoke("--version"):
            raise RuntimeError("Wrong application version")
        invoke("--help")
        info = json.loads(invoke("--backend-info"))
        if info.get("frozen") is not True or info.get("root") is not None:
            raise RuntimeError(f"Application did not use its frozen backend: {info}")
        if not info.get("pykotor") or not info.get("utility"):
            raise RuntimeError(f"Missing bundled backend namespaces: {info}")
        catalogue = json.loads(invoke("--list-namespaces", "--tslpatchdata", str(data)))
        if len(catalogue.get("namespaces", [])) != 1:
            raise RuntimeError(f"Unexpected namespace catalogue: {catalogue}")
        invoke("--validate", "--tslpatchdata", str(data), "--non-interactive")
        invoke("--list-namespaces", "--tslpatchdata", str(data), "--namespace-option-index", "999", expected=4)
        invoke("--not-a-real-option", expected=2)
        if gui:
            result = json.loads(invoke("--packaging-self-test", graphical=True))
            if result.get("ok") is not True or not result.get("frozen"):
                raise RuntimeError(f"GUI smoke test failed: {result}")
            if appimage and Path(result["launch_directory"]).resolve() != executable.parent:
                raise RuntimeError("AppImage default mod directory points inside its mount/extraction directory")
        print(f"All packaging smoke tests passed: {executable}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--no-gui", action="store_true")
    parser.add_argument("--appimage", action="store_true")
    options = parser.parse_args()
    smoke_test(options.executable, gui=not options.no_gui, appimage=options.appimage)
