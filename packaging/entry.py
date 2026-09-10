"""PyInstaller entry point; use the bundled backend rather than a source checkout."""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from holopatcher.__main__ import main
    raise SystemExit(main())
