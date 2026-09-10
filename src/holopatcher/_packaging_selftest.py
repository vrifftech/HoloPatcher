"""Explicit, noninteractive packaging diagnostic; never run during normal startup.

This creates only empty GUI widgets in a temporary directory. It does not install
mods, change permissions, or download anything.
"""
from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
import sys
import tempfile


def main() -> int:
    from holopatcher.bootstrap import bootstrap_backend
    bootstrap_backend()
    # PLY needs the packaged grammar source files for runtime introspection.
    # Compile without writing to the application bundle or a game directory.
    from ply import yacc
    from pykotor.common.misc import Game
    from pykotor.resource.formats.ncs import bytes_ncs, compile_nss, read_ncs
    for game in (Game.K1, Game.K2):
        compiled = compile_nss("void main() { int value = 1; }", game, errorlog=yacc.NullLogger())
        if not read_ncs(bytes_ncs(compiled)).instructions:
            raise RuntimeError(f"NSS compilation produced no instructions for {game}")
    import tkinter as tk
    from holopatcher.app import App
    from utility.tkinter.rte_editor import RichTextEditor
    from utility.system.path import Path as UtilityPath

    with tempfile.TemporaryDirectory(prefix="holopatcher-gui-selftest-") as directory:
        args = Namespace(
            tslpatchdata=directory, game_dir=None, namespace_option_index=None,
            namespace_id=None, console=True, action=None,
        )
        app = App(args)
        try:
            app.withdraw()
            editor_window = tk.Toplevel(app)
            editor_window.withdraw()
            editor = RichTextEditor(editor_window, UtilityPath(directory))
            editor.text_area.insert("1.0", "Packaging self-test")
            app.update_idletasks()
            result = {
                "ok": True,
                "nss_compilation": "K1 and K2 passed",
                "frozen": bool(getattr(sys, "frozen", False)),
                "tk": app.tk.call("package", "require", "Tk"),
                "icon_exists": (Path(__file__).parent / "resources/icons/patcher_icon_v2.png").is_file(),
                "launch_directory": str(App._launch_directory()),
            }
            if not result["icon_exists"]:
                raise RuntimeError("Bundled icon is missing")
        finally:
            app.destroy()
    print(json.dumps(result), flush=True)
    return 0
