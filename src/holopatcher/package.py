"""Shared package/namespace selection. No UI, resource search, or installation."""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass

from pykotor.tools.path import CaseAwarePath
from pykotor.tslpatcher.namespaces import PatcherNamespace
from pykotor.tslpatcher.reader import ConfigReader, NamespaceReader
from utility.system.path import PurePath


class PackageError(ValueError):
    """The selected package or its declared paths are invalid."""


class NoPackageError(FileNotFoundError):
    """There is no default INI or catalogue at this location."""


class NamespaceSelectionError(PackageError):
    """The explicitly selected option does not identify one namespace."""


def resolve_package_file(root, relative_path) -> CaseAwarePath:
    raw = pathlib.PureWindowsPath(str(relative_path))
    if raw.drive or raw.root or ".." in raw.parts:
        raise PackageError(f"Invalid package-relative path: {relative_path}")
    path = CaseAwarePath.get_case_sensitive_path(root.joinpath(*raw.parts))
    pathlib.Path(path).resolve().relative_to(pathlib.Path(root).resolve())
    return path


@dataclass(frozen=True)
class ModPackage:
    root: CaseAwarePath
    data_root: CaseAwarePath
    namespaces: tuple[PatcherNamespace, ...]
    selected_index: int

    def select(self, index: int | None = None, namespace_id: str | None = None) -> int:
        if index is not None and namespace_id is not None:
            raise NamespaceSelectionError("Select an option by index or ID, not both.")
        if namespace_id is not None:
            matches = [i for i, namespace in enumerate(self.namespaces)
                       if namespace.namespace_id.casefold() == namespace_id.casefold()]
            if len(matches) != 1:
                raise NamespaceSelectionError(f"Namespace ID must identify exactly one option: {namespace_id!r}")
            return matches[0]
        selected = self.selected_index if index is None else index
        if not 0 <= selected < len(self.namespaces):
            raise NamespaceSelectionError(f"Namespace index {selected} is outside 0..{len(self.namespaces) - 1}.")
        return selected

    def changes_path(self, index: int) -> CaseAwarePath:
        return resolve_package_file(self.data_root, self.namespaces[index].changes_filepath())


def load_package(requested: os.PathLike | str) -> ModPackage:
    """Locate the declared package and retain inner-INI selection and outer roots."""
    selected = CaseAwarePath.get_case_sensitive_path(os.path.abspath(os.path.expanduser(requested)))
    if not selected.exists():
        raise FileNotFoundError(f"Mod package does not exist: {selected}")
    selected_ini = None
    if selected.is_file():
        if selected.name.casefold() != "namespaces.ini":
            selected_ini = selected
        selected = selected.parent
    shared = next((p for p in (selected, *selected.parents) if p.name.casefold() == "tslpatchdata"), None)
    if shared is not None:
        data_root = shared
        if selected_ini is None and selected != shared:
            selected_ini = CaseAwarePath.get_case_sensitive_path(selected / "changes.ini")
    else:
        child = CaseAwarePath.get_case_sensitive_path(selected / "tslpatchdata")
        data_root = child if child.is_dir() else selected
    root = data_root.parent if data_root.name.casefold() == "tslpatchdata" else selected
    catalogue = resolve_package_file(data_root, "namespaces.ini")
    changes = selected_ini or resolve_package_file(data_root, "changes.ini")
    selected_index = 0
    if catalogue.is_file():
        namespaces = NamespaceReader.from_filepath(catalogue)
        if not namespaces:
            raise PackageError("The package declares no namespace options.")
        for namespace in namespaces:
            path = resolve_package_file(data_root, namespace.changes_filepath())
            if not path.is_file():
                raise FileNotFoundError(f"Namespace INI not found: {path}")
            # Validate the declared information path without requiring or reading it.
            resolve_package_file(data_root, namespace.rtf_filepath())
        if selected_ini is not None:
            absolute = pathlib.Path(selected_ini).resolve()
            matches = [i for i, namespace in enumerate(namespaces)
                       if pathlib.Path(resolve_package_file(data_root, namespace.changes_filepath())).resolve() == absolute]
            if len(matches) != 1:
                raise NamespaceSelectionError("The selected INI does not identify exactly one declared namespace option.")
            selected_index = matches[0]
    elif changes.is_file():
        pathlib.Path(changes).resolve().relative_to(pathlib.Path(data_root).resolve())
        reader = ConfigReader.from_filepath(changes)
        reader.load_settings()
        namespace = PatcherNamespace.from_default()
        namespace.name = reader.config.window_title or changes.parents[1].name.strip() or "<< Untitled Mod Loaded >>"
        relative = pathlib.Path(changes).resolve().relative_to(pathlib.Path(data_root).resolve())
        namespace.ini_filename = relative.name
        namespace.data_folderpath = PurePath(relative.parent)
        namespaces = [namespace]
    else:
        raise NoPackageError(f"No changes.ini or namespaces.ini found in: {data_root}")
    return ModPackage(CaseAwarePath(root), CaseAwarePath(data_root), tuple(namespaces), selected_index)
