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


RUNTIME_ICON_PNG_BASE64 = (
    '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'
)

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
    """Embed a compact PNG fallback without shipping the full icon set."""
    original_icon = package_root / "resources/icons/patcher_icon_v2.png"
    if not original_icon.is_file():
        raise FileNotFoundError(original_icon)

    decoded = base64.b64decode(RUNTIME_ICON_PNG_BASE64, validate=True)
    if not decoded.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Embedded runtime icon is not a valid PNG payload")

    (package_root / "_embedded_assets.py").write_text(
        '"""Generated compact assets for Nuitka builds."""\n'
        f"PATCHER_ICON_PNG_BASE64 = {RUNTIME_ICON_PNG_BASE64!r}\n",
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
        '        try:\n'
        '            from holopatcher._embedded_assets import PATCHER_ICON_PNG_BASE64\n'
        '        except ImportError:\n'
        '            icon_path = pathlib.Path(__file__).parent / "resources/icons/patcher_icon_v2.png"\n'
        '            self._icon = tk.PhotoImage(master=self, file=str(icon_path))\n'
        '        else:\n'
        '            self._icon = tk.PhotoImage(master=self, data=PATCHER_ICON_PNG_BASE64)\n'
        '        self.iconphoto(True, self._icon)\n'
    )
    if old not in source:
        raise RuntimeError("app.py layout changed: icon block not found")
    app_path.write_text(source.replace(old, new, 1), encoding="utf-8")

    # The original PNG, ICO, and ICNS remain available in the frontend checkout
    # for Nuitka's platform-specific build flags. They are not needed at runtime.
    shutil.rmtree(original_icon.parent)


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

    # Packaging test modules are not part of the distributed application.
    (output / "src/holopatcher/_packaging_selftest.py").unlink(missing_ok=True)

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
            """Entry point for Nuitka application builds."""
            from __future__ import annotations

            import multiprocessing

            from holopatcher.__main__ import main as application_main


            def main() -> int:
                multiprocessing.freeze_support()
                return int(application_main())


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
