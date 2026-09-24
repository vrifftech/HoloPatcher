"""PyInstaller GUI entry point; headless operations remain source-checkout only."""
from __future__ import annotations

import multiprocessing


if __name__ == "__main__":
    multiprocessing.freeze_support()
    from holopatcher.bootstrap import bootstrap_backend

    bootstrap_backend()
    from holopatcher.app import main

    raise SystemExit(main(None))
