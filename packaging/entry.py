"""PyInstaller entry point for GUI and headless CLI operations."""
from __future__ import annotations

import multiprocessing


if __name__ == "__main__":
    multiprocessing.freeze_support()
    from holopatcher.__main__ import main

    raise SystemExit(main())
