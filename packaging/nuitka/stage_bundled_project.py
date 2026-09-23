"""Create temporary HoloPatcher build input for Nuitka one-file builds.

The staged project contains the checked-out HoloPatcher frontend plus the
PyKotor and Utility sources selected by the workflow. The source repositories
are not modified.
"""
from __future__ import annotations

import argparse
import ast
import base64
import shutil
import subprocess
import textwrap
import tomllib
from pathlib import Path


def copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))


def git_revision(root: Path, fallback: str) -> str:
    if fallback:
        return fallback
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def patch_bootstrap(path: Path) -> None:
    """Make the temporary staged backend usable under Nuitka.

    Nuitka intentionally does not set ``sys.frozen``, so the normal PyInstaller
    branch alone cannot identify bundled backend modules.
    """
    source = path.read_text(encoding="utf-8")
    anchor = "_active_root: Path | None = None\n"
    helper = textwrap.dedent(
        '''

        def _packaged_backend_available() -> bool:
            """Return whether this generated distribution contains its backend."""
            try:
                importlib.import_module("holopatcher._packaged_backend")
                importlib.import_module("pykotor")
                importlib.import_module("utility")
            except (ImportError, ModuleNotFoundError):
                return False
            return True
        '''
    )
    if anchor not in source:
        raise RuntimeError("bootstrap.py layout changed: active-root anchor not found")
    source = source.replace(anchor, anchor + helper, 1)

    old = '    if getattr(sys, "frozen", False):\n        return None  # Use only the modules bundled by PyInstaller.\n'
    new = (
        '    if getattr(sys, "frozen", False) or _packaged_backend_available():\n'
        '        return None  # Use only the modules copied into this generated distribution.\n'
    )
    if old not in source:
        raise RuntimeError("bootstrap.py layout changed: frozen-backend branch not found")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def embed_gui_icon(package_root: Path) -> None:
    """Embed a PNG fallback for one-file environments without a visible data path."""
    icon_path = package_root / "resources/icons/patcher_icon_v2.png"
    if not icon_path.is_file():
        raise FileNotFoundError(icon_path)
    encoded = base64.b64encode(icon_path.read_bytes()).decode("ascii")
    (package_root / "_embedded_assets.py").write_text(
        '"""Generated assets for Nuitka one-file builds."""\n'
        f"PATCHER_ICON_PNG_BASE64 = {encoded!r}\n",
        encoding="utf-8",
    )

    app_path = package_root / "app.py"
    source = app_path.read_text(encoding="utf-8")
    old = (
        '        icon_path = pathlib.Path(__file__).parent / "resources/icons/patcher_icon_v2.png"\n'
        '        self._icon = tk.PhotoImage(master=self, file=str(icon_path))\n'
        '        self.iconphoto(True, self._icon)\n'
    )
    new = (
        '        icon_path = pathlib.Path(__file__).parent / "resources/icons/patcher_icon_v2.png"\n'
        '        if icon_path.is_file():\n'
        '            self._icon = tk.PhotoImage(master=self, file=str(icon_path))\n'
        '        else:\n'
        '            from holopatcher._embedded_assets import PATCHER_ICON_PNG_BASE64\n'
        '            self._icon = tk.PhotoImage(master=self, data=PATCHER_ICON_PNG_BASE64)\n'
        '        self.iconphoto(True, self._icon)\n'
    )
    if old not in source:
        raise RuntimeError("app.py layout changed: icon block not found")
    app_path.write_text(source.replace(old, new, 1), encoding="utf-8")


def rewrite_scriptdefs_for_nuitka(path: Path, *, chunk_size: int = 32) -> None:
    """Split generated script-definition lists into small builder functions.

    ``pykotor.common.scriptdefs`` is generated source containing thousands of
    constructor expressions in four module-level list literals. Nuitka turns a
    module body into one native initializer. On Windows, that oversized native
    initializer can exhaust the default PE stack before the NSS compiler has
    even parsed a script. Small builder functions preserve the exact objects and
    order while bounding each native stack frame.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines(keepends=True)

    def source_segment(node: ast.AST) -> str:
        """Slice one AST node using UTF-8 byte offsets used by CPython AST."""
        assert hasattr(node, "lineno") and hasattr(node, "end_lineno")
        start_line = lines[node.lineno - 1]
        end_line = lines[node.end_lineno - 1]
        if node.lineno == node.end_lineno:
            encoded = start_line.encode("utf-8")
            return encoded[node.col_offset : node.end_col_offset].decode("utf-8")
        pieces = [start_line.encode("utf-8")[node.col_offset :].decode("utf-8")]
        pieces.extend(lines[node.lineno : node.end_lineno - 1])
        pieces.append(end_line.encode("utf-8")[: node.end_col_offset].decode("utf-8"))
        return "".join(pieces)

    imports: list[str] = []
    definitions: list[tuple[str, list[str]]] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(source_segment(node))
            continue
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.List)
        ):
            definitions.append(
                (node.targets[0].id, [source_segment(element) for element in node.value.elts])
            )
            continue
        raise RuntimeError(
            f"scriptdefs.py layout changed: unsupported top-level {type(node).__name__}"
        )

    expected = ["KOTOR_CONSTANTS", "TSL_CONSTANTS", "KOTOR_FUNCTIONS", "TSL_FUNCTIONS"]
    if [name for name, _elements in definitions] != expected:
        raise RuntimeError(
            "scriptdefs.py layout changed: expected "
            + ", ".join(expected)
            + "; found "
            + ", ".join(name for name, _elements in definitions)
        )

    output = [
        '"""Generated staging form with bounded native module-initializer frames."""',
        *imports,
        "",
    ]
    chunk_count = 0
    element_count = 0
    for name, elements in definitions:
        builders: list[str] = []
        element_count += len(elements)
        for start in range(0, len(elements), chunk_size):
            builder = f"_nuitka_build_{name.lower()}_{start // chunk_size}"
            builders.append(builder)
            chunk_count += 1
            output.append(f"def {builder}():")
            output.append("    return [")
            for expression in elements[start : start + chunk_size]:
                output.append("        " + expression.replace("\n", "\n        ") + ",")
            output.append("    ]")
            output.append("")

        output.append(f"{name} = []")
        for builder in builders:
            output.append(f"{name}.extend({builder}())")
        output.append("")

    path.write_text("\n".join(output), encoding="utf-8")
    print(
        f"Rewrote {path.name}: {element_count} definitions across "
        f"{chunk_count} bounded builders (chunk size {chunk_size})."
    )


def patch_ply_for_frozen_tables(staged_src: Path) -> None:
    """Use generated PLY tables instead of runtime source inspection."""
    compiler = staged_src / "pykotor/resource/formats/ncs/compiler"

    lexer_path = compiler / "lexer.py"
    lexer = lexer_path.read_text(encoding="utf-8")
    old_lexer = "self.lexer: lex.Lexer = lex.lex(module=self, errorlog=errorlog, nowarn=nowarn)"
    new_lexer = (
        'self.lexer: lex.Lexer = lex.lex(\n'
        '            module=self, errorlog=errorlog, nowarn=nowarn, optimize=True,\n'
        '            lextab="pykotor.resource.formats.ncs.compiler.lextab",\n'
        '        )'
    )
    if old_lexer not in lexer:
        raise RuntimeError("lexer.py layout changed: lex.lex call not found")
    lexer_path.write_text(lexer.replace(old_lexer, new_lexer, 1), encoding="utf-8")

    parser_path = compiler / "parser.py"
    parser = parser_path.read_text(encoding="utf-8")
    old_parser = (
        '        self.parser: yacc.LRParser = yacc.yacc(\n'
        '            module=self,\n'
        '            errorlog=errorlog,\n'
        '            write_tables=False,\n'
        '            debug=debug,\n'
        '        )\n'
    )
    new_parser = (
        '        self.parser: yacc.LRParser = yacc.yacc(\n'
        '            module=self,\n'
        '            errorlog=errorlog,\n'
        '            optimize=True,\n'
        '            write_tables=True,\n'
        '            tabmodule="pykotor.resource.formats.ncs.compiler.parsetab",\n'
        '            debug=debug,\n'
        '        )\n'
    )
    if old_parser not in parser:
        raise RuntimeError("parser.py layout changed: yacc.yacc call not found")
    parser_path.write_text(parser.replace(old_parser, new_parser, 1), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend-root", type=Path, required=True)
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frontend-ref", default="")
    parser.add_argument("--backend-ref", default="")
    args = parser.parse_args()

    frontend = args.frontend_root.resolve()
    backend = args.backend_root.resolve()
    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    (output / "src").mkdir(parents=True)

    sources = {
        "holopatcher": frontend / "src/holopatcher",
        "pykotor": backend / "Libraries/PyKotor/src/pykotor",
        "utility": backend / "Libraries/Utility/src/utility",
    }
    for name, source in sources.items():
        if not source.is_dir():
            raise FileNotFoundError(f"Missing source package: {source}")
        copy_tree(source, output / "src" / name)

    patch_bootstrap(output / "src/holopatcher/bootstrap.py")
    embed_gui_icon(output / "src/holopatcher")
    rewrite_scriptdefs_for_nuitka(output / "src/pykotor/common/scriptdefs.py")
    patch_ply_for_frozen_tables(output / "src")

    frontend_ref = git_revision(frontend, args.frontend_ref)
    backend_ref = git_revision(backend, args.backend_ref)
    (output / "src/holopatcher/_packaged_backend.py").write_text(
        '"""Generated provenance marker for a bundled backend."""\n'
        f"FRONTEND_REVISION = {frontend_ref!r}\n"
        f"BACKEND_REVISION = {backend_ref!r}\n",
        encoding="utf-8",
    )
    (output / "src/holopatcher/_packaged_entry.py").write_text(
        textwrap.dedent(
            '''
            """Entry point for Nuitka one-file builds."""
            from __future__ import annotations

            import json
            import multiprocessing
            import sys
            import traceback


            def _probe(name: str, callback):
                print(json.dumps({"probe": name, "status": "starting"}, sort_keys=True), flush=True)
                try:
                    detail = callback()
                except BaseException as exc:
                    traceback.print_exc()
                    print(
                        json.dumps(
                            {
                                "probe": name,
                                "status": "failed",
                                "error": f"{type(exc).__name__}: {exc}",
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                    raise
                print(
                    json.dumps({"probe": name, "status": "passed", "detail": detail}, sort_keys=True),
                    flush=True,
                )
                return detail


            def _probe_backend():
                from holopatcher.bootstrap import backend_info

                return backend_info()


            def _nss_phase(name: str, callback):
                print(
                    json.dumps(
                        {"probe": "nss", "phase": name, "status": "starting"},
                        sort_keys=True,
                    ),
                    flush=True,
                )
                detail = callback()
                print(
                    json.dumps(
                        {"probe": "nss", "phase": name, "status": "passed"},
                        sort_keys=True,
                    ),
                    flush=True,
                )
                return detail


            def _probe_nss():
                def load_ply():
                    from ply import yacc
                    return yacc

                yacc = _nss_phase("import-ply", load_ply)

                def load_game():
                    from pykotor.common.misc import Game
                    return Game

                Game = _nss_phase("import-game", load_game)

                def load_scriptdefs():
                    from pykotor.common import scriptdefs
                    return {
                        "k1_constants": len(scriptdefs.KOTOR_CONSTANTS),
                        "k2_constants": len(scriptdefs.TSL_CONSTANTS),
                        "k1_functions": len(scriptdefs.KOTOR_FUNCTIONS),
                        "k2_functions": len(scriptdefs.TSL_FUNCTIONS),
                    }

                definition_counts = _nss_phase("import-scriptdefs", load_scriptdefs)

                def load_scriptlib():
                    from pykotor.common import scriptlib
                    return {
                        "k1_scripts": len(scriptlib.KOTOR_LIBRARY),
                        "k2_scripts": len(scriptlib.TSL_LIBRARY),
                    }

                library_counts = _nss_phase("import-scriptlib", load_scriptlib)

                def load_ncs_api():
                    from pykotor.resource.formats.ncs import bytes_ncs, compile_nss, read_ncs
                    return bytes_ncs, compile_nss, read_ncs

                bytes_ncs, compile_nss, read_ncs = _nss_phase("import-ncs-api", load_ncs_api)

                counts = {}
                for game in (Game.K1, Game.K2):
                    def compile_one(game=game):
                        compiled = compile_nss(
                            "void main() { int value = 1; }",
                            game,
                            errorlog=yacc.NullLogger(),
                        )
                        instructions = read_ncs(bytes_ncs(compiled)).instructions
                        if not instructions:
                            raise RuntimeError(f"NSS compilation produced no instructions for {game}")
                        return len(instructions)

                    counts[game.name] = _nss_phase(f"compile-{game.name.lower()}", compile_one)

                return {
                    "definitions": definition_counts,
                    "libraries": library_counts,
                    "instruction_counts": counts,
                }


            def _probe_tcl():
                import tkinter as tk

                interp = tk.Tcl()
                return {
                    "patchlevel": interp.eval("info patchlevel"),
                    "library": interp.eval("info library"),
                }


            def _headless_packaging_self_test() -> int:
                result = {
                    "ok": True,
                    "backend": _probe("backend", _probe_backend),
                    "nss_instruction_counts": _probe("nss", _probe_nss),
                    "tcl": _probe("tcl", _probe_tcl),
                    "nuitka": "__compiled__" in globals(),
                }
                print(json.dumps(result, sort_keys=True), flush=True)
                return 0


            def main() -> int:
                multiprocessing.freeze_support()
                if sys.argv[1:] == ["--packaging-headless-self-test"]:
                    return _headless_packaging_self_test()
                if sys.argv[1:] == ["--packaging-self-test"]:
                    from holopatcher._packaging_selftest import main as selected_main
                else:
                    from holopatcher.__main__ import main as selected_main
                return int(selected_main())


            if __name__ == "__main__":
                raise SystemExit(main())
            '''
        ).lstrip(),
        encoding="utf-8",
    )

    with (frontend / "pyproject.toml").open("rb") as stream:
        frontend_project = tomllib.load(stream)["project"]
    version = str(frontend_project["version"])

    (output / "README.md").write_text(
        "# HoloPatcher Nuitka build input\n\n"
        "Generated temporarily by the HoloPatcher Nuitka workflow.\n",
        encoding="utf-8",
    )
    shutil.copy2(frontend / "LICENSE", output / "LICENSE")
    (output / "pyproject.toml").write_text(
        textwrap.dedent(
            f'''
            [build-system]
            requires = ["setuptools>=68", "wheel"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "HoloPatcher-Bundled"
            version = "{version}"
            description = "HoloPatcher with its selected backend"
            requires-python = ">=3.10"
            dependencies = [
              "ply>=3.11,<4",
              "charset-normalizer>=2,<4",
              "defusedxml>=0.7,<1",
            ]
            readme = "README.md"
            license = {{file = "LICENSE"}}
            authors = [{{name = "OpenKotOR"}}]

            [project.scripts]
            holopatcher = "holopatcher._packaged_entry:main"

            [tool.setuptools]
            package-dir = {{"" = "src"}}
            include-package-data = false

            [tool.setuptools.packages.find]
            where = ["src"]
            include = ["holopatcher", "holopatcher.*", "pykotor", "pykotor.*", "utility", "utility.*"]
            namespaces = true

            [tool.setuptools.package-data]
            holopatcher = ["resources/icons/*"]
            "*" = ["*.pyi"]
            '''
        ).lstrip(),
        encoding="utf-8",
    )

    print(f"Staged {output}")
    print(f"Frontend revision: {frontend_ref}")
    print(f"Backend revision:  {backend_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
