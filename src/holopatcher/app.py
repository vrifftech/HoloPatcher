from __future__ import annotations

import ctypes
import json
import os
import pathlib
import re
import stat
import sys
import tkinter as tk
import traceback
import webbrowser
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread, current_thread, main_thread
from concurrent.futures import Future
from functools import wraps
from queue import Empty, Queue
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from typing import TYPE_CHECKING, Callable
from types import SimpleNamespace
from urllib.parse import urlparse

from holopatcher import CURRENT_VERSION, ExitCode
from holopatcher.bootstrap import bootstrap_backend

bootstrap_backend()

from pykotor.common.misc import Game
from pykotor.common.stream import BinaryReader
from pykotor.tools.encoding import decode_bytes_with_fallbacks
from pykotor.tools.path import CaseAwarePath, find_kotor_paths_from_default
from pykotor.tslpatcher.config import LogLevel
from pykotor.tslpatcher.logger import LogType, PatchLog, PatchLogger
from pykotor.tslpatcher.patcher import ModInstaller
from pykotor.tslpatcher.reader import ConfigReader
from holopatcher.package import NoPackageError, load_package, resolve_package_file
from pykotor.tslpatcher.uninstall import ModUninstaller
from utility.error_handling import universal_simplify_exception
from utility.string_util import striprtf
from utility.system.path import Path
from utility.tkinter.tooltip import ToolTip

if TYPE_CHECKING:
    from types import TracebackType

    from pykotor.tslpatcher.namespaces import PatcherNamespace

VERSION_LABEL = f"v{CURRENT_VERSION}"

_RTF_FIELD_START = re.compile(r"\{\\field\b", re.IGNORECASE)
_RTF_FLDINST_START = re.compile(r"\{(?:\\\*)?\\fldinst\b", re.IGNORECASE)
_RTF_FLDRSLT_START = re.compile(r"\{\\fldrslt\b", re.IGNORECASE)
_RTF_HYPERLINK = re.compile(r'\bHYPERLINK\s+(?:"([^"]+)"|([^\s{}]+))', re.IGNORECASE)


def _rtf_group_end(source: str, start: int) -> int | None:
    """Return the exclusive end of an RTF group starting at *start*."""
    if start >= len(source) or source[start] != "{":
        return None
    depth = 0
    index = start
    while index < len(source):
        char = source[index]
        if char == "\\" and index + 1 < len(source) and source[index + 1] in "{}\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _rtf_destination_text(group: str, control_word: str) -> str:
    """Strip one destination control word, then decode the remaining RTF."""
    inner = group[1:-1]
    inner = re.sub(
        rf"^\\(?:\*\\)?{re.escape(control_word)}\b\s*",
        "",
        inner,
        count=1,
        flags=re.IGNORECASE,
    )
    return striprtf("{" + inner + "}").strip()


def _rtf_hyperlink(field_group: str) -> tuple[str, str] | None:
    instruction_match = _RTF_FLDINST_START.search(field_group)
    result_match = _RTF_FLDRSLT_START.search(field_group)
    if instruction_match is None or result_match is None:
        return None

    instruction_end = _rtf_group_end(field_group, instruction_match.start())
    result_end = _rtf_group_end(field_group, result_match.start())
    if instruction_end is None or result_end is None:
        return None

    instruction = _rtf_destination_text(
        field_group[instruction_match.start():instruction_end],
        "fldinst",
    )
    url_match = _RTF_HYPERLINK.search(instruction)
    if url_match is None:
        return None

    url = (url_match.group(1) or url_match.group(2) or "").strip()
    if not url:
        return None

    display = _rtf_destination_text(
        field_group[result_match.start():result_end],
        "fldrslt",
    ) or url
    return display, url


def _strip_rtf_with_hyperlinks(rtf_text: str) -> tuple[str, list[tuple[int, int, str]]]:
    """Convert RTF to plain text while retaining standard HYPERLINK spans."""
    transformed: list[str] = []
    replacements: list[tuple[str, str, str]] = []
    cursor = 0
    search_from = 0

    while True:
        match = _RTF_FIELD_START.search(rtf_text, search_from)
        if match is None:
            break
        field_end = _rtf_group_end(rtf_text, match.start())
        if field_end is None:
            break

        parsed = _rtf_hyperlink(rtf_text[match.start():field_end])
        if parsed is not None:
            display, url = parsed
            marker = f"HOLOPATCHERLINKMARKER{len(replacements):06d}X"
            transformed.append(rtf_text[cursor:match.start()])
            transformed.append(marker)
            replacements.append((marker, display, url))
            cursor = field_end
        search_from = field_end

    transformed.append(rtf_text[cursor:])
    plain_text = striprtf("".join(transformed))
    spans: list[tuple[int, int, str]] = []

    for marker, display, url in replacements:
        start = plain_text.find(marker)
        if start < 0:
            continue
        plain_text = plain_text[:start] + display + plain_text[start + len(marker):]
        spans.append((start, start + len(display), url))

    return plain_text, spans

# Minimum display level per severity, matching TSLPatcher's AddLogLine().
_LOG_DISPLAY_LEVELS = {
    LogType.NOTE: LogLevel.GENERAL,
    LogType.ERROR: LogLevel.ERRORS,
    LogType.WARNING: LogLevel.WARNINGS,
    LogType.VERBOSE: LogLevel.FULL,
}


class HoloPatcherError(Exception):
    ...


def on_ui_thread(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        if current_thread() is main_thread():
            return method(self, *args, **kwargs)
        future = Future()
        self._ui_queue.put((future, method, args, kwargs))
        return future.result()
    return call

class App(tk.Tk):
    def __init__(self, cmdline_args: Namespace | None = None):
        if current_thread() is not main_thread():
            raise RuntimeError("The HoloPatcher GUI must be created on the main thread.")
        super().__init__()
        self._ui_queue = Queue()
        self._closing = False
        self.exit_code = ExitCode.SUCCESS
        self.after(20, self._drain_ui_queue)
        self.title(f"HoloPatcher {VERSION_LABEL}")

        self._log_file = None
        self._log_lock = Lock()
        self.log_level = LogLevel.WARNINGS
        self._display_log_level = LogLevel.FULL
        self._log_view_active = False
        self._close_requested = False
        self.task_running: bool = False
        self.task_thread: Thread | None = None
        self.mod_path: str = ""
        self.namespaces: list[PatcherNamespace] = []
        self.tslpatchdata_path: CaseAwarePath | None = None
        self._tooltips: list[ToolTip] = []
        self._discovered_game_paths: dict[Game, list[str]] = {Game.K1: [], Game.K2: []}
        self._manual_game_paths: list[str] = []
        self._game_path_filter: tuple[Game, ...] = (Game.K1, Game.K2)
        self._discovery_running = False
        self._discovery_thread: Thread | None = None

        self.initialize_fonts()
        self.initialize_logger()
        self.initialize_top_menu()
        self.initialize_ui_controls()
        self.set_state(False)
        self.set_window()
        icon_path = pathlib.Path(__file__).parent / "resources/icons/patcher_icon_runtime.png"
        self._icon = tk.PhotoImage(master=self, file=str(icon_path))
        self.iconphoto(True, self._icon)

        # Map the title bar's X button to our handle_exit_button function.
        # This probably also means this will be called when attempting to 'End Task' in e.g. task manager.
        self.protocol("WM_DELETE_WINDOW", self.handle_exit_button)

        if cmdline_args is None:
            cmdline_args = Namespace(tslpatchdata=None, game_dir=None, namespace_option_index=None, namespace_id=None, console=False)
        if getattr(cmdline_args, "action", None) is not None:
            raise HoloPatcherError("CLI actions must use the headless entry point.")
        self.open_mod(cmdline_args.tslpatchdata or self._launch_directory(),
                      namespace_index=cmdline_args.namespace_option_index,
                      namespace_id=cmdline_args.namespace_id)
        self.handle_commandline(cmdline_args)
        self.after_idle(self._start_game_discovery)


    def _drain_ui_queue(self):
        # Bound each UI batch so a large log cannot starve input or cancellation.
        for _ in range(256):
            try:
                future, method, args, kwargs = self._ui_queue.get_nowait()
            except Empty:
                break
            try:
                result = method(self, *args, **kwargs)
            except Exception as exc:
                if future is not None:
                    future.set_exception(exc)
                else:
                    self._handle_general_exception(exc, "A GUI update failed")
            else:
                if future is not None:
                    future.set_result(result)
        if not self._closing:
            self.after(20, self._drain_ui_queue)

    @on_ui_thread
    def _dialog(self, kind: str, *args, **kwargs):
        kwargs.setdefault("parent", self)
        return getattr(messagebox, kind)(*args, **kwargs)

    def _package_file(self, relative_path) -> CaseAwarePath:
        if self.tslpatchdata_path is None:
            raise HoloPatcherError("Select a mod package first.")
        return self._resolve_package_file(self.tslpatchdata_path, relative_path)

    @on_ui_thread
    def _selected_install_paths(self):
        namespace = self._selected_namespace()
        return self.mod_path, self.gamepaths.get(), self._package_file(namespace.changes_filepath())

    def _create_installer(self, package_root, game_path, changes_path):
        return ModInstaller(package_root, game_path, changes_path, self.logger)

    def initialize_fonts(self):
        """Keep platform-derived fonts alive; never override Tk's display scaling."""
        self._ui_font = tkfont.nametofont("TkDefaultFont", root=self).copy()
        self._text_font = tkfont.nametofont("TkTextFont", root=self).copy()
        self._font_sizes = (self._ui_font.cget("size"), self._text_font.cget("size"))
        self._font_scale = 100
        self._style = ttk.Style(self)
        # The entry, its geometry, and the popup must use the same named font.
        self.option_add("*TCombobox*Listbox.font", str(self._ui_font))
        self._configure_control_styles()

    def _configure_control_styles(self):
        line_height = self._ui_font.metrics("linespace")
        self._control_padding = max(2, line_height // 6)
        self._style.configure("HoloPatcher.TCombobox", font=self._ui_font,
                              padding=(self._control_padding * 2, self._control_padding))
        self._style.configure("HoloPatcher.TButton", font=self._ui_font)
        self._style.configure("HoloPatcher.TLabel", font=self._ui_font)

    def _wrap_status(self, event):
        """Wrap status text to the space actually allocated by the grid."""
        padding = [self.winfo_pixels(value) for value in self.tk.splitlist(event.widget.cget("padding"))]
        left = padding[0] if padding else 0
        right = padding[2] if len(padding) >= 3 else left
        width = max(1, event.width - left - right - 2)
        if int(event.widget.cget("wraplength")) != width:
            event.widget.configure(wraplength=width)

    def _window_metrics(self):
        self.update_idletasks()
        # Account for native themes whose entry field has a fixed minimum size.
        line_height = self._ui_font.metrics("linespace")
        for combo in (self.namespaces_combobox, self.gamepaths):
            extra = max(0, line_height + 2 * self._control_padding - combo.winfo_reqheight())
            combo.grid_configure(ipady=(extra + 1) // 2)
        self.update_idletasks()
        screen_width = self.winfo_vrootwidth() or self.winfo_screenwidth()
        screen_height = self.winfo_vrootheight() or self.winfo_screenheight()
        # Leave room for window decorations / desktop panels; do not impose a
        # maxsize, so the user can still resize or move to a larger display.
        available_width = max(1, int(screen_width * 0.90))
        decoration_allowance = max(48, 2 * tkfont.nametofont("TkMenuFont", root=self).metrics("linespace"))
        available_height = max(1, int(screen_height * 0.90) - decoration_allowance)
        digit_width = self._ui_font.measure("0")
        text_line = self._text_font.metrics("linespace")
        chrome_height = (self._top_frame.winfo_reqheight() + self._bottom_frame.winfo_reqheight()
                         + self._progress_frame.winfo_reqheight() + 20)
        minimum_width = min(available_width, max(self._top_frame.winfo_reqwidth(),
                                                self._bottom_frame.winfo_reqwidth(), 44 * digit_width))
        minimum_height = min(available_height, chrome_height + 8 * text_line)
        self.minsize(minimum_width, minimum_height)
        return available_width, available_height, minimum_width, minimum_height, chrome_height

    def set_window(self, width: int | None = None, height: int | None = None):
        """Choose a readable, font-sized starting window within Tk's display bounds."""
        available_width, available_height, minimum_width, minimum_height, chrome = self._window_metrics()
        width = min(available_width, max(minimum_width, width or 94 * self._ui_font.measure("0")))
        height = min(available_height, max(minimum_height, height or chrome + 22 * self._text_font.metrics("linespace")))
        x_position = self.winfo_vrootx() + max(0, (self.winfo_vrootwidth() - width) // 2)
        y_position = self.winfo_vrooty() + int(self.winfo_vrootheight() * 0.05) + max(0, (available_height - height) // 2)
        self.geometry(f"{width}x{height}{x_position:+d}{y_position:+d}")
        self.resizable(width=True, height=True)

    def change_font_size(self, increment: int):
        """Resize this application's controls/text without changing system fonts."""
        if self._closing or self._close_requested:
            return "break"
        scale = 100 if increment == 0 else min(200, max(80, self._font_scale + increment))
        if scale == self._font_scale:
            return "break"
        self._font_scale = scale
        for font, base_size in zip((self._ui_font, self._text_font), self._font_sizes):
            size = max(1, round(abs(base_size) * scale / 100))
            font.configure(size=-size if base_size < 0 else size)
        self._log_bold_font.configure(size=self._text_font.cget("size"))
        self._log_verbose_font.configure(size=self._text_font.cget("size"))
        self._configure_control_styles()
        width, height = self.winfo_width(), self.winfo_height()
        _, _, minimum_width, minimum_height, _ = self._window_metrics()
        # Preserve the user's size/position, growing only enough for controls.
        # Resizing text must not reset selections or log text.
        self.geometry(f"{max(width, minimum_width)}x{max(height, minimum_height)}")
        self.view_menu.entryconfigure(0, state=tk.DISABLED if scale == 200 else tk.NORMAL)
        self.view_menu.entryconfigure(1, state=tk.DISABLED if scale == 80 else tk.NORMAL)
        return "break"

    def initialize_logger(self):
        self.logger = PatchLogger()
        self.logger.verbose_observable.subscribe(self.write_log)
        self.logger.note_observable.subscribe(self.write_log)
        self.logger.warning_observable.subscribe(self.write_log)
        self.logger.error_observable.subscribe(self.write_log)

    def initialize_top_menu(self):
        # Initialize top menu bar
        self.menu_bar = tk.Menu(self)
        self.config(menu=self.menu_bar)

        # Tools menu
        tools_menu = tk.Menu(self.menu_bar, tearoff=0)
        tools_menu.add_command(label="Validate INI syntax/configuration", command=self.test_reader)
        tools_menu.add_command(label="Uninstall Mod / Restore Backup", command=self.uninstall_selected_mod)
        tools_menu.add_command(label="Grant owner access to selected folder...", command=self.fix_permissions)
        tools_menu.add_command(label="Create info.rte...", command=self.create_rte_content)
        self.menu_bar.add_cascade(label="Tools", menu=tools_menu)

        self.view_menu = tk.Menu(self.menu_bar, tearoff=0)
        modifier = "Command" if self.tk.call("tk", "windowingsystem") == "aqua" else "Control"
        shortcut = "Cmd" if modifier == "Command" else "Ctrl"
        self.view_menu.add_command(label="Larger text", accelerator=f"{shortcut}++",
                                   command=lambda: self.change_font_size(10))
        self.view_menu.add_command(label="Smaller text", accelerator=f"{shortcut}+-",
                                   command=lambda: self.change_font_size(-10))
        self.view_menu.add_command(label="Default text size", accelerator=f"{shortcut}+0",
                                   command=lambda: self.change_font_size(0))
        self.view_menu.add_separator()
        self.view_menu.add_command(label="Reset window size", command=self.set_window)
        self.menu_bar.add_cascade(label="View", menu=self.view_menu)
        for key, increment in (("plus", 10), ("equal", 10), ("KP_Add", 10),
                               ("minus", -10), ("KP_Subtract", -10), ("0", 0)):
            self.bind(f"<{modifier}-{key}>", lambda event, amount=increment: self.change_font_size(amount))

        # Help menu
        help_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Help", menu=help_menu)

        # DeadlyStream submenu
        deadlystream_menu = tk.Menu(help_menu, tearoff=0)
        deadlystream_menu.add_command(label="Discord", command=lambda: webbrowser.open_new("https://discord.gg/nDkHXfc36s"))
        deadlystream_menu.add_command(label="Website", command=lambda: webbrowser.open_new("https://deadlystream.com"))
        help_menu.add_cascade(label="DeadlyStream", menu=deadlystream_menu)

        # Neocities submenu
        neocities_menu = tk.Menu(help_menu, tearoff=0)
        neocities_menu.add_command(label="Discord", command=lambda: webbrowser.open_new("https://discord.com/invite/kotor"))
        neocities_menu.add_command(label="Website", command=lambda: webbrowser.open_new("https://kotor.neocities.org"))
        help_menu.add_cascade(label="KOTOR Community Portal", menu=neocities_menu)

        # OpenKOTOR submenu
        openkotor_menu = tk.Menu(help_menu, tearoff=0)
        openkotor_menu.add_command(label="Discord", command=lambda: webbrowser.open_new("https://discord.gg/YC7wBqabxA"))
        openkotor_menu.add_command(label="Website", command=lambda: webbrowser.open_new("https://openkotor.com"))
        help_menu.add_cascade(label="OpenKOTOR", menu=openkotor_menu)

    def initialize_ui_controls(self):
        # Use grid layout for main window
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Top area for comboboxes and buttons
        top_frame = self._top_frame = ttk.Frame(self)
        top_frame.grid(row=0, column=0, sticky="ew")
        top_frame.grid_columnconfigure(0, weight=1)  # Make comboboxes expand
        top_frame.grid_columnconfigure(1, weight=0)
        top_frame.grid_columnconfigure(2, weight=0)  # Keep buttons fixed size

        # Setup the namespaces/changes ini combobox (selected mod)
        self.namespaces_combobox: ttk.Combobox = ttk.Combobox(
            top_frame, state="readonly", style="HoloPatcher.TCombobox", font=self._ui_font, width=28, height=10,
        )
        self.namespaces_combobox.grid(row=0, column=0, padx=5, pady=2, sticky="ew")
        self.namespaces_combobox.set("Select the mod to install")
        self._tooltips.append(ToolTip(self.namespaces_combobox, lambda: self.get_namespace_description()))
        self.namespaces_combobox.bind("<<ComboboxSelected>>", self.on_namespace_option_chosen)
        self.namespace_info_button = ttk.Button(
            top_frame, text="?", width=3, takefocus=True, style="HoloPatcher.TButton",
            command=self.show_namespace_description,
        )
        self.namespace_info_button.grid(row=0, column=1, padx=(0, 5), pady=2)
        self.namespace_info_button.bind("<Return>", lambda event: self.namespace_info_button.invoke())
        self._tooltips.append(ToolTip(
            self.namespace_info_button,
            lambda: "Show the selected installation option's description",
        ))
        # Browse for a tslpatcher mod
        self.browse_button: ttk.Button = ttk.Button(top_frame, text="Browse", command=self.open_mod, style="HoloPatcher.TButton")
        self.browse_button.grid(row=0, column=2, padx=5, pady=2, sticky="e")

        # Store all discovered KOTOR install paths
        self.gamepaths = ttk.Combobox(
            top_frame, style="HoloPatcher.TCombobox", font=self._ui_font, width=28, height=10,
        )
        self.gamepaths.set("Select your KOTOR directory path")
        self.gamepaths.grid(row=1, column=0, columnspan=2, padx=5, pady=2, sticky="ew")
        self.gamepaths["values"] = ()
        self.gamepaths.bind("<<ComboboxSelected>>", self.on_gamepaths_chosen)
        self.gamepaths.bind("<Return>", self._remember_game_path)
        self.gamepaths.bind("<FocusOut>", self._remember_game_path)
        # Browse for a KOTOR path
        self.gamepaths_browse_button = ttk.Button(top_frame, text="Browse", command=self.open_kotor, style="HoloPatcher.TButton")
        self.gamepaths_browse_button.grid(row=1, column=2, padx=5, pady=2, sticky="e")

        self.discovery_status = ttk.Label(
            top_frame, text="Searching for game installations…", style="HoloPatcher.TLabel",
            width=1, wraplength=400, anchor="w", justify=tk.LEFT,
        )
        self.discovery_status.bind("<Configure>", self._wrap_status)
        self.discovery_status.grid(row=2, column=0, columnspan=2, padx=5, pady=(0, 4), sticky="ew")
        self.discovery_refresh_button = ttk.Button(top_frame, text="Refresh", command=self._start_game_discovery, style="HoloPatcher.TButton")
        self.discovery_refresh_button.grid(row=2, column=2, padx=5, pady=(0, 4), sticky="e")

        # Middle area for text and scrollbar
        text_frame = ttk.Frame(self)
        text_frame.grid(row=1, column=0, sticky="nsew")
        text_frame.grid_rowconfigure(1, weight=1)
        text_frame.grid_columnconfigure(0, weight=1)

        self.preparation_label = ttk.Label(
            text_frame,
            text=("Preparing installation...\n"
                  "Reading configuration and assembling patch order.\n"
                  "Large mods may take a while."),
            padding=8, justify=tk.LEFT, anchor="w", wraplength=400, width=1, style="HoloPatcher.TLabel",
        )
        self.preparation_label.bind("<Configure>", self._wrap_status)
        self.preparation_label.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.preparation_label.grid_remove()

        # Configure the text
        # Small natural request lets the scrollable area shrink on small screens;
        # set_window() supplies the preferred reading area using font metrics.
        self.main_text = tk.Text(text_frame, wrap=tk.WORD, width=1, height=1, padx=8, pady=6)
        self.main_text.grid(row=1, column=0, sticky="nsew")
        self.set_text_font(self.main_text)
        self._log_bold_font = self._text_font.copy()
        self._log_bold_font.configure(weight="bold")
        self._log_verbose_font = self._text_font.copy()
        self._log_verbose_font.configure(slant="italic")

        # Create scrollbar for main frame
        scrollbar = ttk.Scrollbar(text_frame, command=self.main_text.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.main_text.config(yscrollcommand=scrollbar.set)

        # Bottom area for buttons
        bottom_frame = self._bottom_frame = ttk.Frame(self)
        bottom_frame.grid(row=2, column=0, sticky="ew")

        self.exit_button = ttk.Button(bottom_frame, text="Exit", command=self.handle_exit_button, style="HoloPatcher.TButton")
        self.exit_button.pack(side="left", padx=5, pady=5)
        self.install_button = ttk.Button(bottom_frame, text="Install", command=self.begin_install, style="HoloPatcher.TButton")
        self.install_button.pack(side="right", padx=5, pady=5)
        self.simple_thread_event: Event = Event()
        progress_frame = self._progress_frame = ttk.Frame(self)
        progress_frame.grid(row=3, column=0, padx=5, pady=(0, 5), sticky="ew")
        progress_frame.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress_label = ttk.Label(progress_frame, text="Ready", style="HoloPatcher.TLabel",
                                        width=1, wraplength=400, anchor="w", justify=tk.LEFT)
        self.progress_label.bind("<Configure>", self._wrap_status)
        self.progress_label.grid(row=1, column=0, sticky="ew")

    def set_text_font(
        self,
        text_frame: tk.Text,
    ):
        text_frame.configure(font=self._text_font)

    @on_ui_thread
    def check_for_updates(self):
        if self.task_running:
            return
        self._dialog("showinfo", "Updates are not configured",
                     f"This build is HoloPatcher {CURRENT_VERSION}. No release channel is configured for this audited build. "
                     "It will not download or recommend a different project's installer.")

    def handle_commandline(self, cmdline_args: Namespace):
        """Apply GUI preselection; explicit CLI actions never construct this window."""
        if cmdline_args.game_dir:
            self.open_kotor(cmdline_args.game_dir)
        if not cmdline_args.console:
            self.hide_console()

    def hide_console(self):
        """Hide only an exclusively owned Windows console, never the caller's terminal."""
        if os.name != "nt":
            return
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        kernel32.GetConsoleProcessList.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
        kernel32.GetConsoleProcessList.restype = wintypes.DWORD
        handle = kernel32.GetConsoleWindow()
        processes = (wintypes.DWORD * 2)()
        count = kernel32.GetConsoleProcessList(processes, len(processes))
        if handle and count == 1 and processes[0] == os.getpid():
            user32 = ctypes.windll.user32
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.ShowWindow(handle, 0)

    @on_ui_thread
    def uninstall_selected_mod(self):
        if not self.preinstall_validate_chosen():
            return
        package, game_path, _ = self._selected_install_paths()
        backup_parent = Path(package, "backup")
        if not backup_parent.safe_isdir():
            self._dialog("showerror", "Backup folder empty/missing",
                         f"Could not find backup folder '{backup_parent}'. No game files were changed.")
            return
        dialogs = SimpleNamespace(**{
            kind: (lambda *args, _kind=kind, **kwargs: self._dialog(_kind, *args, **kwargs))
            for kind in ("showinfo", "showwarning", "showerror", "askyesno", "askyesnocancel")
        })
        def restore():
            uninstaller = ModUninstaller(backup_parent, Path(game_path), self.logger, dialogs=dialogs)
            success = uninstaller.uninstall_selected_mod(should_cancel=self.simple_thread_event)
            if not success:
                self.exit_code = ExitCode.ABORT_INSTALL_UNSAFE if self.simple_thread_event.is_set() else ExitCode.INSTALL_COMPLETED_WITH_ERRORS
            self.logger.add_note("Backup restoration completed." if success else "Backup restoration did not complete; the backup was retained.")
        self._start_task("Backup restoration", restore, log_path=pathlib.Path(package) / "installlog.txt")

    def _close_when_idle(self):
        if self.task_running or (self.task_thread is not None and self.task_thread.is_alive()):
            self.after(50, self._close_when_idle)
            return
        if not self._closing:
            self._closing = True
            self.destroy()

    def handle_exit_button(self):
        if self._close_requested:
            return
        if self.task_running:
            if not self._dialog("askyesno", "Cancel current task?",
                                "Stop after the current file operation finishes and close? A partial operation may need restoration from backup."):
                return
            self._close_requested = True
            self.simple_thread_event.set()
            self.exit_button.config(state=tk.DISABLED)
            self._close_when_idle()
            return
        self._close_requested = True
        self._close_when_idle()

    def _start_game_discovery(self):
        """One independent, read-only scan; never occupy the installation worker."""
        if self._discovery_running or self.task_running or self._close_requested or self._closing:
            return
        self._discovery_running = True
        self.discovery_status.config(text="Searching for game installations…")
        self.discovery_refresh_button.config(state=tk.DISABLED)

        def discover():
            try:
                paths = {game: [str(path) for path in values]
                         for game, values in find_kotor_paths_from_default().items()}
            except Exception as exc:
                if sys.stderr is not None:
                    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
                self._post_ui(App._finish_game_discovery, None)
            else:
                self._post_ui(App._finish_game_discovery, paths)

        try:
            self._discovery_thread = Thread(target=discover, name="game-installation-discovery", daemon=True)
            self._discovery_thread.start()
        except Exception:
            self._finish_game_discovery(None)

    def _finish_game_discovery(self, paths: dict[Game, list[str]] | None):
        self._discovery_running = False
        self._discovery_thread = None
        # A slow/unavailable volume must not prevent closing the window. Late
        # results stay on the queue; no Tk calls originate from the worker.
        if self._closing or self._close_requested:
            return
        if paths is None:
            self.discovery_status.config(text="Search failed. Use Browse or Refresh.")
        else:
            self._discovered_game_paths = paths
            count = sum(len(values) for values in paths.values())
            self.discovery_status.config(text=(
                f"Found {count} game installation{'s' if count != 1 else ''}."
                if count else "No installations found. Use Browse."
            ))
            self._refresh_game_path_choices()
        self.discovery_refresh_button.config(state=tk.DISABLED if self.task_running else tk.NORMAL)

    def _remember_game_path(self, event: tk.Event | None = None):
        value = self.gamepaths.get()
        if value and value != "Select your KOTOR directory path" and value not in self._manual_game_paths:
            self._manual_game_paths.append(value)

    def _refresh_game_path_choices(self):
        """Filter the cached scan without changing the user's selection or text."""
        self._remember_game_path()
        paths = [path for game in self._game_path_filter for path in self._discovered_game_paths.get(game, ())]
        self.gamepaths["values"] = tuple(dict.fromkeys([*paths, *self._manual_game_paths]))

    def on_gamepaths_chosen(
        self,
        event: tk.Event,
    ):
        """Adjust the combobox after a short delay."""
        self._remember_game_path()
        self.after(10, lambda: self.move_cursor_to_end(event.widget))

    def move_cursor_to_end(
        self,
        combobox: ttk.Combobox,
    ):
        """Shows the rightmost portion of the specified combobox as that's the most relevant."""
        combobox.focus_set()
        position: int = len(combobox.get())
        combobox.icursor(position)
        combobox.xview(position)
        self.focus_set()

    def _hide_tooltips(self):
        """Close any tooltip windows before modal actions or mod state changes."""
        for tooltip in self._tooltips:
            tooltip.hide_tip()

    def get_namespace_description(self) -> str:
        index = self.namespaces_combobox.current()
        return self.namespaces[index].description if 0 <= index < len(self.namespaces) else ""


    @on_ui_thread
    def show_namespace_description(self):
        self._hide_tooltips()
        if self.task_running or self._close_requested:
            return
        index = self.namespaces_combobox.current()
        if not 0 <= index < len(self.namespaces):
            return
        namespace = self.namespaces[index]
        name = namespace.name or f"Option {index + 1}"
        self._dialog(
            "showinfo", f"Installation option {index + 1}: {name}",
            f"{name}\n\n{namespace.description or 'No description was provided for this option.'}",
        )

    @on_ui_thread
    def on_namespace_option_chosen(
        self,
        event: tk.Event,
        config_reader: ConfigReader | None = None,
    ):
        """Handles the namespace option being chosen from the combobox.

        Args:
        ----
            self: The PatcherWindow instance
            event: The event object from the combobox

        Processes the chosen namespace option by:
            1. Finding the matching PatcherNamespace object
            2. Loading the changes.ini file path
            3. Extracting the game number if present
            4. Handling game paths if a game number is found
            5. Loading the info.rtf file as defined.
        """
        self._hide_tooltips()
        if self.task_running or self._close_requested:
            return
        try:
            # Load the settings from the ini changes file.
            namespace_option = self._selected_namespace()
            changes_ini_path = self._package_file(namespace_option.changes_filepath())
            reader: ConfigReader = config_reader or ConfigReader.from_filepath(changes_ini_path)
            reader.load_settings()
            self.log_level = reader.config.log_level

            # Filter the listed games in the combobox with the mod's supported ones.
            game_number: int | None = reader.config.game_number
            if game_number:
                game = Game(game_number)
                # Retain the existing K2-option ordering/compatibility policy.
                self._game_path_filter = (game, Game.K1) if game == Game.K2 else (game,)
            else:
                self._game_path_filter = (Game.K1, Game.K2)
            self._refresh_game_path_choices()

            # Strip info.rtf and display in the main window frame.
            info_rtf_path = self._package_file(namespace_option.rtf_filepath())
            info_rte_path = CaseAwarePath.get_case_sensitive_path(info_rtf_path.with_suffix(".rte"))
            if not info_rtf_path.safe_isfile() and not info_rte_path.safe_isfile():
                self.set_stripped_rtf_text("")
                self._dialog("showwarning", "No info.rtf", f"Could not load the info rtf for this mod, file '{info_rtf_path}' not found on disk.")
                return
            if info_rte_path.safe_isfile():
                data: bytes = BinaryReader.load_file(info_rte_path)
                rtf_text: str = decode_bytes_with_fallbacks(data)
                self.load_rte_content(rtf_text)
            elif info_rtf_path.safe_isfile():
                data = BinaryReader.load_file(info_rtf_path)
                rtf_text = decode_bytes_with_fallbacks(data)
                self.set_stripped_rtf_text(rtf_text)
        except Exception as e:  # noqa: BLE001
            self._handle_general_exception(e, "An unexpected error occurred while loading the patcher namespace.")
        else:
            self.focus_set()

    @on_ui_thread
    def _handle_general_exception(self, exc: BaseException, custom_msg: str = "Unexpected error", title: str = "", msgbox: bool = True):
        """Report independently of disk logging, including Tk callback/worker failures."""
        detailed_msg = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if sys.stderr is not None:
            print(detailed_msg, file=sys.stderr)
        if self._display_log_level != LogLevel.NOTHING:
            self._append_text(f"{custom_msg}\n{detailed_msg}\n", LogType.ERROR)
        log_error = None
        with self._log_lock:
            if self._log_file is not None:
                try:
                    self._log_file.write(detailed_msg + "\n")
                    self._log_file.flush()
                except OSError as failure:
                    log_error = failure
        error_name, msg = universal_simplify_exception(exc)
        if log_error is not None:
            msg += f"\nThe operation log could not be written: {log_error}"
        if msgbox:
            self._dialog("showerror", title or error_name, f"{custom_msg}.\n\n{msg}")

    @on_ui_thread
    def load_namespace(self, namespaces: list[PatcherNamespace], config_reader: ConfigReader | None = None, *, selected_namespace: PatcherNamespace | None = None):
        self._hide_tooltips()
        if self.task_running:
            return
        if not namespaces:
            raise HoloPatcherError("The package declares no namespace options.")
        selected_index = 0 if selected_namespace is None else next(i for i, value in enumerate(namespaces) if value is selected_namespace)
        self.namespaces = namespaces
        self.namespaces_combobox["values"] = [namespace.name or f"Option {i + 1}" for i, namespace in enumerate(namespaces)]
        self.namespaces_combobox.config(state="readonly")
        self.namespaces_combobox.current(selected_index)
        self.install_button.config(state=tk.NORMAL)
        self.namespace_info_button.config(state=tk.NORMAL)
        self.on_namespace_option_chosen(tk.Event(), config_reader)

    @on_ui_thread
    def open_mod(self, default_directory_path_str: os.PathLike | str | None = None, *,
                 namespace_index: int | None = None, namespace_id: str | None = None):
        """Commit a package and its namespace catalogue together after validation."""
        self._hide_tooltips()
        if self.task_running or self._close_requested:
            return
        try:
            requested = default_directory_path_str
            if requested is None:
                requested = filedialog.askdirectory(parent=self)
            if not requested:
                return
            package = load_package(requested)
            index = package.select(namespace_index, namespace_id)
            reader = ConfigReader.from_filepath(package.changes_path(index))
            reader.load_settings()
            if not self.check_access(Path(package.data_root)):
                return
            self.tslpatchdata_path = package.data_root
            self.mod_path = str(package.root)
            self.load_namespace(list(package.namespaces), reader,
                                selected_namespace=package.namespaces[index])
        except NoPackageError as exc:
            # Launching outside a mod package leaves Browse available, as before.
            if default_directory_path_str is None or namespace_index is not None or namespace_id is not None:
                self._handle_general_exception(exc, "Could not load the mod package; the previous selection was retained")
        except Exception as exc:
            self._handle_general_exception(exc, "Could not load the mod package; the previous selection was retained")

    def open_kotor(
        self,
        default_kotor_dir_str: os.PathLike | str | None = None,
    ):
        """Opens the KOTOR directory."""
        if self.task_running or self._close_requested:
            return
        try:
            directory_path_str: os.PathLike | str = default_kotor_dir_str or filedialog.askdirectory(parent=self)
            if not directory_path_str:
                return
            directory = CaseAwarePath.get_case_sensitive_path(os.path.expanduser(directory_path_str))
            if not self.check_access(directory):
                return
            self.gamepaths.set(str(directory))
            self._refresh_game_path_choices()
            self.after(10, self.move_cursor_to_end, self.gamepaths)
        except Exception as e:  # noqa: BLE001
            self._handle_general_exception(e, "An unexpected error occurred while loading the game directory.")



    @on_ui_thread
    def fix_permissions(self, directory: os.PathLike | str | None = None):
        """An explicit owner-only change on one selected POSIX path; never recursive."""
        if self.task_running or self._close_requested:
            self._dialog("showinfo", "Task already running", "Finish the current task before changing permissions.")
            return
        if os.name != "posix":
            self._dialog("showinfo", "Permission changes are not automatic",
                         "Use this path's Windows Properties / Security settings to grant your account access. HoloPatcher does not change ACLs or elevate itself.")
            return
        selected = directory if directory is not None else filedialog.askdirectory(parent=self)
        if not selected:
            return
        try:
            path = pathlib.Path(selected).expanduser().absolute()
            original = path.lstat()
            if stat.S_ISLNK(original.st_mode) or not (stat.S_ISDIR(original.st_mode) or stat.S_ISREG(original.st_mode)):
                raise ValueError("Select a regular file or directory, not a link or special file.")
            if original.st_uid != os.getuid():
                raise PermissionError("Only paths owned by your current account can be changed here.")
            mode = stat.S_IMODE(original.st_mode)
            granted = stat.S_IRUSR | stat.S_IWUSR | (stat.S_IXUSR if stat.S_ISDIR(original.st_mode) else 0)
            updated = mode | granted
            if updated == mode:
                self._dialog("showinfo", "Owner permissions already set", "The selected path already has the requested owner permissions. No changes were made.")
                return
            if not self._dialog("askyesno", "Change owner permissions?",
                                f"Change only '{path}' from {mode:04o} to {updated:04o}?\n\nSubfolders and files inside it will not be changed. Other users' permissions and ownership remain unchanged."):
                return
            def change():
                if self.simple_thread_event.is_set():
                    return
                current = path.lstat()
                if (current.st_dev, current.st_ino, current.st_uid, current.st_mode) != (original.st_dev, original.st_ino, original.st_uid, original.st_mode):
                    raise RuntimeError("The selected path changed after confirmation; permissions were not changed.")
                os.chmod(path, updated, follow_symlinks=False)
                self.logger.add_note(f"Owner access set on '{path}' ({mode:04o} -> {updated:04o}). No other paths were changed.")
                self._dialog("showinfo", "Owner permissions updated", "Only the selected path was changed.")
            self._start_task("Owner permission change", change)
        except Exception as exc:
            self._handle_general_exception(exc, "Permissions were not changed")

    def check_access(self, directory: Path) -> bool:
        """Report access problems; selection never changes permissions or ownership."""
        mode = os.R_OK | os.X_OK
        if directory.is_dir() and os.access(directory, mode):
            return True
        self._dialog("showerror", "Path is not accessible",
                     f"HoloPatcher needs read access to '{directory}'. "
                     "Choose an accessible directory or adjust your account's permissions explicitly. No permissions were changed.")
        return False

    def preinstall_validate_chosen(self) -> bool:
        """Validates prerequisites for starting an install."""
        if self.task_running:
            self._dialog("showinfo",
                "Task already running",
                "Wait for the previous task to finish.",
            )
            return False
        if not self.mod_path or not CaseAwarePath(self.mod_path).safe_isdir() or not 0 <= self.namespaces_combobox.current() < len(self.namespaces):
            self._dialog("showinfo",
                "No mod chosen",
                "Select your mod directory first.",
            )
            return False
        game_path: str = self.gamepaths.get()
        if not game_path:
            self._dialog("showinfo",
                "No KOTOR directory chosen",
                "Select your KOTOR directory first.",
            )
            return False
        case_game_path = CaseAwarePath(game_path)
        if not case_game_path.safe_isdir():
            self._dialog("showinfo",
                "Invalid KOTOR directory chosen",
                "Select a valid path to your KOTOR install.",
            )
            return False
        game_path_str = str(case_game_path)
        self.gamepaths.set(game_path_str)
        return self.check_access(Path(game_path_str))

    @on_ui_thread
    def begin_install(self):
        if not self.preinstall_validate_chosen():
            return
        try:
            selection = self._selected_install_paths()
            reader = ConfigReader.from_filepath(selection[2])
            reader.load_settings()
            self.log_level = reader.config.log_level
        except Exception as exc:
            self._handle_general_exception(exc, "Could not select the installation option")
            return
        self._start_task("Installation", lambda: self.begin_install_thread(self.simple_thread_event, selection),
                         log_path=pathlib.Path(selection[0]) / "installlog.txt", log_level=self.log_level, preparing_install=True)

    def begin_install_thread(self, should_cancel_thread: Event, selection=None):
        """Use the admitted GUI selection and the existing worker lifecycle."""
        if selection is None:
            try:
                selected = self._selected_install_paths()
                reader = ConfigReader.from_filepath(selected[2])
                reader.load_settings()
                self.log_level = reader.config.log_level
            except Exception as exc:
                self.exit_code = ExitCode.EXCEPTION_DURING_INSTALL
                self._handle_general_exception(exc, "Could not select the installation option")
                return
            self._start_task("Installation", lambda: self.begin_install_thread(should_cancel_thread, selected),
                             log_path=pathlib.Path(selected[0]) / "installlog.txt", log_level=self.log_level, preparing_install=True)
            return
        package_root, game_path, ini_file_path = selection
        installer = self._create_installer(package_root, game_path, ini_file_path)
        self._execute_mod_install(installer, should_cancel_thread)

    @on_ui_thread
    def test_reader(self):
        if self.task_running or self._close_requested:
            self._dialog("showinfo", "Task already running", "Finish the current task before validating another configuration.")
            return
        try:
            namespace = self._selected_namespace()
            ini_file_path = self._package_file(namespace.changes_filepath())
            package_root = self.mod_path
        except Exception as exc:
            self._handle_general_exception(exc, "Select a mod package before validating its configuration")
            return
        def parse():
            try:
                reader = ConfigReader.from_filepath(ini_file_path, self.logger)
                reader.load(reader.config)
            except Exception as exc:
                self.exit_code = ExitCode.INSTALL_COMPLETED_WITH_ERRORS
                self._handle_general_exception(exc, "INI syntax/configuration validation failed")
                return
            if self.logger.errors:
                self.exit_code = ExitCode.INSTALL_COMPLETED_WITH_ERRORS
            self.logger.add_note("INI syntax/configuration parsing finished. This does not validate destination paths, required resources, or whether installation will succeed.")
        self._start_task("INI syntax/configuration validation", parse,
                         log_path=pathlib.Path(package_root) / "installlog.txt")

    @on_ui_thread
    def set_state(self, state: bool):
        if state:
            self._hide_tooltips()
        self.task_running = state
        idle = not state and not self._close_requested
        state_name = tk.NORMAL if idle else tk.DISABLED
        self.install_button.config(state=tk.NORMAL if idle and self.namespaces else tk.DISABLED)
        self.gamepaths_browse_button.config(state=state_name)
        self.discovery_refresh_button.config(state=tk.NORMAL if idle and not self._discovery_running else tk.DISABLED)
        self.browse_button.config(state=state_name)
        self.gamepaths.config(state=state_name)
        self.namespaces_combobox.config(state="readonly" if idle and self.namespaces else tk.DISABLED)
        self.namespace_info_button.config(state=tk.NORMAL if idle and self.namespaces else tk.DISABLED)
        for label in ("Tools",):
            self.menu_bar.entryconfigure(label, state=state_name)

    def _clear_text_content(self):
        self.main_text.config(state=tk.NORMAL)
        self.main_text.delete("1.0", tk.END)
        for tag in self.main_text.tag_names():
            if tag != "sel":
                self.main_text.tag_delete(tag)

    @on_ui_thread
    def clear_main_text(self):
        """Start a fresh log view, without inheriting mod-authored RTE styles."""
        self._clear_text_content()
        red, green, blue = self.main_text.winfo_rgb(self.main_text.cget("background"))
        dark = (299 * red + 587 * green + 114 * blue) < 32768 * 1000
        self.main_text.tag_configure("_log_note", foreground=self.main_text.cget("foreground"))
        self.main_text.tag_configure(
            "_log_verbose", foreground="#9dc9ff" if dark else "#245b85", font=self._log_verbose_font,
        )
        self.main_text.tag_configure(
            "_log_warning", foreground="#ffd166" if dark else "#854600", font=self._log_bold_font,
        )
        self.main_text.tag_configure(
            "_log_error", foreground="#ff9b9b" if dark else "#9c2020", font=self._log_bold_font,
        )
        self.main_text.config(state=tk.DISABLED)
        self._log_view_active = True

    def _execute_mod_install(
        self,
        installer: ModInstaller,
        should_cancel_thread: Event,
    ):
        """Executes the mod installation."""
        confirm_msg: str = installer.config().confirm_message.strip()
        if confirm_msg and confirm_msg != "N/A" and not self._dialog("askokcancel", "This mod requires confirmation", confirm_msg):
            should_cancel_thread.set()
            self.logger.add_note("Installation declined; no game files were changed.")
            return
        install_start_time: datetime = datetime.now(timezone.utc).astimezone()
        processed = 0
        def progress_update():
            nonlocal processed
            processed += 1
            self._post_ui(App._show_progress, processed, self.logger.patches_configured)
        installer.install(should_cancel_thread, progress_update_func=progress_update)
        self._post_ui(App._show_progress, processed, self.logger.patches_configured)
        total_install_time: timedelta = datetime.now(timezone.utc).astimezone() - install_start_time

        days, remainder = divmod(total_install_time.total_seconds(), 24 * 60 * 60)
        hours, remainder = divmod(remainder, 60 * 60)
        minutes, seconds = divmod(remainder, 60)

        time_str = (
            f"{f'{int(days)} days, ' if days else ''}"
            f"{f'{int(hours)} hours, ' if hours else ''}"
            f"{f'{int(minutes)} minutes, ' if minutes or not (days or hours) else ''}"
            f"{int(seconds)} seconds"
        )

        num_errors: int = len(self.logger.errors)
        num_warnings: int = len(self.logger.warnings)
        num_patches: int = self.logger.patches_completed
        self.logger.add_note(
            f"The installation is complete with {num_errors} errors and {num_warnings} warnings.{os.linesep}"
            f"Total install time: {time_str}{os.linesep}"
            f"Total patches: {num_patches}",
        )
        if should_cancel_thread.is_set():
            self.exit_code = ExitCode.ABORT_INSTALL_UNSAFE
            self._dialog("showwarning", "Installation cancelled", "Installation stopped. Review the log and backup before continuing.")
            return
        if num_errors > 0:
            self.exit_code = ExitCode.INSTALL_COMPLETED_WITH_ERRORS
            self._dialog("showerror",
                "Install completed with errors!",
                f"The install completed with {num_errors} errors and {num_warnings} warnings! The installation may not have been successful, check the logs for more details."
                f"{os.linesep*2}Total install time: {time_str}"
                f"{os.linesep}Total patches: {num_patches}",
            )
        elif num_warnings > 0:
            self._dialog("showwarning",
                "Install completed with warnings",
                f"The install completed with {num_warnings} warnings! Review the logs for details. The script in the 'uninstall' folder of the mod directory will revert these changes."
                f"{os.linesep*2}Total install time: {time_str}"
                f"{os.linesep}Total patches: {num_patches}",
            )
        else:
            self._dialog("showinfo",
                "Install complete!",
                f"Check the logs for details on what has been done. Utilize the script in the 'uninstall' folder of the mod directory to revert these changes."
                f"{os.linesep*2}Total install time: {time_str}"
                f"{os.linesep}Total patches: {num_patches}",
            )



    @on_ui_thread
    def create_rte_content(self, event: tk.Tk | None = None):
        if self.task_running or self._close_requested:
            return
        from utility.tkinter.rte_editor import RichTextEditor
        window = tk.Toplevel(self)
        window.editor = RichTextEditor(window, Path(self.mod_path or Path.cwd()))
        window.protocol("WM_DELETE_WINDOW", window.destroy)

    @on_ui_thread
    def load_rte_content(self, rte_content: str | bytes | bytearray | None = None):
        if rte_content is None:
            filename = filedialog.askopenfilename()
            if not filename:
                return
            rte_content = decode_bytes_with_fallbacks(BinaryReader.load_file(filename))
        document = json.loads(rte_content)
        self._clear_text_content()
        self._log_view_active = False
        self.main_text.insert("1.0", document["content"])
        # Old RTE documents used fixed tags; current documents store their configurations.
        defaults = {"bold": {"font": self._log_bold_font}, "italic": {"font": self._log_verbose_font},
                    "underline": {"underline": True}, "overstrike": {"overstrike": True}}
        for tag, config in {**defaults, **document.get("tag_configs", {})}.items():
            self.main_text.tag_configure(tag, **config)
        for tag, ranges in document.get("tags", {}).items():
            for start, end in ranges:
                self.main_text.tag_add(tag, start, end)
        self.main_text.config(state=tk.DISABLED)


    def _open_info_link(self, url: str):
        """Open a user-clicked web/mail link from mod-authored info.rtf."""
        scheme = urlparse(url).scheme.lower()
        if scheme not in {"http", "https", "mailto"}:
            self._dialog(
                "showwarning",
                "Unsupported link",
                f"HoloPatcher will not open this link type:\n{url}",
            )
            return
        webbrowser.open_new_tab(url)

    @on_ui_thread
    def set_stripped_rtf_text(
        self,
        rtf_text: str,
    ):
        """Display info.rtf as plain text while preserving clickable hyperlinks."""
        stripped_content, links = _strip_rtf_with_hyperlinks(rtf_text)
        self._clear_text_content()
        self._log_view_active = False
        self.main_text.insert("1.0", stripped_content)

        for link_index, (start, end, url) in enumerate(links):
            tag = f"_rtf_hyperlink_{link_index}"
            self.main_text.tag_configure(tag, foreground="#0563C1", underline=True)
            self.main_text.tag_add(tag, f"1.0+{start}c", f"1.0+{end}c")
            self.main_text.tag_bind(tag, "<Enter>", lambda event: self.main_text.config(cursor="hand2"))
            self.main_text.tag_bind(tag, "<Leave>", lambda event: self.main_text.config(cursor=""))
            self.main_text.tag_bind(tag, "<Button-1>", lambda event, target=url: self._open_info_link(target))

        self.main_text.config(state=tk.DISABLED)

    def write_log(self, log: PatchLog):
        text = log.formatted_message + "\n"
        # Disk I/O runs on the operation thread. UI delivery does not wait for Tk.
        with self._log_lock:
            if self._log_file is not None:
                self._log_file.write(text)
                self._log_file.flush()
        if self._display_log_level >= _LOG_DISPLAY_LEVELS[log.log_type]:
            self._post_ui(App._append_text, text, log.log_type)


    def _post_ui(self, method, *args, **kwargs):
        if current_thread() is main_thread():
            method(self, *args, **kwargs)
        else:
            self._ui_queue.put((None, method, args, kwargs))

    @staticmethod
    def _launch_directory() -> pathlib.Path:
        if getattr(sys, "frozen", False):
            executable = pathlib.Path(sys.executable).resolve()
            # AppImage's executable lives inside a read-only mount or temporary
            # extraction directory. Search beside the user's AppImage instead.
            if sys.platform.startswith("linux"):
                appimage, appdir = os.environ.get("APPIMAGE"), os.environ.get("APPDIR")
                if appimage and appdir and executable.is_relative_to(pathlib.Path(appdir).resolve()):
                    return pathlib.Path(appimage).resolve().parent
            if sys.platform == "darwin":
                bundle = next((p for p in executable.parents if p.suffix.lower() == ".app"), None)
                if bundle is not None:
                    return bundle.parent
            return executable.parent
        return pathlib.Path(__file__).resolve().parents[2]

    def _selected_namespace(self):
        index = self.namespaces_combobox.current()
        if not 0 <= index < len(self.namespaces):
            raise HoloPatcherError("Select a namespace option first.")
        return self.namespaces[index]

    _resolve_package_file = staticmethod(resolve_package_file)

    @on_ui_thread
    def _start_task(self, name, work: Callable[[], None], *, log_path=None,
                    log_level: LogLevel = LogLevel.FULL, preparing_install: bool = False):
        """Admit one operation, with selections captured by the caller on the UI thread."""
        if self.task_running or self._close_requested:
            self._dialog("showinfo", "Task already running", "Finish the current task before starting another.")
            return False
        try:
            # Failure to establish the chosen log aborts before game mutations.
            log_file = None
            if log_path is not None:
                destination = pathlib.Path(log_path)
                if destination.is_symlink():
                    raise OSError(f"The operation log cannot be a symbolic link: {destination}")
                log_file = destination.open("a", encoding="utf-8", buffering=1)
        except OSError as exc:
            self.exit_code = ExitCode.EXCEPTION_DURING_INSTALL
            self._handle_general_exception(exc, "Cannot open the operation log; no operation was started")
            return False
        self._log_file = log_file
        self.initialize_logger()
        self.exit_code = ExitCode.SUCCESS
        self.simple_thread_event.clear()
        self._display_log_level = log_level
        if log_level != LogLevel.NOTHING:
            self.clear_main_text()
        elif self._log_view_active:
            # A previous diagnostic task may have replaced the selected option's information.
            self.on_namespace_option_chosen(tk.Event())
        # Keep a full animation cycle; maximum=1 skips between the endpoints.
        self.progress.configure(mode="indeterminate", value=0, maximum=100)
        self.progress.start(50)
        self.progress_label.configure(text="Preparing installation..." if preparing_install else name)
        if preparing_install:
            self.preparation_label.grid()
        self.set_state(True)

        def run():
            try:
                work()
            except Exception as exc:
                self.exit_code = ExitCode.EXCEPTION_DURING_INSTALL
                self._handle_general_exception(exc, f"{name} did not complete")
            finally:
                try:
                    with self._log_lock:
                        if self._log_file is not None:
                            stream, self._log_file = self._log_file, None
                            stream.close()
                except OSError as exc:
                    self.exit_code = ExitCode.EXCEPTION_DURING_INSTALL
                    self._handle_general_exception(exc, "Could not finish writing the operation log")
                finally:
                    self._post_ui(App._finish_task)

        self.task_thread = Thread(target=run, name=f"HoloPatcher {name}")
        try:
            self.task_thread.start()
        except Exception as exc:
            self.task_thread = None
            if self._log_file is not None:
                stream, self._log_file = self._log_file, None
                stream.close()
            self.set_state(False)
            self.preparation_label.grid_remove()
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=1, value=0)
            self.progress_label.configure(text="Could not start; review the log")
            self._display_log_level = LogLevel.FULL
            self.exit_code = ExitCode.EXCEPTION_DURING_INSTALL
            self._handle_general_exception(exc, "Could not start the operation")
            return False
        return True

    def _finish_task(self):
        # Do not release ownership while a finishing worker is still alive.
        if self.task_thread is not None and self.task_thread.is_alive():
            self.after(10, self._finish_task)
            return
        self.preparation_label.grid_remove()
        final_progress = float(self.progress.cget("value"))
        indeterminate = str(self.progress.cget("mode")) == "indeterminate"
        self.progress.stop()
        if indeterminate:
            self.progress.configure(mode="determinate", maximum=1, value=0)
        else:
            self.progress.configure(value=final_progress)
        if self.simple_thread_event.is_set():
            self.progress_label.configure(text="Stopped; review the log")
        elif self.exit_code != ExitCode.SUCCESS:
            self.progress_label.configure(text="Finished with errors; review the log")
        else:
            self.progress_label.configure(text="Finished")
        self.task_thread = None
        self._display_log_level = LogLevel.FULL
        self.set_state(False)
        if self._close_requested:
            self._close_when_idle()

    def _show_progress(self, completed, total):
        self.preparation_label.grid_remove()
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=max(total, 1), value=completed)
        self.progress_label.configure(text=f"{completed} / {total} operations processed")

    def _append_text(self, text, log_type: LogType = LogType.NOTE):
        if not self._log_view_active:
            self.clear_main_text()
        self.main_text.config(state=tk.NORMAL)
        self.main_text.insert(tk.END, text, (f"_log_{log_type.name.lower()}",))
        self.main_text.see(tk.END)
        self.main_text.config(state=tk.DISABLED)

    def report_callback_exception(self, exc_type, exc, traceback_obj):
        self._handle_general_exception(exc, "A window action failed")


def onAppCrash(etype: type[BaseException], e: BaseException, tback: TracebackType | None):
    title, short_msg = universal_simplify_exception(e)
    detailed_msg = "".join(traceback.format_exception(etype, e, tback))
    if sys.stderr is not None:
        print(detailed_msg, file=sys.stderr)
    if current_thread() is not main_thread():
        return
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(title, short_msg, parent=root)
    root.destroy()
    raise SystemExit(ExitCode.EXCEPTION_DURING_INSTALL)


def main(args: Namespace) -> int:
    previous_hook = sys.excepthook
    sys.excepthook = onAppCrash
    try:
        app = App(args)
        app.mainloop()
        return int(app.exit_code)
    except Exception as exc:
        onAppCrash(type(exc), exc, exc.__traceback__)
        return ExitCode.EXCEPTION_DURING_INSTALL
    finally:
        sys.excepthook = previous_hook


if __name__ == "__main__":
    from holopatcher.__main__ import main as run_application
    raise SystemExit(run_application())
