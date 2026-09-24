"""Check that a built GUI starts without using a source backend or network access."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import time


def smoke_test(executable: Path, *, gui: bool = True, appimage: bool = False) -> None:
    if not gui:
        return
    executable = executable.resolve()
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "TCL_LIBRARY", "TK_LIBRARY"):
        env.pop(key, None)
    env["HOLOPATCHER_PYKOTOR_ROOT"] = "__invalid_backend_must_not_be_used__"
    env["PYKOTOR_ROOT"] = "__invalid_backend_must_not_be_used__"
    if appimage:
        env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
    else:
        env.pop("APPIMAGE_EXTRACT_AND_RUN", None)

    with tempfile.TemporaryDirectory(prefix="HoloPatcher smoke ") as directory:
        work = Path(directory)
        data = work / "tslpatchdata"
        data.mkdir(parents=True)
        (data / "changes.ini").write_text(
            "[Settings]\nWindowCaption=Packaging smoke test\nLogLevel=3\n",
            encoding="utf-8",
        )
        process = subprocess.Popen(
            [str(executable)],
            cwd=work,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            time.sleep(3)
            code = process.poll()
            if code is not None:
                stderr = process.stderr.read() if process.stderr is not None else ""
                raise RuntimeError(f"GUI exited during startup with code {code}: {stderr}")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        print(f"GUI startup smoke test passed: {executable}", flush=True)
