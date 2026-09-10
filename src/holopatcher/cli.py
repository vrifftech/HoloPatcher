"""Terminal operations around the audited backend. This module never imports Tk."""
from __future__ import annotations

import json
import pathlib
import signal
import sys
from contextlib import contextmanager, nullcontext, redirect_stdout
from configparser import Error as ConfigError
from threading import Event
from types import SimpleNamespace
from typing import TYPE_CHECKING

from holopatcher import CURRENT_VERSION, ExitCode
from holopatcher.package import NamespaceSelectionError, load_package
from pykotor.tools.path import CaseAwarePath
from pykotor.tslpatcher.logger import PatchLogger
from pykotor.tslpatcher.patcher import ModInstaller
from pykotor.tslpatcher.reader import ConfigReader
from pykotor.tslpatcher.uninstall import ModUninstaller
from utility.system.path import Path

if TYPE_CHECKING:
    from argparse import Namespace
    from holopatcher.package import ModPackage


@contextmanager
def _operation_log(root, logger: PatchLogger):
    """Open the one package log before mutation; never substitute another location."""
    path = pathlib.Path(root) / "installlog.txt"
    if path.is_symlink():
        raise OSError(f"The operation log cannot be a symbolic link: {path}")
    with path.open("a", encoding="utf-8", buffering=1) as stream:
        def record(message):
            stream.write(message.formatted_message + "\n")
            stream.flush()
        observables = (logger.verbose_observable, logger.note_observable,
                       logger.warning_observable, logger.error_observable)
        for observable in observables:
            observable.subscribe(record)
        try:
            logger.add_note(f"HoloPatcher {CURRENT_VERSION}; operation log: {path}")
            yield
        finally:
            for observable in observables:
                observable.callbacks.remove(record)


@contextmanager
def _cooperative_cancellation(event: Event):
    """Stop between backend operations; a signal must not interrupt a file commit."""
    signals = [signal.SIGINT]
    if sys.platform == "win32":
        signals.append(signal.SIGBREAK)
    else:
        signals.append(signal.SIGTERM)
    previous = {}
    def request_stop(signum, frame):
        event.set()
    try:
        for signum in signals:
            previous[signum] = signal.signal(signum, request_stop)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _confirm(title: str, message: str, args: Namespace, *, ordinary: bool = True) -> bool:
    print(f"{title}\n{message}", file=sys.stderr, flush=True)
    if ordinary and args.yes:
        print("Consent acknowledged by --yes.", file=sys.stderr, flush=True)
        return True
    if args.non_interactive or sys.stdin is None or not sys.stdin.isatty():
        print("Consent not supplied; no input will be read. "
              + ("Use --yes for normal unattended consent." if ordinary else "This safety warning requires interactive review."),
              file=sys.stderr, flush=True)
        return False
    while True:
        print("Continue? [y/N] ", end="", file=sys.stderr, flush=True)
        answer = sys.stdin.readline()
        if not answer:
            return False
        answer = answer.strip().casefold()
        if answer in ("y", "yes"):
            return True
        if answer in ("", "n", "no"):
            return False
        print("Enter yes or no.", file=sys.stderr)


def _validate(package: ModPackage, index: int, logger: PatchLogger) -> ExitCode:
    try:
        reader = ConfigReader.from_filepath(package.changes_path(index), logger)
        reader.load(reader.config)
    except Exception as exc:
        logger.add_error(f"INI validation failed: {type(exc).__name__}: {exc}")
        return ExitCode.INSTALL_COMPLETED_WITH_ERRORS
    logger.add_note("INI syntax/configuration parsing finished. This is not a dry run: "
                    "destination paths, required resources, compilation, and installation success were not verified.")
    return ExitCode.INSTALL_COMPLETED_WITH_ERRORS if logger.errors else ExitCode.SUCCESS


def _install(package: ModPackage, index: int, game_path, args: Namespace,
             logger: PatchLogger, cancel: Event) -> ExitCode:
    installer = ModInstaller(package.root, game_path, package.changes_path(index), logger)
    if installer.game is None:
        logger.add_error(f"Not a recognized game/content installation: {game_path}")
        return ExitCode.ABORT_INSTALL_UNSAFE
    try:
        config = installer.config()
    except ImportError as exc:
        logger.add_error(str(exc))  # A configured required resource is absent.
        return ExitCode.ABORT_INSTALL_UNSAFE
    except Exception as exc:
        logger.add_error(f"Configuration loading failed: {type(exc).__name__}: {exc}")
        return ExitCode.INSTALL_COMPLETED_WITH_ERRORS
    message = config.confirm_message.strip()
    if message and message != "N/A" and not _confirm("This mod requires confirmation", message, args):
        logger.add_note("Installation declined; no game files were changed.")
        return ExitCode.ABORT_INSTALL_UNSAFE
    processed = 0
    def progress():
        nonlocal processed
        processed += 1
        # Plain complete lines work both in a terminal and behind another program.
        print(f"[Progress] {processed}/{logger.patches_configured}", file=sys.stderr, flush=True)
    try:
        with _cooperative_cancellation(cancel):
            installer.install(cancel, progress_update_func=progress)
    except ValueError as exc:
        logger.add_error(f"Installation rejected: {exc}")
        return ExitCode.ABORT_INSTALL_UNSAFE
    if cancel.is_set():
        logger.add_warning("Cancellation requested. Completed operations remain installed; review the log and backup.")
        return ExitCode.INTERRUPTED
    return ExitCode.INSTALL_COMPLETED_WITH_ERRORS if logger.patches_failed or logger.errors else ExitCode.SUCCESS


def _restore(package: ModPackage, game_path, args: Namespace,
             logger: PatchLogger, cancel: Event) -> ExitCode:
    refused = False
    def safety_question(title, message):
        nonlocal refused
        allowed = _confirm(title, message, args, ordinary=False)
        refused = not allowed
        return allowed
    dialogs = SimpleNamespace(
        showerror=lambda title, message: logger.add_error(f"{title}: {message}"),
        askyesno=safety_question,
    )
    uninstaller = ModUninstaller(Path(package.root, "backup"), Path(game_path), logger, dialogs=dialogs)
    try:
        folder, existing, files, _ = uninstaller.get_backup_info()
    except ValueError as exc:
        logger.add_error(f"Unsafe backup: {exc}")
        return ExitCode.ABORT_INSTALL_UNSAFE
    if folder is None:
        return ExitCode.ABORT_INSTALL_UNSAFE if refused else ExitCode.INSTALL_COMPLETED_WITH_ERRORS
    logger.add_note(f"Using backup '{folder}'.")
    if not _confirm("Restore this backup?",
                    f"Restore {len(files)} files and remove {len(existing)} installed files in '{game_path}'?\n"
                    f"Backup: {folder}\nRestore mods in reverse installation order. This is the latest package backup, "
                    "not a namespace-specific backup. The backup will be kept.", args):
        logger.add_note("Restoration declined; no game files were changed.")
        return ExitCode.ABORT_INSTALL_UNSAFE
    try:
        with _cooperative_cancellation(cancel):
            uninstaller.restore_backup(folder, existing, files, should_cancel=cancel)
    except Exception as exc:
        logger.add_error(f"Backup restoration did not complete: {type(exc).__name__}: {exc}. "
                         "Some earlier files may already have been restored. The backup is retained; do not delete it.")
        return ExitCode.INTERRUPTED if cancel.is_set() else ExitCode.INSTALL_COMPLETED_WITH_ERRORS
    logger.add_note(f"Backup restoration completed; backup retained at '{folder}'.")
    return ExitCode.INTERRUPTED if cancel.is_set() else ExitCode.SUCCESS


def execute_cli(args: Namespace) -> int:
    logger = PatchLogger()
    cancel = Event()
    result = ExitCode.EXCEPTION_DURING_INSTALL
    try:
        # Keep the namespace-list stdout as one JSON document; parser notes use stderr.
        with redirect_stdout(sys.stderr) if args.action == "list-namespaces" else nullcontext():
            package = load_package(args.tslpatchdata)
        index = package.select(args.namespace_option_index, args.namespace_id)
        if args.action == "list-namespaces":
            print(json.dumps({
                "package_root": str(package.root),
                "patch_data_root": str(package.data_root),
                "selected_index": index,
                "namespaces": [{"index": i, "id": namespace.namespace_id,
                                "name": namespace.name, "description": namespace.description,
                                "changes_ini": str(package.changes_path(i))}
                               for i, namespace in enumerate(package.namespaces)],
            }, ensure_ascii=True, indent=2))
            return ExitCode.SUCCESS
        game_path = None
        if args.action in {"install", "uninstall"}:
            game_path = CaseAwarePath.get_case_sensitive_path(pathlib.Path(args.game_dir).expanduser().absolute())
            if not game_path.is_dir():
                raise NotADirectoryError(f"Game/content directory does not exist: {game_path}")
        with _operation_log(package.root, logger):
            namespace = package.namespaces[index]
            logger.add_note(f"Package: {package.root}")
            logger.add_note(f"Option {index}: ID={namespace.namespace_id!r}, name={namespace.name!r}; INI: {package.changes_path(index)}")
            if game_path is not None:
                logger.add_note(f"Destination: {game_path}")
            try:
                if args.action == "validate":
                    result = _validate(package, index, logger)
                elif args.action == "install":
                    result = _install(package, index, game_path, args, logger, cancel)
                elif args.action == "uninstall":
                    result = _restore(package, game_path, args, logger, cancel)
                else:
                    raise ValueError(f"Unknown CLI operation: {args.action}")
            except KeyboardInterrupt:
                cancel.set()
                result = ExitCode.INTERRUPTED
                logger.add_warning("Interrupted; any previously completed operations may remain. Review the log and backup.")
            except Exception as exc:
                logger.add_error(f"{args.action} failed: {type(exc).__name__}: {exc}")
                result = ExitCode.EXCEPTION_DURING_INSTALL
            logger.add_note(f"Headless {args.action} finished with exit code {int(result)}.")
    except NamespaceSelectionError as exc:
        print(f"[Error] {exc}", file=sys.stderr)
        result = ExitCode.NAMESPACE_INDEX_OUT_OF_RANGE
    except (ConfigError, KeyError, ValueError) as exc:
        print(f"[Error] Configuration selection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        result = ExitCode.INSTALL_COMPLETED_WITH_ERRORS if args.action == "validate" else ExitCode.EXCEPTION_DURING_INSTALL
    except KeyboardInterrupt:
        print("[Warning] Interrupted; previously completed operations may remain.", file=sys.stderr)
        result = ExitCode.INTERRUPTED
    except Exception as exc:
        # Reporting does not depend on successfully opening or writing the package log.
        print(f"[Error] {args.action} did not complete: {type(exc).__name__}: {exc}", file=sys.stderr)
        result = ExitCode.EXCEPTION_DURING_INSTALL
    status = "success" if result == ExitCode.SUCCESS else "cancelled" if result == ExitCode.INTERRUPTED else "failed"
    counts = (f" configured={logger.patches_configured} completed={logger.patches_completed}"
              f" skipped={logger.patches_skipped} failed={logger.patches_failed}") if args.action == "install" else ""
    print(f"[Result] action={args.action} status={status} exit_code={int(result)}{counts}"
          f" warnings={len(logger.warnings)} errors={len(logger.errors)}", flush=True)
    return result
