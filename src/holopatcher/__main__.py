"""Choose terminal or graphical operation before importing either frontend."""
from __future__ import annotations

import argparse
import io
import json
import multiprocessing
import sys
from collections.abc import Sequence

from holopatcher import CURRENT_VERSION, ExitCode


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="HoloPatcher: launch the GUI, or request a headless operation.",
        epilog="Legacy syntax: HoloPatcher GameDir ModDir [NamespaceIndex] --install",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"HoloPatcher {CURRENT_VERSION}")
    actions = parser.add_mutually_exclusive_group()
    for name, help_text in (
        ("install", "Install the selected option without creating the GUI."),
        ("uninstall", "Restore the latest package backup and keep that backup."),
        ("validate", "Validate INI syntax/configuration, not installation success."),
        ("list-namespaces", "List option indices, IDs, names, and configuration paths."),
        ("backend-info", "Print the selected backend paths without importing Tk."),
    ):
        actions.add_argument(f"--{name}", action="store_const", dest="action", const=name, help=help_text)
    parser.add_argument("--game-dir", help="Game or supported content directory.")
    parser.add_argument("--tslpatchdata", help="Package, patch-data directory, namespace directory, or INI.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--namespace-option-index", type=int, help="Zero-based option index.")
    selection.add_argument("--namespace-id", help="Namespace INI ID, not its display name.")
    parser.add_argument("--yes", action="store_true", help="Acknowledge normal install/restore consent; never safety mismatches.")
    parser.add_argument("--non-interactive", action="store_true", help="Never prompt or read stdin. Use with --yes for unattended consent.")
    parser.add_argument("--console", action="store_true", help="Keep the console when launching the GUI (CLI always keeps it).")
    parser.add_argument("paths", nargs="*", metavar="PATH", help=argparse.SUPPRESS)
    args = parser.parse_intermixed_args(argv)
    paths = args.paths
    del args.paths
    if paths:
        if len(paths) not in (2, 3):
            parser.error("legacy syntax requires GameDir ModDir [NamespaceIndex]")
        for field, value in zip(("game_dir", "tslpatchdata"), paths[:2]):
            supplied = getattr(args, field)
            if supplied is not None and supplied != value:
                parser.error(f"conflicting positional and --{field.replace('_', '-')} values")
            setattr(args, field, value)
        if len(paths) == 3:
            try:
                index = int(paths[2])
            except ValueError:
                parser.error("NamespaceIndex must be an integer")
            if args.namespace_id is not None:
                parser.error("a positional NamespaceIndex cannot be combined with --namespace-id")
            if args.namespace_option_index is not None and args.namespace_option_index != index:
                parser.error("conflicting namespace indices")
            args.namespace_option_index = index
    if args.action in {"install", "uninstall", "validate", "list-namespaces"} and not args.tslpatchdata:
        parser.error(f"--{args.action} requires --tslpatchdata (or legacy ModDir)")
    if args.action in {"install", "uninstall"} and not args.game_dir:
        parser.error(f"--{args.action} requires --game-dir (or legacy GameDir)")
    if (args.yes or args.non_interactive) and args.action not in {"install", "uninstall", "validate", "list-namespaces"}:
        parser.error("--yes and --non-interactive require a headless operation")
    if (args.namespace_option_index is not None or args.namespace_id is not None) and not args.tslpatchdata:
        parser.error("namespace selection requires --tslpatchdata (or legacy ModDir)")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    multiprocessing.freeze_support()
    # Use the same diagnostic encoding for argument/bootstrap errors and operations.
    # Escaping diagnostic surrogates does not transcode any game resource.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True, write_through=True)
    args = parse_args(argv)
    try:
        from holopatcher.bootstrap import backend_info, bootstrap_backend
        if args.action == "backend-info":
            print(json.dumps(backend_info(), indent=2))
            return ExitCode.SUCCESS
        bootstrap_backend()
        if args.action is not None:
            from holopatcher.cli import execute_cli
            return execute_cli(args)
        # Only normal launch imports Tk and constructs the original application.
        from holopatcher.app import main as run_gui
        return run_gui(args)
    except KeyboardInterrupt:
        print("[Warning] Interrupted. Previously completed operations may remain installed.", file=sys.stderr)
        return ExitCode.INTERRUPTED
    except Exception as exc:
        print(f"[Error] HoloPatcher could not complete: {type(exc).__name__}: {exc}", file=sys.stderr)
        return ExitCode.EXCEPTION_DURING_INSTALL


if __name__ == "__main__":
    raise SystemExit(main())
