from __future__ import annotations

import os
import sys
import tempfile
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING

from holopatcher.config import CURRENT_VERSION
from pykotor.common.misc import Game
from pykotor.common.stream import BinaryReader
from pykotor.extract.file import ResourceIdentifier
from pykotor.tools.encoding import decode_bytes_with_fallbacks
from pykotor.tools.path import CaseAwarePath, find_kotor_paths_from_default
from pykotor.tslpatcher.config import LogLevel
from pykotor.tslpatcher.patcher import ModInstaller, is_capsule_file
from pykotor.tslpatcher.reader import ConfigReader, NamespaceReader
from pykotor.tslpatcher.uninstall import ModUninstaller
from utility.string_util import striprtf

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Callable
    from datetime import timedelta
    from threading import Event

    from pykotor.tslpatcher.logger import PatchLog, PatchLogger
    from pykotor.tslpatcher.namespaces import PatcherNamespace

VERSION_LABEL = f"v{CURRENT_VERSION}"


class ExitCode(IntEnum):
    SUCCESS = 0
    UNKNOWN_STARTUP_ERROR = 1
    NUMBER_OF_ARGS = 2
    NAMESPACES_INI_NOT_FOUND = 3
    NAMESPACE_INDEX_OUT_OF_RANGE = 4
    CHANGES_INI_NOT_FOUND = 5
    ABORT_INSTALL_UNSAFE = 6
    EXCEPTION_DURING_INSTALL = 7
    INSTALL_COMPLETED_WITH_ERRORS = 8
    CRASH = 9
    CLOSE_FOR_UPDATE_PROCESS = 10


class HoloPatcherError(Exception): ...


@dataclass
class ModInfo:
    """Information about a loaded mod."""

    mod_path: str
    namespaces: list[PatcherNamespace]
    config_reader: ConfigReader | None


@dataclass
class NamespaceInfo:
    """Information about a selected namespace."""

    config_reader: ConfigReader
    log_level: LogLevel
    game_number: int | None
    game_paths: list[str]
    info_content: str | None


@dataclass
class InstallResult:
    """Result of a mod installation."""

    install_time: timedelta
    num_errors: int
    num_warnings: int
    num_patches: int


def is_frozen() -> bool:
    """Check if running as a frozen executable."""
    return (
        getattr(sys, "frozen", False)
        or getattr(sys, "_MEIPASS", False)
        or tempfile.gettempdir() in sys.executable
    )


def is_running_from_temp() -> bool:
    """Check if running from a temporary directory."""
    app_path = Path(sys.executable)
    temp_dir = tempfile.gettempdir()
    return str(app_path).startswith(temp_dir)


def get_namespace_description(
    namespaces: list[PatcherNamespace],
    selected_namespace_name: str,
) -> str:
    """Get the description for a namespace by name."""
    namespace_option: PatcherNamespace | None = next(
        (x for x in namespaces if x.name == selected_namespace_name),
        None,
    )
    return namespace_option.description if namespace_option else ""


def load_mod(
    directory_path: os.PathLike | str,
) -> ModInfo:
    """Load a mod from a directory.

    Args:
    ----
        directory_path: Path to mod directory (or tslpatchdata subdirectory)

    Returns:
    -------
        ModInfo: Information about the loaded mod

    Raises:
    ------
        FileNotFoundError: If no valid mod found at path
    """
    tslpatchdata_path = CaseAwarePath(directory_path, "tslpatchdata")
    if not tslpatchdata_path.is_dir() and tslpatchdata_path.parent.name.lower() == "tslpatchdata":
        tslpatchdata_path = tslpatchdata_path.parent

    mod_path = str(tslpatchdata_path.parent)
    namespace_path: CaseAwarePath = tslpatchdata_path / "namespaces.ini"
    changes_path: CaseAwarePath = tslpatchdata_path / "changes.ini"

    namespaces: list[PatcherNamespace]
    config_reader: ConfigReader | None = None

    if namespace_path.is_file():
        namespaces = NamespaceReader.from_filepath(namespace_path)
    elif changes_path.is_file():
        config_reader = ConfigReader.from_filepath(
            changes_path, tslpatchdata_path=tslpatchdata_path
        )
        namespaces = [config_reader.config.as_namespace(changes_path)]
    else:
        raise FileNotFoundError(f"No namespaces.ini or changes.ini found in {tslpatchdata_path}")

    return ModInfo(mod_path, namespaces, config_reader)


def load_namespace_config(
    mod_path: str,
    namespaces: list[PatcherNamespace],
    selected_namespace_name: str,
    *,
    config_reader: ConfigReader | None = None,
) -> NamespaceInfo:
    """Load configuration for a specific namespace.

    Args:
    ----
        mod_path: Path to mod directory
        namespaces: List of available namespaces
        selected_namespace_name: Name of namespace to load
        config_reader: Optional pre-loaded ConfigReader

    Returns:
    -------
        NamespaceInfo: Configuration and metadata for the namespace

    Raises:
    ------
        ValueError: If namespace not found
    """
    namespace_option: PatcherNamespace | None = next(
        (x for x in namespaces if x.name == selected_namespace_name),
        None,
    )
    if namespace_option is None:
        raise ValueError(f"Namespace '{selected_namespace_name}' not found in namespaces list")
    changes_ini_path = CaseAwarePath(mod_path, "tslpatchdata", namespace_option.changes_filepath())
    tslpatchdata_path = CaseAwarePath(mod_path, "tslpatchdata")
    reader: ConfigReader = config_reader or ConfigReader.from_filepath(
        changes_ini_path, tslpatchdata_path=tslpatchdata_path
    )
    reader.load_settings()

    game_number: int | None = reader.config.game_number
    game: Game | None = Game(game_number) if game_number else None
    game_paths: list[str] = (
        [
            str(path)
            for game_key in ([game] + ([Game.K1] if game is not None and game == Game.K2 else []))
            for path in (find_kotor_paths_from_default()[game_key] if game_key is not None else [])
        ]
        if game_number
        else []
    )

    info_rtf_path = CaseAwarePath(mod_path, "tslpatchdata", namespace_option.rtf_filepath())
    info_rte_path = info_rtf_path.with_suffix(".rte")

    info_content: str | None = None
    if info_rte_path.is_file():
        data: bytes = BinaryReader.load_file(info_rte_path)
        info_content = decode_bytes_with_fallbacks(data, errors="replace")
    elif info_rtf_path.is_file():
        data = BinaryReader.load_file(info_rtf_path)
        rtf_text = decode_bytes_with_fallbacks(data, errors="replace")
        info_content = striprtf(rtf_text)

    return NamespaceInfo(reader, reader.config.log_level, game_number, game_paths, info_content)


def validate_game_directory(
    directory_path: os.PathLike | str,
) -> str:
    """Validate a KOTOR game directory.

    Args:
    ----
        directory_path: Path to validate

    Returns:
    -------
        str: Validated directory path

    Raises:
    ------
        ValueError: If directory is invalid
    """
    directory = CaseAwarePath(directory_path)
    if not directory.is_dir():
        raise ValueError(f"Invalid KOTOR directory: {directory_path}")
    return str(directory)


def check_directory_access(
    directory: Path,
    *,
    recurse: bool = False,
    should_filter: bool = False,
) -> bool:
    """Check if directory is accessible.

    Args:
    ----
        directory: Directory to check
        recurse: Check recursively if True
        should_filter: Filter by valid resource types if True

    Returns:
    -------
        bool: True if accessible
    """
    if should_filter:

        def filter_func(x: Path) -> bool:
            return not ResourceIdentifier.from_path(x).restype.is_invalid

        return directory.has_access(recurse=recurse, filter_results=filter_func)

    return directory.has_access(recurse=recurse)


def validate_install_paths(
    mod_path: str,
    game_path: str,
) -> bool:
    """Validate that mod and game paths are ready for installation.

    Args:
    ----
        mod_path: Path to mod directory
        game_path: Path to game directory

    Returns:
    -------
        bool: True if paths are valid
    """
    return (
        bool(mod_path)
        and CaseAwarePath(mod_path).is_dir()
        and bool(game_path)
        and CaseAwarePath(game_path).is_dir()
    )


def parse_args() -> Namespace:
    """Parse command line arguments.

    Returns:
    -------
        Namespace: Parsed command line arguments
    """
    import os

    from argparse import ArgumentParser
    from pathlib import Path

    def _get_invocation_command() -> str:
        """Get the actual command used to invoke the CLI."""
        if not sys.argv:
            return "holopatcher"

        # Try to detect if we're being run via "uv run" by checking parent process
        is_uv_run = False
        try:
            import psutil  # type: ignore[import-untyped]

            current_process = psutil.Process()
            parent = current_process.parent()
            if parent and "uv" in parent.name().lower():
                is_uv_run = True
        except (ImportError, Exception):  # noqa: BLE001
            # psutil not available or can't access parent process
            # Try alternative detection: check if UV_* env vars exist
            if any("UV" in k.upper() for k in os.environ.keys()):
                is_uv_run = True

        script_path = Path(sys.argv[0]).resolve()
        cwd = Path.cwd().resolve()

        # Try to make path relative to current directory
        try:
            rel_script = script_path.relative_to(cwd)
            rel_script_str = str(rel_script).replace(
                "\\", "/"
            )  # Use forward slashes for consistency
        except ValueError:
            rel_script_str = str(script_path)

        # If detected as uv run, prefix with "uv run"
        if is_uv_run:
            return f"uv run {rel_script_str}"

        # Check for "python -m" pattern
        if len(sys.argv) >= 3 and sys.argv[1] == "-m":
            # python -m holopatcher
            return f"python -m {sys.argv[2]}"

        # Check if we're being run via python (not as a module)
        python_exe = Path(sys.executable).name.lower()
        if python_exe in ("python", "python3", "python.exe", "python3.exe", "py", "py.exe"):
            # python script.py
            return f"python {rel_script_str}"

        # For direct execution, return the relative path
        return rel_script_str

    prog = _get_invocation_command()
    parser = ArgumentParser(prog=prog, description="HoloPatcher CLI")

    parser.add_argument("--game-dir", type=str, help="Path to game directory")
    parser.add_argument("--tslpatchdata", type=str, help="Path to tslpatchdata")
    parser.add_argument("--namespace-option-index", type=int, help="Namespace option index")
    parser.add_argument(
        "--console", action="store_true", help="Show the console when launching HoloPatcher."
    )
    parser.add_argument("--uninstall", action="store_true", help="Uninstalls the selected mod.")
    parser.add_argument(
        "--install", action="store_true", help="Starts an install immediately on launch."
    )
    parser.add_argument(
        "--validate", action="store_true", help="Starts validation of the selected mod."
    )

    kwargs, positional = parser.parse_known_args()

    required_number_of_positional_args = 2
    max_positional_args = 3  # sourcery skip: move-assign

    number_of_positional_args = len(positional)
    if number_of_positional_args == required_number_of_positional_args:
        kwargs.game_dir = positional[0]
        kwargs.tslpatchdata = positional[1]
    if number_of_positional_args == max_positional_args:
        kwargs.namespace_option_index = positional[2]
    if kwargs.namespace_option_index:
        try:
            kwargs.namespace_option_index = int(kwargs.namespace_option_index)
        except ValueError as e:
            print((e.__class__.__name__, str(e)), file=sys.stderr)  # noqa: T201
            print(
                f"Invalid namespace_option_index. It should be an integer, got {kwargs.namespace_option_index}",
                file=sys.stderr,
            )  # noqa: T201
            sys.exit(ExitCode.NAMESPACE_INDEX_OUT_OF_RANGE)

    return kwargs


def calculate_total_patches(installer: ModInstaller) -> int:
    """Calculate total number of patches for progress calculation.

    Args:
    ----
        installer: ModInstaller instance

    Returns:
    -------
        int: Total number of patches
    """
    return len(
        [
            *installer.config().install_list,  # NOTE: TSLPatcher executes [InstallList] after [TLKList]
            *installer.get_tlk_patches(installer.config()),
            *installer.config().patches_2da,
            *installer.config().patches_gff,
            *installer.config().patches_nss,
            *installer.config().patches_ncs,  # NOTE: TSLPatcher executes [CompileList] after [HACKList]
            *installer.config().patches_ssf,
        ]
    )


def get_confirm_message(installer: ModInstaller) -> str | None:
    """Get confirmation message if mod requires it.

    Args:
    ----
        installer: ModInstaller instance

    Returns:
    -------
        str | None: Confirmation message if required, None otherwise
    """
    msg = installer.config().confirm_message.strip()
    return msg if msg and msg != "N/A" else None


_CANONICAL_LOWERCASE_GAME_ROOT_DIRS = {"modules", "override"}


def _split_relative_path(path_value: str) -> list[str]:
    normalized_path = CaseAwarePath.str_norm(path_value, slash="/")
    if normalized_path in {"", "."}:
        return []
    return [part for part in normalized_path.split("/") if part and part != "."]


def _join_relative_path(parts: list[str], slash: str | None = None) -> str:
    if not parts:
        return "."

    output_slash = os.sep if slash is None else slash
    if output_slash not in {"\\", "/"}:
        raise ValueError(f"Invalid slash str: '{output_slash}'")
    return output_slash.join(parts)


def _case_matching_existing_directory(parent: Path, directory_name: str) -> str | None:
    if directory_name in {"", ".", ".."} or not parent.is_dir():
        return None

    matches: list[str] = []
    try:
        for child in parent.iterdir():
            if child.name.lower() == directory_name.lower() and child.is_dir():
                matches.append(child.name)
    except OSError:
        return None

    if not matches:
        return None

    lower_name = directory_name.lower()
    for candidate in matches:
        if candidate == lower_name:
            return candidate
    for candidate in matches:
        if candidate == directory_name:
            return candidate
    return sorted(matches, key=lambda item: (item.lower() != lower_name, item))[0]


def _relative_parts_under_directory(
    path_value: os.PathLike | str,
    directory: os.PathLike | str,
) -> list[str] | None:
    path_abs = os.path.abspath(os.fspath(path_value))
    directory_abs = os.path.abspath(os.fspath(directory))

    try:
        common_path = os.path.commonpath([path_abs.lower(), directory_abs.lower()])
    except ValueError:
        return None

    if common_path != directory_abs.lower():
        return None

    relative_path = os.path.relpath(path_abs, directory_abs)
    if relative_path in {"", "."}:
        return []
    if relative_path == os.pardir or relative_path.startswith(os.pardir + os.sep):
        return None
    return _split_relative_path(relative_path)


def _directory_case_key(parts: list[str]) -> tuple[str, ...]:
    return tuple(part.lower() for part in parts)


class _DirectoryCaseResolver:
    """Resolve directory casing generically without forcing new directories lowercase.

    Existing directories always win, regardless of which directory name a mod
    instruction uses. For directories that do not exist yet, preserve the first
    casing seen for that relative directory path and reuse it for later patch
    entries. This prevents one entry from using ``CustomFolder`` while another
    entry writes to ``customfolder`` during the same install, without globally
    lowercasing custom directory names.
    """

    def __init__(self, game_root: os.PathLike | str):
        self.game_root = Path(CaseAwarePath(game_root))
        self._planned_directory_casing: dict[tuple[str, ...], str] = {}

    def normalize_relative_output_path(
        self,
        path_value: str,
        base_directory: os.PathLike | str,
        *,
        lowercase_leaf: bool,
        leaf_is_directory: bool,
        path_separator: str | None = None,
    ) -> str:
        """Normalize an output path without blindly lowercasing directory names.

        Every directory component is matched case-insensitively against existing
        directories under its parent. This is generic: it applies to arbitrary
        directories, not only to ``modules`` and ``override``. New custom
        directories keep their authored casing, with the first casing seen for
        that relative path reused throughout the install. The returned path uses
        the requested separator, or the current platform separator by default.
        """
        parts = _split_relative_path(path_value)
        if not parts:
            return "."

        base_path = Path(CaseAwarePath(base_directory))
        base_parts = self._resolve_base_directory_parts(base_path)
        current_path = (
            self._path_from_game_root(base_parts) if base_parts is not None else base_path
        )
        parent_parts = list(base_parts) if base_parts is not None else None
        directory_part_count = len(parts) if leaf_is_directory else max(len(parts) - 1, 0)

        normalized_parts: list[str] = []
        for part in parts[:directory_part_count]:
            is_game_root_child = parent_parts == []
            resolved_part = self._resolve_directory_part(
                current_path,
                parent_parts,
                part,
                is_game_root_child=is_game_root_child,
            )
            normalized_parts.append(resolved_part)
            if parent_parts is not None:
                parent_parts.append(resolved_part)
            current_path = current_path / resolved_part

        if not leaf_is_directory:
            leaf_name = parts[-1].lower() if lowercase_leaf else parts[-1]
            normalized_parts.append(leaf_name)

        return _join_relative_path(normalized_parts, path_separator)

    def _resolve_base_directory_parts(self, base_path: Path) -> list[str] | None:
        raw_base_parts = _relative_parts_under_directory(base_path, self.game_root)
        if raw_base_parts is None:
            return None

        resolved_base_parts: list[str] = []
        current_path = self.game_root
        for part in raw_base_parts:
            resolved_part = self._resolve_directory_part(
                current_path,
                resolved_base_parts,
                part,
                is_game_root_child=not resolved_base_parts,
            )
            resolved_base_parts.append(resolved_part)
            current_path = current_path / resolved_part
        return resolved_base_parts

    def _resolve_directory_part(
        self,
        current_path: Path,
        parent_parts: list[str] | None,
        part: str,
        *,
        is_game_root_child: bool,
    ) -> str:
        resolved_part = _case_matching_existing_directory(current_path, part)
        planned_key = (
            _directory_case_key([*parent_parts, part])
            if parent_parts is not None
            else None
        )

        if resolved_part is None and planned_key is not None:
            resolved_part = self._planned_directory_casing.get(planned_key)
        if (
            resolved_part is None
            and is_game_root_child
            and part.lower() in _CANONICAL_LOWERCASE_GAME_ROOT_DIRS
        ):
            resolved_part = part.lower()
        if resolved_part is None:
            resolved_part = part

        if planned_key is not None:
            self._planned_directory_casing[planned_key] = resolved_part
        return resolved_part

    def _path_from_game_root(self, parts: list[str]) -> Path:
        path = self.game_root
        for part in parts:
            path = path / part
        return path


def _normalize_install_destination(
    destination: str,
    game_root: os.PathLike | str,
    directory_case_resolver: _DirectoryCaseResolver | None = None,
) -> str:
    resolver = directory_case_resolver or _DirectoryCaseResolver(game_root)
    destination_is_capsule = is_capsule_file(destination)
    return resolver.normalize_relative_output_path(
        destination,
        game_root,
        lowercase_leaf=destination_is_capsule,
        leaf_is_directory=not destination_is_capsule,
        path_separator=os.sep,
    )


def _normalize_install_saveas(
    saveas: str,
    game_root: os.PathLike | str,
    destination: str,
    directory_case_resolver: _DirectoryCaseResolver | None = None,
) -> str:
    resolver = directory_case_resolver or _DirectoryCaseResolver(game_root)
    if is_capsule_file(destination):
        return resolver.normalize_relative_output_path(
            saveas,
            game_root,
            lowercase_leaf=True,
            leaf_is_directory=False,
            path_separator=os.sep,
        )

    destination_base = Path(CaseAwarePath(game_root))
    if destination != ".":
        destination_base = destination_base / destination
    return resolver.normalize_relative_output_path(
        saveas,
        destination_base,
        lowercase_leaf=True,
        leaf_is_directory=False,
        path_separator=os.sep,
    )


def force_lowercase_install_filenames(
    installer: ModInstaller,
    logger: PatchLogger | None = None,
) -> int:
    """Normalize installer output filenames without forcing directory casing.

    The native Linux KotOR II executable resolves loose files using lowercase
    filenames. TSLPatcher mods are often authored on case-insensitive
    filesystems and may request mixed-case output paths; normalize output
    filenames before PyKotor writes files or archive resources.

    Directory components are not blindly lowercased. Instead, every directory
    component is matched case-insensitively against existing directories under
    its parent, and the already-existing casing is reused. This applies to any
    directory, not only to ``modules`` and ``override``. Directories that the
    mod creates keep the first casing seen for that relative directory path, so
    later mod instructions cannot introduce a second case variant accidentally.
    Normalized installer path strings use the current platform separator, so
    Windows installs keep backslash-style TSLPatcher paths.

    Source filenames are intentionally left unchanged so mixed-case mod packages
    can still be read through CaseAwarePath.

    Args:
    ----
        installer: The installer whose loaded config should be normalized.
        logger: Optional patch logger for install-log notes.

    Returns:
    -------
        int: Number of patch entries whose output path or filename changed.
    """
    config = installer.config()
    game_root = getattr(installer, "game_path", ".")
    directory_case_resolver = _DirectoryCaseResolver(game_root)
    patches = [
        *config.install_list,
        config.patches_tlk,
        *config.patches_2da,
        *config.patches_gff,
        *config.patches_nss,
        *config.patches_ncs,
        *config.patches_ssf,
    ]

    changed_entries = 0
    for patch in patches:
        changed_fields: list[str] = []

        destination = getattr(patch, "destination", None)
        if isinstance(destination, str):
            normalized_destination = _normalize_install_destination(
                destination, game_root, directory_case_resolver
            )
            if destination != normalized_destination:
                setattr(patch, "destination", normalized_destination)
                changed_fields.append(
                    f"destination: '{destination}' -> '{normalized_destination}'"
                )
                destination = normalized_destination

        saveas = getattr(patch, "saveas", None)
        if isinstance(saveas, str):
            normalized_saveas = _normalize_install_saveas(
                saveas,
                game_root,
                destination if isinstance(destination, str) else ".",
                directory_case_resolver,
            )
            if saveas != normalized_saveas:
                setattr(patch, "saveas", normalized_saveas)
                changed_fields.append(f"saveas: '{saveas}' -> '{normalized_saveas}'")

        if changed_fields:
            changed_entries += 1
            if logger is not None:
                logger.add_verbose(
                    f"Normalizing {patch.__class__.__name__} output name(s): "
                    + "; ".join(changed_fields)
                )

    if changed_entries and logger is not None:
        logger.add_note(
            f"Normalized output filenames and preserved directory casing for "
            f"{changed_entries} patch {'entry' if changed_entries == 1 else 'entries'}."
        )

    return changed_entries


def install_mod(
    mod_path: str,
    game_path: str,
    namespaces: list[PatcherNamespace],
    selected_namespace_name: str,
    logger: PatchLogger,
    should_cancel: Event,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> InstallResult:
    """Install a mod.

    Args:
    ----
        mod_path: Path to mod directory
        game_path: Path to game directory
        namespaces: List of available namespaces
        selected_namespace_name: Name of namespace to install
        logger: Logger instance
        should_cancel: Event to signal cancellation
        progress_callback: Optional callback for progress updates

    Returns:
    -------
        InstallResult: Installation results

    Raises:
    ------
        Exception: If installation fails
    """
    namespace_option: PatcherNamespace | None = next(
        (x for x in namespaces if x.name == selected_namespace_name),
        None,
    )
    if namespace_option is None:
        raise ValueError(f"Namespace '{selected_namespace_name}' not found in namespaces list")
    tslpatchdata_path = CaseAwarePath(mod_path, "tslpatchdata")
    ini_file_path = tslpatchdata_path.joinpath(namespace_option.changes_filepath())
    namespace_mod_path: CaseAwarePath = ini_file_path.parent

    installer = ModInstaller(namespace_mod_path, game_path, ini_file_path, logger)
    installer.tslpatchdata_path = tslpatchdata_path
    force_lowercase_install_filenames(installer, logger)

    install_start_time: datetime = datetime.now(timezone.utc).astimezone()
    installer.install(should_cancel, progress_callback)
    lowercase_directory(game_path, logger, include_root=False, log_each=False)
    total_install_time: timedelta = datetime.now(timezone.utc).astimezone() - install_start_time

    num_errors: int = len(logger.errors)
    num_warnings: int = len(logger.warnings)
    num_patches: int = installer.config().patch_count()

    time_str = format_install_time(total_install_time)
    logger.add_note(
        f"The installation is complete with {num_errors} errors and {num_warnings} warnings.{os.linesep}"
        f"Total install time: {time_str}{os.linesep}"
        f"Total patches: {num_patches}",
    )

    return InstallResult(total_install_time, num_errors, num_warnings, num_patches)


def validate_config(
    mod_path: str,
    namespaces: list[PatcherNamespace],
    selected_namespace_name: str,
    logger: PatchLogger,
) -> None:
    """Validate a mod's configuration.

    Args:
    ----
        mod_path: Path to mod directory
        namespaces: List of available namespaces
        selected_namespace_name: Name of namespace to validate
        logger: Logger instance

    Raises:
    ------
        ValueError: If namespace not found
        Exception: If validation fails
    """
    namespace_option: PatcherNamespace | None = next(
        (x for x in namespaces if x.name == selected_namespace_name),
        None,
    )
    if namespace_option is None:
        raise ValueError(f"Namespace '{selected_namespace_name}' not found in namespaces list")
    ini_file_path = CaseAwarePath(mod_path, "tslpatchdata", namespace_option.changes_filepath())
    tslpatchdata_path = CaseAwarePath(mod_path, "tslpatchdata")

    reader = ConfigReader.from_filepath(ini_file_path, logger, tslpatchdata_path=tslpatchdata_path)
    reader.load(reader.config)


def uninstall_mod(
    mod_path: str,
    game_path: str,
    logger: PatchLogger,
) -> bool:
    """Uninstall a mod using its backup.

    Args:
    ----
        mod_path: Path to mod directory
        game_path: Path to game directory
        logger: Logger instance

    Returns:
    -------
        bool: True if uninstall completed fully

    Raises:
    ------
        FileNotFoundError: If backup folder not found
    """
    backup_parent_folder = Path(mod_path, "backup")
    if not backup_parent_folder.is_dir():
        raise FileNotFoundError(f"Backup folder not found: {backup_parent_folder}")

    uninstaller = ModUninstaller(backup_parent_folder, Path(game_path), logger)
    return uninstaller.uninstall_selected_mod()


def _same_filesystem_entry(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and left.samefile(right)
    except OSError:
        return False


def _rename_path_to_lowercase(
    path: Path,
    logger: PatchLogger,
    *,
    log_each: bool = True,
) -> bool:
    lowercase_path = path.with_name(path.name.lower())
    if path.name == lowercase_path.name:
        return False

    try:
        target_exists = lowercase_path.exists()
        target_is_same_entry = target_exists and _same_filesystem_entry(path, lowercase_path)
        if target_exists and not target_is_same_entry:
            logger.add_error(
                f"Cannot rename '{path}' to '{lowercase_path.name}' because the lowercase target "
                "already exists. Resolve this filename collision manually."
            )
            return False

        if log_each:
            logger.add_note(f"Renaming {path} to '{lowercase_path.name}'")

        if target_is_same_entry:
            temp_path = path.with_name(f".holopatcher_case_tmp_{uuid.uuid4().hex}")
            path.rename(temp_path)
            temp_path.rename(lowercase_path)
        else:
            path.rename(lowercase_path)
    except OSError as e:
        logger.add_error(
            f"Could not rename '{path}' to '{lowercase_path.name}': "
            f"{e.__class__.__name__}: {e}"
        )
        return False
    return True


def lowercase_directory(
    directory: str,
    logger: PatchLogger,
    *,
    include_root: bool = False,
    include_directories: bool = False,
    log_each: bool = True,
) -> bool:
    """Convert files in a directory tree to lowercase.

    Args:
    ----
        directory: Directory to process
        logger: Logger instance
        include_root: Whether to lowercase the selected directory itself. Ignored
            unless include_directories is also True.
        include_directories: Whether to also lowercase directories. This is off
            by default so install cleanup does not rename game folders such as
            modules/override.
        log_each: Whether to add a note for each renamed filesystem entry

    Returns:
    -------
        bool: True if any changes were made
    """
    directory_path = Path(CaseAwarePath(directory))
    made_change = False
    rename_count = 0
    for root, dirs, files in os.walk(str(directory_path), topdown=False):
        for file_name in files:
            file_path: Path = Path(root, file_name)
            if _rename_path_to_lowercase(file_path, logger, log_each=log_each):
                made_change = True
                rename_count += 1

        if include_directories:
            for folder_name in dirs:
                dir_path: Path = Path(root, folder_name)
                if _rename_path_to_lowercase(dir_path, logger, log_each=log_each):
                    made_change = True
                    rename_count += 1

    if (
        include_directories
        and include_root
        and _rename_path_to_lowercase(directory_path, logger, log_each=log_each)
    ):
        made_change = True
        rename_count += 1

    if rename_count and not log_each:
        entry_type = "filesystem entry" if include_directories else "file"
        logger.add_note(
            f"Lowercased {rename_count} {entry_type}"
            f"{'' if rename_count == 1 else 's'} under '{directory_path}'."
        )

    return made_change


def gain_directory_access(
    directory: str,
    logger: PatchLogger,
) -> tuple[bool, int, int]:
    """Attempt to gain access to a directory.

    Args:
    ----
        directory: Directory to fix permissions for
        logger: Logger instance

    Returns:
    -------
        tuple[bool, int, int]: (success, num_files, num_folders)

    Raises:
    ------
        PermissionError: If access cannot be gained
    """
    path: Path = Path(directory)
    access: bool = path.gain_access(recurse=True, log_func=logger.add_verbose)
    if not access:
        raise PermissionError(f"Permission denied to {directory}")

    num_files = 0
    num_folders = 0
    if path.is_dir():
        for entry in path.rglob("*"):
            if entry.is_file():
                num_files += 1
            elif entry.is_dir():
                num_folders += 1

    return (True, num_files, num_folders)


def get_log_file_path(mod_path: str) -> Path:
    """Get the log file path for a mod.

    Args:
    ----
        mod_path: Path to mod directory

    Returns:
    -------
        Path: Path to log file
    """
    return Path(mod_path) / "installlog.txt"


def write_log_entry(
    log: PatchLog,
    mod_path: str,
    log_level: LogLevel,
) -> None:
    """Write a log entry to file with level filtering.

    Args:
    ----
        log: PatchLog object to write
        mod_path: Path to mod directory
        log_level: Current log level setting
    """
    from loggerplus import RobustLogger
    from pykotor.tslpatcher.logger import LogType

    def log_type_to_level() -> LogType:
        log_map: dict[LogLevel, LogType] = {
            LogLevel.ERRORS: LogType.WARNING,
            LogLevel.GENERAL: LogType.WARNING,
            LogLevel.FULL: LogType.VERBOSE,
            LogLevel.WARNINGS: LogType.NOTE,
            LogLevel.NOTHING: LogType.WARNING,
        }
        return log_map[log_level]

    log_file_path = get_log_file_path(mod_path)
    try:
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        with log_file_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"{log.formatted_message}\n")
        if log.log_type.value < log_type_to_level().value:
            return
    except OSError as e:
        RobustLogger().error(
            f"Failed to write the log file at '{log_file_path}': {e.__class__.__name__}: {e}"
        )


def format_install_time(install_time: timedelta) -> str:
    """Format an installation time as a human-readable string.

    Args:
    ----
        install_time: Time duration

    Returns:
    -------
        str: Formatted time string
    """
    days, remainder = divmod(install_time.total_seconds(), 24 * 60 * 60)
    hours, remainder = divmod(remainder, 60 * 60)
    minutes, seconds = divmod(remainder, 60)

    return (
        f"{f'{int(days)} days, ' if days else ''}"
        f"{f'{int(hours)} hours, ' if hours else ''}"
        f"{f'{int(minutes)} minutes, ' if minutes or not (days or hours) else ''}"
        f"{int(seconds)} seconds"
    )
