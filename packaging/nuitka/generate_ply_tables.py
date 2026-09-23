"""Generate deterministic PLY lexer/parser tables inside staged source."""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", type=Path, required=True)
    args = parser.parse_args()

    src = args.stage.resolve() / "src"
    sys.path.insert(0, str(src))

    from pykotor.resource.formats.ncs.compiler.lexer import NssLexer
    from pykotor.resource.formats.ncs.compiler.parser import NssParser

    NssLexer()
    NssParser([], [], {})
    importlib.invalidate_caches()

    compiler = src / "pykotor/resource/formats/ncs/compiler"
    expected = [compiler / "lextab.py", compiler / "parsetab.py"]
    missing = [str(path) for path in expected if not path.is_file()]
    if missing:
        raise RuntimeError("PLY table generation failed: " + ", ".join(missing))
    for path in expected:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
