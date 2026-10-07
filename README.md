# HoloPatcher

## Overview

HoloPatcher is a cross-platform TSLPatcher-compatible mod installer for Star Wars: Knights of the Old Republic and The Sith Lords. This repository contains the standalone frontend; patching and resource handling are provided by the PyKotOR backend checkout.


## Goals

- **Backwards Compatibility**: Match TSLPatcher's output and behavior as closely as possible.
- **Cross-platform compatible**: Windows/Mac/Linux will all produce the same patch results. HoloPatcher provides case-insensitive pathing support for all operating systems.
- **Add new features**: Add highly-requested features while still ensuring backwards compatibility with TSLPatcher.

## Features

- **Configurable Patching**: Offers a flexible system for defining modifications through INI files, allowing for detailed control over file modifications, additions, and compilations.
- **Game File Support**: Supports a wide range of game file types, including GFF, 2DA, TLK, SSF, and NCS/NSS scripts, enabling comprehensive modding capabilities.
- **Memory Management**: Implements a memory system for tracking and reusing modifications across different files, optimizing patching processes and ensuring consistency.
- **Error Handling and Logging**: Provides robust error handling and detailed logging functionality, aiding in debugging and ensuring smooth patching operations.
- **User Interface**: Features a graphical user interface for easy mod installation and management.
- **Command-line support**: Offer a command line for tools such as KOTORModSync and the KotOR plug-in for ModOrganizer 2.
- **Compiling NSS scripts into NCS bytecode without reliance on legacy tool nwnnsscomp.**

## Usage

For packaged builds, check this repository's [GitHub Actions](https://github.com/vrifftech/HoloPatcher/actions) for artifacts from successful builds. Releases, when published, will appear on this repository's [releases page](https://github.com/vrifftech/HoloPatcher/releases).

The primary PyInstaller artifacts are:

| Platform | Artifact |
| --- | --- |
| Windows x64 | `.exe` |
| Linux x86-64 | `.AppImage` |
| macOS Intel | `.app.zip` |
| macOS Apple Silicon | `.app.zip` |

Packaged executables are primarily GUI applications. The headless CLI documented below is available when running from source.

### Requirements

Running from source requires Python 3.10 or newer, a separate PyKotor checkout, and Tkinter for the GUI. From the HoloPatcher repository root, install the runtime dependencies:

```bash
python -m pip install -r requirements.txt
```

### Running HoloPatcher

Set `HOLOPATCHER_PYKOTOR_ROOT` to the PyKotor checkout's root directory (the directory containing `Libraries`). `PYKOTOR_ROOT` is accepted as a fallback.

**PowerShell:**

```powershell
$env:HOLOPATCHER_PYKOTOR_ROOT = "C:\path\to\PyKotor"
python .\run.py
```

**Bash:**

```bash
export HOLOPATCHER_PYKOTOR_ROOT="/path/to/PyKotor"
python ./run.py
```

### Command-Line Interface

With the backend selected as above, run commands from the HoloPatcher repository root:

```bash
python run.py --help
python run.py --backend-info
python run.py --list-namespaces --tslpatchdata "/path/to/mod"
python run.py --validate --tslpatchdata "/path/to/mod" --namespace-option-index 0
python run.py --install --game-dir "/path/to/game" --tslpatchdata "/path/to/mod" --namespace-option-index 0 --yes --non-interactive
python run.py --uninstall --game-dir "/path/to/game" --tslpatchdata "/path/to/mod" --yes --non-interactive
```

Choose one headless operation: `--install`, `--uninstall`, `--validate`, `--list-namespaces`, or `--backend-info`.

| Option | Description |
| --- | --- |
| `--install` | Install the selected option without opening the GUI. |
| `--uninstall` | Restore the latest package backup and retain it. This is not a namespace-specific restore; restore mods in reverse installation order. |
| `--validate` | Parse INI syntax and configuration. This is **not a dry-run installation** and does not verify destination paths, required resources, compilation, or installation success. |
| `--list-namespaces` | List namespace option indices, IDs, display names, and configuration paths as JSON. |
| `--backend-info` | Print the selected backend paths without importing Tkinter. |
| `--game-dir PATH` | Game installation or supported content directory. Required for installation and uninstallation. |
| `--tslpatchdata PATH` | Package root, `tslpatchdata` directory, namespace directory, or INI file. Required for installation, uninstallation, validation, and listing namespaces. |
| `--namespace-option-index INDEX` | Select a **zero-based** namespace option index. |
| `--namespace-id ID` | Select the namespace's **INI identifier**, not its display name. Use this or `--namespace-option-index`, not both. |
| `--yes` | Acknowledge ordinary install/restore confirmation only; does not override safety mismatches. |
| `--non-interactive` | Never prompt or read stdin. Combine with `--yes` for unattended ordinary confirmation. |
| `--console` | Keep the console when launching the source GUI; CLI operations always keep it. |
| `--version` | Print the version and exit. |

Paths are not universally required: `--backend-info`, `--version`, and `--help` need neither path, and validation and namespace listing do not require a game directory. `--yes` and `--non-interactive` apply to install, uninstall, validate, and list-namespaces operations, not GUI launch or `--backend-info`.

The legacy positional form `GameDir ModDir [NamespaceIndex]` is also supported:

```bash
python run.py "GameDir" "ModDir" 0 --install
```

### Graphical User Interface

Run `python run.py` without arguments from the repository root to launch the GUI. After installing this checkout with `python -m pip install .`, use `holopatcher` or `python -m holopatcher`, with the same backend selection.

## Configuration

Modifications are defined in INI files, which specify the files to be patched, the changes to be made, and any additional instructions required for the patching process. The system supports a variety of operations, including:

- Adding or modifying fields in GFF files.
- Inserting or modifying rows in 2DA files.
- Adding or [modifying](https://github.com/OpenKotOR/PyKotor/wiki/HoloPatcher-README-for-mod-developers#tlk-replacements) entries in TLK files.
- Compiling NSS scripts into NCS bytecode without reliance on nwnnsscomp.
- Modifying SSF sound files.

See the legacy TSLPatcher readme for the original configuration syntax; HoloPatcher-specific extensions are described in the developer documentation below.

## Extending

HoloPatcher is designed with extensibility in mind. Developers can extend its functionality by adding new types of modifications or integrating additional game file formats.

## Contributing

Contributions to the PyKotor's HoloPatcher are welcome. Whether it's adding new features, improving existing functionality, or fixing bugs, your contributions are appreciated.

## Further Documentation

For more detailed guides and tutorials on using HoloPatcher, refer to the following resources. These are upstream references; use the launch and CLI instructions above for this standalone checkout.

- [Installing Mods with HoloPatcher](https://github.com/OpenKotOR/PyKotor/wiki/Installing-Mods-with-HoloPatcher): A step-by-step tutorial on setting up and running HoloPatcher.
- [Advanced Configuration Options](https://github.com/OpenKotOR/PyKotor/wiki/HoloPatcher-README-for-mod-developers): Detailed descriptions of advanced features and how to use them.
- [Mod Creation Best Practices](https://github.com/OpenKotOR/PyKotor/wiki/Mod-Creation-Best-Practices): Guidelines and tips for creating mods with HoloPatcher.
- [Notes on Internal Workings](https://github.com/OpenKotOR/PyKotor/wiki/Explanations-on-HoloPatcher-Internal-Logic): Explanations on how HoloPatcher works internally and some key TSLPatcher logic.
