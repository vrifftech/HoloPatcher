"""PyInstaller entry point; use the bundled backend rather than a source checkout."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if sys.argv[1:] == ["--packaging-self-test"]:
        from holopatcher._packaging_selftest import main
    else:
        from holopatcher.__main__ import main
    raise SystemExit(main())
