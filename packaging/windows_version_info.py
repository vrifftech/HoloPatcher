"""Build Windows VERSIONINFO from the frontend's existing release identity."""
from __future__ import annotations

from typing import TYPE_CHECKING

from packaging.version import Version  # Already required by PyInstaller.

if TYPE_CHECKING:
    from PyInstaller.utils.win32.versioninfo import VSVersionInfo


def make_version_info(current_version: str) -> VSVersionInfo:
    """Preserve the display version; zero-pad its release tuple for Windows.

    Prerelease/dev suffixes stay in the strings and set VS_FF_PRERELEASE; they
    do not occupy a numeric component. Reject releases Windows cannot encode.
    This module is a build helper, not a runtime application dependency.
    """
    display_version = current_version.strip()
    parsed = Version(display_version)
    if parsed.epoch or len(parsed.release) > 4 or any(part > 0xFFFF for part in parsed.release):
        raise ValueError(
            f"Cannot encode HoloPatcher version {current_version!r}: Windows requires "
            "at most four release components in 0..65535 and no version epoch."
        )
    numeric_version = parsed.release + (0,) * (4 - len(parsed.release))

    # Imported only when the Windows branch of the PyInstaller spec calls us.
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=numeric_version,
            prodvers=numeric_version,
            mask=0x3F,
            flags=0x02 if parsed.is_prerelease else 0x00,  # VS_FF_PRERELEASE
            OS=0x40004,  # VOS_NT_WINDOWS32
            fileType=0x01,  # VFT_APP
            subtype=0x00,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([
                StringTable("040904B0", [  # U.S. English, Unicode (1200).
                    StringStruct("CompanyName", "OpenKotOR"),
                    StringStruct("FileDescription", "HoloPatcher - KOTOR mod installer"),
                    StringStruct("FileVersion", display_version),
                    StringStruct("InternalName", "HoloPatcher"),
                    StringStruct("LegalCopyright", "OpenKotOR PyKotOR Tools"),
                    StringStruct("OriginalFilename", "HoloPatcher.exe"),
                    StringStruct("ProductName", "HoloPatcher"),
                    StringStruct("ProductVersion", display_version),
                ]),
            ]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )
