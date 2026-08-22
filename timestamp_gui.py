"""Focused clickable timestamp MVP.

The app keeps the OBS connection/status indicator and the global timestamp
hotkey, while removing the legacy Gemini, HUD, and Resolve flows. Each new
timestamp captures a small context screenshot of the main monitor.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox

import customtkinter as ctk
from pynput import keyboard, mouse

from timestamp_audio import (
    AudioError,
    AudioRecorder,
    PlaybackController,
    TAG_NAME_MAX_LENGTH,
    TimestampEntry,
    TimestampSession,
    format_elapsed_display,
    normalize_tag_name,
    parse_time_input,
    replay_display_name,
    sanitize_project_name,
    sanitize_recent_projects,
    sanitize_tag_definitions,
    update_recent_projects,
)
from timestamp_obs import OBSManager
from timestamp_screenshot import ScreenshotError, capture_to_path


def recording_display_name(output_path: object) -> str | None:
    """Derive a segment name from an OBS recording file path.

    "E:/rec/[21-08][14-55-19].mp4" becomes "[21-08][14-55-19]"; anything
    unusable (None, empty, extension-only) yields None so callers fall back
    to a generic "Recording N" label.
    """
    if not output_path:
        return None
    basename = os.path.basename(str(output_path).strip())
    stem = os.path.splitext(basename)[0].strip()
    return stem or None


def format_entry_ref(entry: TimestampEntry) -> str:
    """User-facing reference: R02-001 inside a segment, legacy 005 otherwise."""
    if entry.recording_number is not None and entry.recording_index is not None:
        return f"R{entry.recording_number:02d}-{entry.recording_index:03d}"
    return f"{entry.id:03d}"


def elide_middle(text: str, max_chars: int) -> str:
    """Shorten long strings (paths) to max_chars, replacing the middle with …."""
    text = str(text)
    if len(text) <= max_chars:
        return text
    if max_chars < 5:
        return text[:max_chars]
    half = (max_chars - 1) // 2
    return text[: max_chars - 1 - half] + "…" + text[-half:]


class Theme:
    BG_DARKEST = "#0A0A0A"
    BG_SURFACE = "#141414"
    BG_ENTRY = "#1A1A1A"
    CRIMSON = "#DC143C"
    CRIMSON_HOVER = "#B8112F"
    GREEN = "#00E676"
    GREEN_HOVER = "#00C853"
    BLUE = "#448AFF"
    BLUE_HOVER = "#2962FF"
    RED = "#FF5252"
    RED_HOVER = "#D32F2F"
    AMBER = "#FFAB00"
    GREY = "#616161"
    GREY_HOVER = "#424242"
    VIOLET = "#AB47BC"
    VIOLET_HOVER = "#8E24AA"
    TEXT_DIM = "#AAAAAA"
    TEXT_BRIGHT = "#FFFFFF"
    BTN_SURFACE = "#1C1C1E"
    BTN_SURFACE_HOVER = "#2C2C2E"
    DIVIDER = "#2A2A2A"
    FONT_FAMILY = "Segoe UI"
    FONT_TITLE = (FONT_FAMILY, 16, "bold")
    FONT_SUBTITLE = (FONT_FAMILY, 13, "bold")
    FONT_BODY = (FONT_FAMILY, 12)
    FONT_ROW = (FONT_FAMILY, 11)
    FONT_SMALL = (FONT_FAMILY, 10)
    FONT_BUTTON = (FONT_FAMILY, 12, "bold")
    FONT_BUTTON_SMALL = (FONT_FAMILY, 11)


def get_base_path() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# Mouse buttons pynput reports as "x1" (mouse 4) and "x2" (mouse 5), plus
# middle click. Left/right are excluded on purpose: they are far too commonly
# clicked to be a safe global hotkey.
MOUSE_HOTKEYS = {"middle": "Middle click", "x1": "Mouse 4", "x2": "Mouse 5"}

# Tags offered in the timestamp edit dialog when keybinds.json defines none.
# Users can replace them via the "tags" list in keybinds.json; each entry is
# {"name": str, "color": "#RRGGBB"}.
DEFAULT_TAGS = [
    {"name": "kill", "color": "#FF5252"},
    {"name": "bug", "color": "#FFAB00"},
    {"name": "idea", "color": "#448AFF"},
]
TAG_COLOR_FALLBACK = Theme.GREY

# The output-folder path is shown elided in the settings row so long paths
# cannot push the Browse/Hotkey controls out of the window.
FOLDER_LABEL_MAX_CHARS = 40

# Compact-row meta limits: at most this many tag chips are shown inline,
# plus a "+N" overflow chip; long labels get middle-elided.
ROW_TAG_CHIPS = 3
ROW_LABEL_MAX_CHARS = 34

# A hotkey press only re-fires after this many seconds. This swallows OS
# auto-repeat, and — more importantly — means a missed release event can never
# permanently disable a key: after this window a fresh press works again.
KEY_REPEAT_GUARD_SECONDS = 0.3


class TimestampApp:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title("Clickable Timestamp Recorder")
        self.root.geometry("720x560")
        self.root.minsize(600, 460)
        self.root.configure(fg_color=Theme.BG_DARKEST)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(2, weight=1)

        self.base_path = get_base_path()
        self.config_path = os.path.join(self.base_path, "keybinds.json")
        self.config = self._load_config()
        self.output_folder = self._initial_output_folder()
        self.obs_settings = self.config.get(
            "obs_settings",
            {"host": "localhost", "port": 4455, "password": ""},
        )
        self.timestamp_key = (
            self.config.get("keybinds", {}).get("mark_time") or "f15"
        )
        self.saved_device = self.config.get("audio_device")
        self.saved_project_name = self.config.get("project_name", "")
        self.recent_projects = sanitize_recent_projects(
            self.config.get("recent_projects")
        )
        self._recent_popup: ctk.CTkToplevel | None = None
        self._popup_bind_id: str | None = None

        self.session: TimestampSession | None = None
        self.obs_manager = OBSManager()
        self.recorder = AudioRecorder()
        self.playback = PlaybackController()
        self.device_choices: dict[str, int | None] = {}
        self.recording_entry_id: int | None = None
        self._playback_job = None
        self._pressed_keys: dict[str, float] = {}
        self._capturing_key = False
        self._key_capture_listener = None
        self._mouse_capture_listener = None
        self._mouse_listener = None
        self._closing = False
        self.tag_definitions = self._load_tag_definitions()
        self._edit_dialog: TimestampEditDialog | None = None
        self._manual_dialog: ManualTimestampDialog | None = None
        self._tag_dialog: TagManagerDialog | None = None
        self._overlay_stack: list = []
        # Timestamp-list repaint state: cached row/header/footer widgets so
        # refreshes reconfigure in place instead of destroying and recreating
        # everything (the old full-rebuild behavior flickered on every
        # interaction).
        self._list_rows: dict[int, dict] = {}
        self._header_labels: dict[object, ctk.CTkLabel] = {}
        self._footer_labels: dict[object, ctk.CTkLabel] = {}
        self._empty_label: ctk.CTkLabel | None = None
        self._list_rows_session: TimestampSession | None = None
        self._list_refresh_job: str | None = None
        # One-shot: force the next list refresh to scroll to the newest entry
        # regardless of the current scroll position (set on project load).
        self._scroll_to_bottom_on_next_refresh = False

        self._create_widgets()
        self._setup_overlay_dispatch()
        self._setup_obs()
        self._refresh_devices()
        self._refresh_timestamp_list()
        self._update_action_state()
        self._start_keyboard_listener()
        self._start_mouse_listener()
        self._update_clock()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # ── Configuration ────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        try:
            with open(self.config_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _initial_output_folder(self) -> str:
        configured = self.config.get("output_folder")
        if configured and os.path.isdir(configured):
            return configured
        return os.path.join(self.base_path, "Timestamp_Audio")

    def _save_config(self) -> None:
        data = {
            "keybinds": {"mark_time": self.timestamp_key},
            "output_folder": self.output_folder,
            "project_name": self.project_name_var.get().strip(),
            "obs_settings": self.obs_settings,
            "audio_device": self._selected_device_index(),
            "tags": self.tag_definitions,
            "recent_projects": self.recent_projects,
        }
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=4)

    # ── Tag library ──────────────────────────────────────────────────────────

    def _load_tag_definitions(self) -> list[dict]:
        """Read preset tags from keybinds.json, falling back to defaults."""
        return sanitize_tag_definitions(
            self.config.get("tags"),
            DEFAULT_TAGS,
            fallback_color=TAG_COLOR_FALLBACK,
        )

    def _tag_usage_count(self, tag_name: str) -> int:
        """How many entries in the open session carry this tag (any case)."""
        if not self.session:
            return 0
        key = str(tag_name).lower()
        return sum(
            1
            for entry in self.session.entries
            if any(tag.lower() == key for tag in entry.tags)
        )

    def _open_tag_manager(self, focus_name: bool = False) -> None:
        """Open the tag-library manager (one instance at a time)."""
        if self._tag_dialog is not None and self._tag_dialog.winfo_exists():
            try:
                self._tag_dialog._focus()
            except tk.TclError:
                pass
            return
        editor = self._edit_dialog
        self._tag_dialog = TagManagerDialog(
            self,
            definitions=self.tag_definitions,
            usage_lookup=self._tag_usage_count,
            on_add=self._tag_library_add,
            on_rename=self._tag_library_rename,
            on_delete=self._tag_library_delete,
            on_recolor=self._tag_library_recolor,
            on_close=lambda: self._refocus_edit_dialog(editor),
            focus_name=focus_name,
        )

    @staticmethod
    def _refocus_edit_dialog(editor) -> None:
        """Re-grab the timestamp edit dialog after the manager closes."""
        if editor is not None and editor.winfo_exists():
            try:
                editor._focus()
            except tk.TclError:
                pass

    def _apply_tag_library_change(self) -> None:
        """Sync every GUI surface after a tag-library mutation."""
        self._schedule_list_refresh()
        if self._edit_dialog is not None and self._edit_dialog.winfo_exists():
            self._edit_dialog.refresh_tags(self.tag_definitions)

    def _tag_library_add(self, name: str, color: str) -> None:
        self.tag_definitions.append({"name": name, "color": color})
        self._save_config()
        self._apply_tag_library_change()

    def _tag_library_rename(self, old_name: str, new_name: str) -> None:
        for definition in self.tag_definitions:
            if definition["name"].lower() == old_name.lower():
                definition["name"] = new_name
        self._save_config()
        if self.session:
            self.session.rename_tag(old_name, new_name)
        self._apply_tag_library_change()

    def _tag_library_delete(self, name: str) -> None:
        self.tag_definitions = [
            definition
            for definition in self.tag_definitions
            if definition["name"].lower() != name.lower()
        ]
        self._save_config()
        if self.session:
            self.session.remove_tag(name)
        self._apply_tag_library_change()

    def _tag_library_recolor(self, name: str, color: str) -> None:
        for definition in self.tag_definitions:
            if definition["name"].lower() == name.lower():
                definition["color"] = color
        self._save_config()
        self._apply_tag_library_change()

    def _tag_color(self, tag_name: str) -> str:
        for definition in self.tag_definitions:
            if definition["name"].lower() == tag_name.lower():
                return definition["color"]
        return TAG_COLOR_FALLBACK

    # ── GUI ───────────────────────────────────────────────────────────────────

    def _create_widgets(self) -> None:
        self._create_header()
        self._create_controls()
        self._create_timestamp_list()
        self._create_footer()

    def _create_header(self) -> None:
        """Single-row header: title · REC · clock · OBS status · Tags · Connect."""
        header = ctk.CTkFrame(self.root, fg_color=Theme.BG_SURFACE, corner_radius=12)
        header.grid(row=0, column=0, padx=14, pady=(14, 6), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="Clickable Timestamp Recorder",
            font=Theme.FONT_TITLE,
            text_color=Theme.TEXT_BRIGHT,
            anchor="w",
        ).grid(row=0, column=0, padx=(14, 8), pady=8, sticky="w")

        self.rec_indicator_label = ctk.CTkLabel(
            header,
            text="",
            font=(Theme.FONT_FAMILY, 11, "bold"),
            text_color=Theme.GREEN,
        )
        self.rec_indicator_label.grid(row=0, column=1, padx=(0, 10), pady=8, sticky="e")

        self.clock_label = ctk.CTkLabel(
            header,
            text="00:00:00",
            font=(Theme.FONT_FAMILY, 18, "bold"),
            text_color=Theme.CRIMSON,
        )
        self.clock_label.grid(row=0, column=2, padx=(0, 12), pady=8, sticky="e")

        self.obs_status_label = ctk.CTkLabel(
            header, text="● OBS disconnected", font=Theme.FONT_SMALL, text_color=Theme.RED
        )
        self.obs_status_label.grid(row=0, column=3, padx=(0, 10), pady=8, sticky="e")

        self.tags_manage_button = ctk.CTkButton(
            header,
            text="🏷 Tags",
            width=64,
            height=26,
            font=Theme.FONT_SMALL,
            fg_color=Theme.BTN_SURFACE,
            hover_color=Theme.BTN_SURFACE_HOVER,
            command=lambda: self._open_tag_manager(),
        )
        self.tags_manage_button.grid(row=0, column=4, padx=(0, 6), pady=6, sticky="e")

        self.obs_connect_button = ctk.CTkButton(
            header,
            text="Connect OBS",
            width=100,
            height=26,
            font=Theme.FONT_SMALL,
            fg_color=Theme.GREY,
            hover_color=Theme.GREY_HOVER,
            command=self._toggle_obs_connection,
        )
        self.obs_connect_button.grid(row=0, column=5, padx=(0, 14), pady=6, sticky="e")

    def _create_controls(self) -> None:
        """Settings in two dense rows: project, then mic/folder/hotkey."""
        controls = ctk.CTkFrame(self.root, fg_color="transparent")
        controls.grid(row=1, column=0, padx=14, pady=(0, 4), sticky="ew")
        controls.grid_columnconfigure(0, weight=1)

        # Row 1: project name (clicking the field offers recent projects;
        # typing stays normal).
        row_a = ctk.CTkFrame(controls, fg_color="transparent")
        row_a.grid(row=0, column=0, sticky="ew", pady=(0, 3))
        row_a.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            row_a, text="Project", font=Theme.FONT_SMALL,
            text_color=Theme.TEXT_DIM, anchor="w",
        ).grid(row=0, column=0, padx=(0, 6), pady=3, sticky="w")
        self.project_name_var = tk.StringVar(value=self.saved_project_name)
        self.project_name_entry = ctk.CTkEntry(
            row_a,
            textvariable=self.project_name_var,
            placeholder_text="Enter project name before recording",
            font=Theme.FONT_BODY,
            height=28,
        )
        self.project_name_entry.grid(row=0, column=1, padx=0, pady=3, sticky="ew")
        self.project_name_entry.bind("<Button-1>", self._on_project_entry_click)
        self.project_name_entry.bind("<Key>", lambda _event: self._close_recent_popup())
        self.project_name_entry.bind("<Escape>", lambda _event: self._close_recent_popup())
        ctk.CTkButton(
            row_a,
            text="Set project",
            width=86,
            height=28,
            font=Theme.FONT_BUTTON,
            command=self._set_project,
        ).grid(row=0, column=2, padx=(6, 0), pady=3)

        # Row 2: microphone · output folder · timestamp hotkey.
        row_b = ctk.CTkFrame(controls, fg_color="transparent")
        row_b.grid(row=1, column=0, sticky="ew")
        row_b.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(
            row_b, text="Mic", font=Theme.FONT_SMALL,
            text_color=Theme.TEXT_DIM, anchor="w",
        ).grid(row=0, column=0, padx=(0, 6), pady=3, sticky="w")
        self.device_var = tk.StringVar(value="System default")
        self.device_menu = ctk.CTkOptionMenu(
            row_b,
            variable=self.device_var,
            values=["System default"],
            font=Theme.FONT_BODY,
            height=28,
            dynamic_resizing=False,
        )
        self.device_menu.grid(row=0, column=1, pady=3, sticky="w")
        ctk.CTkButton(
            row_b,
            text="Refresh",
            width=66,
            height=28,
            font=Theme.FONT_BUTTON,
            fg_color=Theme.GREY,
            hover_color=Theme.GREY_HOVER,
            command=self._refresh_devices,
        ).grid(row=0, column=2, padx=(6, 10), pady=3)
        self.folder_label = ctk.CTkLabel(
            row_b,
            text=elide_middle(self.output_folder, FOLDER_LABEL_MAX_CHARS),
            font=Theme.FONT_SMALL,
            text_color=Theme.TEXT_DIM,
            anchor="w",
        )
        self.folder_label.grid(row=0, column=3, padx=(0, 6), pady=3, sticky="ew")
        ctk.CTkButton(
            row_b,
            text="Browse",
            width=66,
            height=28,
            font=Theme.FONT_BUTTON,
            command=self._choose_output_folder,
        ).grid(row=0, column=4, padx=(0, 10), pady=3)
        ctk.CTkLabel(
            row_b, text="Hotkey", font=Theme.FONT_SMALL,
            text_color=Theme.TEXT_DIM, anchor="w",
        ).grid(row=0, column=5, padx=(0, 6), pady=3, sticky="w")
        self.hotkey_button = ctk.CTkButton(
            row_b,
            text=self._hotkey_display(self.timestamp_key),
            width=104,
            height=28,
            font=Theme.FONT_BUTTON,
            fg_color=Theme.GREY,
            hover_color=Theme.GREY_HOVER,
            command=self._change_timestamp_key,
        )
        self.hotkey_button.grid(row=0, column=6, pady=3, sticky="e")

    def _create_timestamp_list(self) -> None:
        list_frame = ctk.CTkFrame(self.root, fg_color=Theme.BG_SURFACE, corner_radius=12)
        list_frame.grid(row=2, column=0, padx=14, pady=6, sticky="nsew")
        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(1, weight=1)

        # Compact toolbar: no section title, just the three controls.
        toolbar = ctk.CTkFrame(list_frame, fg_color="transparent")
        toolbar.grid(row=0, column=0, padx=10, pady=(8, 6), sticky="e")
        self.timer_toggle_button = ctk.CTkButton(
            toolbar,
            text="▶ Start timer",
            width=104,
            height=28,
            font=Theme.FONT_BUTTON,
            fg_color=Theme.GREEN,
            hover_color=Theme.GREEN_HOVER,
            command=self._toggle_timer,
            state="disabled",
        )
        self.timer_toggle_button.grid(row=0, column=0, padx=(8, 4), sticky="e")

        self.new_timestamp_button = ctk.CTkButton(
            toolbar,
            text=f"＋ New timestamp [{self._hotkey_display(self.timestamp_key)}]",
            width=186,
            height=28,
            font=Theme.FONT_BUTTON,
            fg_color=Theme.CRIMSON,
            hover_color=Theme.CRIMSON_HOVER,
            command=self.create_timestamp,
            state="disabled",
        )
        self.new_timestamp_button.grid(row=0, column=1, padx=4, sticky="e")

        self.manual_timestamp_button = ctk.CTkButton(
            toolbar,
            text="＋ Manual",
            width=84,
            height=28,
            font=Theme.FONT_BUTTON,
            fg_color=Theme.GREY,
            hover_color=Theme.GREY_HOVER,
            command=self._open_manual_dialog,
            state="disabled",
        )
        self.manual_timestamp_button.grid(row=0, column=2, padx=(4, 0), sticky="e")

        self.timestamp_list = ctk.CTkScrollableFrame(
            list_frame, fg_color=Theme.BG_ENTRY, corner_radius=8
        )
        self.timestamp_list.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self.timestamp_list.grid_columnconfigure(0, weight=1)

    def _create_footer(self) -> None:
        self.status_label = ctk.CTkLabel(
            self.root,
            text="Create a timestamp, then click it to record a microphone note.",
            font=Theme.FONT_SMALL,
            text_color=Theme.TEXT_DIM,
            anchor="w",
        )
        self.status_label.grid(row=3, column=0, padx=16, pady=(0, 14), sticky="ew")

    def _set_status(self, message: str, color=Theme.TEXT_DIM) -> None:
        self.status_label.configure(text=message, text_color=color)

    # ── Device and folder controls ────────────────────────────────────────────

    def _refresh_devices(self) -> None:
        try:
            devices = AudioRecorder.list_devices()
            self.device_choices = {name: index for index, name in devices}
            names = list(self.device_choices) or ["System default"]
            self.device_menu.configure(values=names)

            saved_name = next(
                (name for name, index in self.device_choices.items() if index == self.saved_device),
                names[0],
            )
            self.device_var.set(saved_name)
            self._set_status(f"Found {len(devices) - 1} microphone device(s).")
        except AudioError as exc:
            self.device_choices = {"System default": None}
            self.device_menu.configure(values=["System default"])
            self.device_var.set("System default")
            self._set_status(str(exc), Theme.AMBER)

    def _selected_device_index(self) -> int | None:
        return self.device_choices.get(self.device_var.get())

    def _change_timestamp_key(self) -> None:
        if self._capturing_key:
            return
        self._capturing_key = True
        self._pressed_keys.clear()
        self.hotkey_button.configure(
            text="Press a key or mouse button… (Esc cancels)", state="disabled"
        )
        unknown_notified = False

        def on_capture(key):
            nonlocal unknown_notified
            key_string = self._key_string(key)
            if key_string == "esc":
                self.root.after(0, lambda: self._finish_key_capture(None))
                return False
            if key_string in ("unknown", "ctrl", "shift", "alt", "cmd"):
                if key_string == "unknown" and not unknown_notified:
                    unknown_notified = True
                    self.root.after(
                        0,
                        lambda: self._set_status(
                            "Key not recognized by Windows — try another key.",
                            Theme.AMBER,
                        ),
                    )
                return
            self.root.after(0, lambda value=key_string: self._finish_key_capture(value))
            return False

        def on_capture_mouse(x, y, button, pressed):
            if not pressed:
                return
            name = self._key_string(button)
            if name not in MOUSE_HOTKEYS:
                return
            self.root.after(0, lambda value=name: self._finish_key_capture(value))
            return False

        self._key_capture_listener = keyboard.Listener(on_press=on_capture)
        self._key_capture_listener.daemon = True
        self._key_capture_listener.start()

        self._mouse_capture_listener = mouse.Listener(on_click=on_capture_mouse)
        self._mouse_capture_listener.daemon = True
        self._mouse_capture_listener.start()

    def _finish_key_capture(self, key_string: str | None) -> None:
        for listener in (self._key_capture_listener, self._mouse_capture_listener):
            if listener and listener.running:
                listener.stop()
        self._key_capture_listener = None
        self._mouse_capture_listener = None
        self._capturing_key = False
        self._pressed_keys.clear()
        if key_string:
            self.timestamp_key = key_string
            self._save_config()
            display = self._hotkey_display(key_string)
            self.hotkey_button.configure(text=display, state="normal")
            self.new_timestamp_button.configure(
                text=f"＋ New timestamp [{display}]"
            )
            self._set_status(f"Timestamp hotkey changed to {display}.", Theme.GREEN)
        else:
            self.hotkey_button.configure(
                text=self._hotkey_display(self.timestamp_key), state="normal"
            )
            self._set_status("Timestamp hotkey change cancelled.")

    def _set_project(self) -> None:
        if self.recorder.active:
            messagebox.showwarning(
                "Recording active",
                "Stop the current timestamp recording before changing projects.",
                parent=self.root,
            )
            return

        project_name = self.project_name_var.get().strip()
        if not project_name:
            messagebox.showwarning(
                "Project name required",
                "Enter a project name before starting an OBS recording.",
                parent=self.root,
            )
            return

        if self.session:
            self.session.save()
        safe_name = sanitize_project_name(project_name)
        project_folder = os.path.join(self.output_folder, safe_name)
        self.session = TimestampSession(project_folder, project_name=project_name)
        self.session.save()
        self.project_name_var.set(project_name)
        self.recent_projects = update_recent_projects(
            self.recent_projects, project_name, self.output_folder
        )
        self._save_config()
        # Land on the latest entries whenever a project opens/switches,
        # even though the pre-refresh view may sit mid-list.
        self._scroll_to_bottom_on_next_refresh = True
        self._schedule_list_refresh()
        self._update_action_state()
        self._set_status(
            f"Project '{project_name}' ready. Start recording in OBS.", Theme.BLUE
        )

        # If OBS was already recording when the project was selected, begin
        # timing immediately instead of waiting for a future OBS event.
        if self.obs_manager.is_recording:
            self._start_obs_timer()

    def _choose_output_folder(self) -> None:
        if self.recorder.active:
            messagebox.showwarning(
                "Recording active",
                "Stop the current recording before changing the audio folder.",
                parent=self.root,
            )
            return

        chosen = filedialog.askdirectory(
            title="Choose project output folder", initialdir=self.output_folder
        )
        if not chosen:
            return

        if self.session:
            self.session.save()
        self.output_folder = chosen
        self.session = None
        self.folder_label.configure(
            text=elide_middle(chosen, FOLDER_LABEL_MAX_CHARS)
        )
        self._save_config()
        self._schedule_list_refresh()
        self._update_action_state()
        self._set_status("Choose a project name, then click Set project.")

    # ── Recent projects popup ───────────────────────────────────────────────

    def _on_project_entry_click(self, _event=None) -> None:
        """Toggle the recent-projects popup below the project-name field."""
        if self._recent_popup is not None and self._recent_popup.winfo_exists():
            self._close_recent_popup()
            return
        self._open_recent_popup()

    def _open_recent_popup(self) -> None:
        """Show up to five recent projects as clickable rows under the entry."""
        if self._closing or not self.recent_projects:
            return
        popup = ctk.CTkToplevel(self.root)
        popup.wm_overrideredirect(True)
        popup.configure(fg_color=Theme.BG_SURFACE, corner_radius=10)
        ctk.CTkLabel(
            popup,
            text="Recent projects",
            font=Theme.FONT_SMALL,
            text_color=Theme.TEXT_DIM,
            anchor="w",
        ).pack(fill="x", padx=14, pady=(8, 2))
        for entry in self.recent_projects:
            name = str(entry.get("name", ""))
            folder = str(entry.get("output_folder", ""))
            ctk.CTkButton(
                popup,
                text=f"{name}   ·   {folder}",
                anchor="w",
                height=34,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BG_ENTRY,
                hover_color=Theme.BTN_SURFACE_HOVER,
                text_color=Theme.TEXT_BRIGHT,
                command=lambda n=name, f=folder: self._load_recent_project(n, f),
            ).pack(fill="x", padx=8, pady=2)

        width = max(430, self.project_name_entry.winfo_width())
        row_count = len(self.recent_projects)
        height = 48 + row_count * 38 + 10
        entry_x = self.project_name_entry.winfo_rootx()
        entry_y = self.project_name_entry.winfo_rooty()
        x = max(
            self.root.winfo_rootx(),
            min(entry_x, self.root.winfo_rootx() + self.root.winfo_width() - width - 8),
        )
        y = entry_y + self.project_name_entry.winfo_height() + 6
        bottom_limit = self.root.winfo_rooty() + self.root.winfo_height()
        if y + height > bottom_limit - 8:
            y = max(self.root.winfo_rooty() + 8, entry_y - height - 6)
        popup.geometry(f"{width}x{height}+{x}+{y}")

        self._recent_popup = popup
        # Any click elsewhere in the main window dismisses the popup.
        self._popup_bind_id = self.root.bind(
            "<Button-1>", self._on_root_click_during_popup, add="+"
        )

    def _close_recent_popup(self) -> None:
        """Dismiss the recent-projects popup if it is showing."""
        popup = self._recent_popup
        self._recent_popup = None
        bind_id = getattr(self, "_popup_bind_id", None)
        if bind_id:
            try:
                self.root.unbind("<Button-1>", bind_id)
            except KeyError:
                pass
            self._popup_bind_id = None
        if popup is not None and popup.winfo_exists():
            popup.destroy()

    def _on_root_click_during_popup(self, event) -> None:
        popup = self._recent_popup
        if popup is None or not popup.winfo_exists():
            return
        widget = getattr(event, "widget", None)
        while widget is not None:
            if widget is popup:
                return
            widget = getattr(widget, "master", None)
        self._close_recent_popup()

    def _load_recent_project(self, name: str, output_folder: str) -> None:
        """Point the app at a stored project and open its session."""
        self._close_recent_popup()
        if self.recorder.active:
            messagebox.showwarning(
                "Recording active",
                "Stop the current timestamp recording before changing projects.",
                parent=self.root,
            )
            return
        if self._closing:
            return
        self.output_folder = output_folder
        self.folder_label.configure(
            text=elide_middle(output_folder, FOLDER_LABEL_MAX_CHARS)
        )
        self.project_name_var.set(name)
        self._set_project()

    # ── Timestamp actions ─────────────────────────────────────────────────────

    def create_timestamp(self) -> None:
        if self._closing or not self.session or not self.session.timer_running:
            self._set_status(
                "Start the timer (in OBS or with ▶ Start timer) before creating timestamps.",
                Theme.AMBER,
            )
            return
        entry = self.session.create_timestamp()
        self._schedule_list_refresh()
        self._set_status(
            f"Timestamp {format_entry_ref(entry)} created at "
            f"{format_elapsed_display(entry.elapsed_seconds)}. Click it to record.",
            Theme.BLUE,
        )
        self._capture_screenshot_for_entry(entry)

    def _toggle_timer(self) -> None:
        """Start/stop the project timer manually, mirroring OBS semantics.

        Uses the exact model calls the OBS event handlers use, so manual runs
        get numbered "Recording N" segments, per-segment indices restart at
        001, and stopping locks new-timestamp creation until the next start.
        """
        if not self.session:
            return
        if self.session.timer_running:
            self.session.stop_timer()
            self._update_action_state()
            self._schedule_list_refresh()
            self._set_status(
                f"Timer stopped for '{self.session.project_name}'. Project session locked.",
                Theme.BLUE,
            )
        else:
            self.session.start_timer()
            segment = self.session.current_recording
            segment_label = f" ({segment.header()})" if segment else ""
            self._update_action_state()
            self._schedule_list_refresh()
            self._set_status(
                f"Timer started for '{self.session.project_name}'{segment_label}.",
                Theme.GREEN,
            )

    def _click_timestamp(self, entry_id: int) -> None:
        # Rows stay usable without a running timer: pending/error rows record,
        # recording rows stop, completed rows play. Only *creating* timestamps
        # is gated on an active timer.
        if not self.session:
            return
        entry = self.session.get(entry_id)
        if not entry:
            return

        if entry.status in ("pending", "error"):
            self._start_entry_recording(entry)
        elif entry.status == "recording":
            self._stop_entry_recording(entry)
        elif entry.status == "completed":
            self._toggle_playback(entry)

    def _start_entry_recording(self, entry: TimestampEntry) -> None:
        if self.recorder.active:
            self._set_status("Stop the current timestamp recording first.", Theme.AMBER)
            return

        output_path = self.session.audio_path(entry)
        try:
            self.recorder.start(output_path, self._selected_device_index())
        except AudioError as exc:
            self.session.mark_error(entry, str(exc))
            self._schedule_list_refresh()
            self._set_status(str(exc), Theme.RED)
            return

        self.recording_entry_id = entry.id
        self.session.mark_recording(entry)
        self._schedule_list_refresh()
        self._set_status(
            f"Recording timestamp {format_entry_ref(entry)}. Click it again to stop.",
            Theme.RED,
        )

    def _stop_entry_recording(self, entry: TimestampEntry) -> None:
        if self.recording_entry_id != entry.id:
            self.session.mark_error(entry, "Recording state is no longer active")
            self._schedule_list_refresh()
            return

        try:
            output_path, duration = self.recorder.stop()
            self.session.mark_completed(entry, output_path, duration)
            self.recording_entry_id = None
            self._schedule_list_refresh()
            self._set_status(
                f"Saved {os.path.basename(output_path)} ({duration:.1f}s).", Theme.GREEN
            )
        except AudioError as exc:
            self.session.mark_error(entry, str(exc))
            self.recording_entry_id = None
            self._schedule_list_refresh()
            self._set_status(str(exc), Theme.RED)

    def _toggle_playback(self, entry: TimestampEntry) -> None:
        if not entry.audio_file:
            self._set_status("This timestamp has no saved audio file.", Theme.RED)
            return

        if not self.session:
            return
        path = os.path.join(self.session.output_dir, entry.audio_file)
        if self.playback.current_path == path:
            self.playback.stop()
            if self._playback_job:
                self.root.after_cancel(self._playback_job)
                self._playback_job = None
            self._schedule_list_refresh()
            self._set_status("Playback stopped.")
            return

        try:
            duration = self.playback.play(path)
        except AudioError as exc:
            self._set_status(str(exc), Theme.RED)
            return

        self._schedule_list_refresh()
        self._set_status(f"Playing {os.path.basename(path)}.", Theme.BLUE)
        if self._playback_job:
            self.root.after_cancel(self._playback_job)
        self._playback_job = self.root.after(
            max(100, int(duration * 1000) + 100), self._finish_playback
        )

    def _finish_playback(self) -> None:
        self.playback.finish()
        self._playback_job = None
        self._schedule_list_refresh()

    # ── Screenshot capture ────────────────────────────────────────────────────

    def _capture_screenshot_for_entry(self, entry: TimestampEntry) -> None:
        """Capture a context screenshot off the Tk thread, then attach it.

        The path is reserved synchronously; pixels are grabbed and encoded on a
        short-lived daemon thread so hotkey handling never stalls. The result
        is attached via root.after(0, ...) like every other callback-originated
        change, which also serializes rapid consecutive captures safely.
        """
        if not self.session or self._closing:
            return
        session = self.session
        path = session.screenshot_path(entry)
        output_dir = session.output_dir

        def worker() -> None:
            try:
                capture_to_path(path)
            except ScreenshotError as exc:
                message = str(exc)
                self._marshal(lambda: self._on_screenshot_failed(message))
                return
            relative = os.path.relpath(path, output_dir)
            self._marshal(
                lambda: self._on_screenshot_captured(session, entry.id, relative)
            )

        threading.Thread(target=worker, daemon=True, name="screenshot").start()

    def _marshal(self, action) -> None:
        """Run action on the Tk thread; ignore calls during shutdown."""
        try:
            self.root.after(0, action)
        except (tk.TclError, RuntimeError):
            pass

    def _on_screenshot_captured(
        self,
        session: TimestampSession,
        entry_id: int,
        relative_path: str,
    ) -> None:
        if self._closing or not self.session:
            return
        if session is not self.session:
            # The capture began under a project that has since been switched;
            # its JPEG belongs to the old project and must never attach here.
            return
        entry = session.get(entry_id)
        if entry is None:
            # The entry was deleted while the capture ran — remove the orphan.
            try:
                os.remove(os.path.join(session.output_dir, relative_path))
            except OSError:
                pass
            return
        entry.screenshot_file = relative_path
        session.save()
        self._schedule_list_refresh()
        self._set_status(
            f"Screenshot attached to {format_entry_ref(entry)}.", Theme.GREEN
        )

    def _on_screenshot_failed(self, message: str) -> None:
        if self._closing:
            return
        # The timestamp itself stays valid; only its snapshot is missing.
        self._set_status(f"Screenshot failed: {message}", Theme.AMBER)

    # ── Timestamp edit, delete, and manual creation ──────────────────────────

    def _open_edit_dialog(self, entry_id: int) -> None:
        if not self.session:
            return
        entry = self.session.get(entry_id)
        if not entry:
            return
        if entry.status == "recording":
            self._set_status("Stop recording this timestamp before editing it.", Theme.AMBER)
            return
        if self._edit_dialog is not None and self._edit_dialog.winfo_exists():
            try:
                self._edit_dialog._focus()
            except tk.TclError:
                pass
            return
        self._edit_dialog = TimestampEditDialog(
            self,
            entry,
            self.tag_definitions,
            on_save=lambda label, tags: self._apply_entry_edit(entry_id, label, tags),
            on_manage_tags=lambda: self._open_tag_manager(focus_name=True),
        )

    def _apply_entry_edit(self, entry_id: int, label: str, tags: list[str]) -> None:
        if not self.session or self._closing:
            return
        try:
            self.session.update_entry(entry_id, label, tags)
        except (KeyError, ValueError) as exc:
            self._set_status(str(exc), Theme.RED)
            return
        self._schedule_list_refresh()
        self._set_status(f"Updated timestamp {format_entry_ref(entry)}.", Theme.GREEN)

    def _delete_timestamp(self, entry_id: int) -> None:
        if not self.session:
            return
        entry = self.session.get(entry_id)
        if not entry:
            return
        if entry.status == "recording":
            self._set_status("Stop recording this timestamp before deleting it.", Theme.AMBER)
            return

        named = f' "{entry.label}"' if entry.label else ""
        if entry.kind == "replay":
            message = (
                f"Delete replay entry {format_entry_ref(entry)} "
                f"({replay_display_name(entry.replay_file)}) from the project log?"
            )
        else:
            message = (
                f"Delete timestamp {format_entry_ref(entry)} at "
                f"{format_elapsed_display(entry.elapsed_seconds)}{named}"
                " from the project log?"
            )
        if entry.audio_file:
            message += "\n\nIts WAV file will be deleted from disk."
        if entry.screenshot_file:
            message += "\nIts screenshot will be deleted from disk."
        if entry.kind == "replay" and entry.replay_file:
            message += "\nThe linked replay video stays on disk."
        if not messagebox.askyesno("Delete timestamp", message, parent=self.root):
            return

        self._stop_playback_of(entry)
        try:
            _, problems = self.session.remove_entry(entry_id)
        except (KeyError, ValueError) as exc:
            self._set_status(str(exc), Theme.RED)
            return
        self._schedule_list_refresh()
        ref = format_entry_ref(entry)
        if problems:
            self._set_status(
                f"Deleted timestamp {ref}, but some files stayed on disk: "
                + "; ".join(problems),
                Theme.AMBER,
            )
        else:
            self._set_status(f"Deleted timestamp {ref}.", Theme.GREEN)

    def _stop_playback_of(self, entry: TimestampEntry) -> None:
        """Stop playback when the entry's WAV is the one currently playing."""
        if not entry.audio_file or not self.playback.current_path or not self.session:
            return
        entry_path = os.path.abspath(os.path.join(self.session.output_dir, entry.audio_file))
        if entry_path != os.path.abspath(self.playback.current_path):
            return
        if self._playback_job:
            self.root.after_cancel(self._playback_job)
            self._playback_job = None
        self.playback.stop()

    def _open_replay_video(self, entry_id: int) -> None:
        """Open the OBS replay video of an entry in the default player."""
        if not self.session:
            return
        entry = self.session.get(entry_id)
        if not entry or not entry.replay_file:
            return
        path = entry.replay_file
        if not os.path.isfile(path):
            self._set_status(f"Replay video not found: {path}", Theme.RED)
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([opener, path])
        except Exception as exc:
            self._set_status(f"Could not open replay video: {exc}", Theme.RED)
            return
        self._set_status(
            f"Opened replay video {os.path.basename(path)}.", Theme.BLUE
        )

    def _open_screenshot(self, entry_id: int) -> None:
        """Open an entry's context screenshot in the default image viewer."""
        if not self.session:
            return
        entry = self.session.get(entry_id)
        if not entry or not entry.screenshot_file:
            return
        # Stored relative to the project folder (Screenshots/<name>.jpg).
        path = os.path.join(self.session.output_dir, entry.screenshot_file)
        if not os.path.isfile(path):
            self._set_status(f"Screenshot not found: {path}", Theme.RED)
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([opener, path])
        except Exception as exc:
            self._set_status(f"Could not open screenshot: {exc}", Theme.RED)
            return
        self._set_status(
            f"Opened screenshot {os.path.basename(path)}.", Theme.BLUE
        )

    def _open_manual_dialog(self) -> None:
        if self._closing or not self.session:
            return
        if self._manual_dialog is not None and self._manual_dialog.winfo_exists():
            try:
                self._manual_dialog._focus()
            except tk.TclError:
                pass
            return
        self._manual_dialog = ManualTimestampDialog(
            self, on_create=self._create_manual_timestamp
        )

    def _create_manual_timestamp(self, time_text: str) -> bool:
        """Create a timestamp from typed time text; False keeps the dialog open."""
        if self._closing or not self.session:
            return True
        seconds = parse_time_input(time_text)
        if seconds is None:
            self._set_status("Enter a time as SS, MM:SS, or HH:MM:SS.", Theme.AMBER)
            return False
        entry = self.session.create_timestamp(elapsed_seconds=seconds)
        self._schedule_list_refresh()
        self._set_status(
            f"Manual timestamp {format_entry_ref(entry)} created at "
            f"{format_elapsed_display(entry.elapsed_seconds)}.",
            Theme.BLUE,
        )
        self._capture_screenshot_for_entry(entry)
        return True

    # ── Timestamp list ────────────────────────────────────────────────────────

    def _schedule_list_refresh(self) -> None:
        """Coalesce list repaints: a burst of state changes produces one
        refresh ~30 ms later instead of one full pass per mutation."""
        if self._closing or self._list_refresh_job is not None:
            return
        try:
            self._list_refresh_job = self.root.after(
                30, self._run_scheduled_list_refresh
            )
        except tk.TclError:
            self._list_refresh_job = None

    def _run_scheduled_list_refresh(self) -> None:
        self._list_refresh_job = None
        if not self._closing:
            self._refresh_timestamp_list()

    def _timestamp_list_canvas(self) -> tk.Canvas | None:
        """The CTkScrollableFrame's internal canvas, defensively resolved.

        CustomTkinter exposes no public scrolling API; ``_parent_canvas`` is
        the conventional handle, so every use is guarded and fails open.
        """
        return getattr(self.timestamp_list, "_parent_canvas", None)

    def _timestamp_list_near_bottom(self) -> bool:
        """True when the list view sits at (or within ~2% of) the bottom.

        A list too short to scroll reports ``(0.0, 1.0)``, which counts as
        near-bottom so follow works from the very first timestamp. Probe
        failures also count as near-bottom: follow is the useful default.
        """
        canvas = self._timestamp_list_canvas()
        if canvas is None:
            return True
        try:
            return canvas.yview()[1] >= 0.98
        except tk.TclError:
            return True

    def _scroll_timestamp_list_to_bottom(self) -> None:
        """Scroll the timestamp list to its newest entry once layout settles."""
        if self._closing:
            return

        def _scroll() -> None:
            canvas = self._timestamp_list_canvas()
            if canvas is None:
                return
            try:
                canvas.update_idletasks()  # let fresh rows update the scrollregion
                canvas.yview_moveto(1.0)
            except tk.TclError:
                pass

        try:
            self.root.after_idle(_scroll)
        except tk.TclError:
            pass

    def _refresh_timestamp_list(self) -> None:
        """Repaint the timestamp list, reusing widgets wherever possible.

        Rows are cached per entry id and section headers/stop-footers per
        segment, so a refresh only reconfigures existing widgets in place;
        rows are created or destroyed solely when entries appear or disappear.
        The previous destroy-everything-and-recreate approach repainted the
        whole scroll area on every interaction, which visibly flickered on
        Windows.
        """
        # Smart-follow scrolling: remember whether the view hugged the bottom
        # before any regridding, then follow only if brand-new entries appear.
        # State-only refreshes (status dots, tag edits) never move the view.
        force_scroll = self._scroll_to_bottom_on_next_refresh
        self._scroll_to_bottom_on_next_refresh = False
        follow = force_scroll or self._timestamp_list_near_bottom()
        added_row = False

        # Entry ids restart at 1 per project, so a different session object
        # invalidates every cached row: start from a clean slate.
        if self._list_rows_session is not self.session:
            self._drop_all_rows_and_headers()
            if self._empty_label is not None:
                self._empty_label.destroy()
                self._empty_label = None
            self._list_rows_session = self.session

        if not self.session or not self.session.entries:
            self._drop_all_rows_and_headers()
            if self._empty_label is None:
                self._empty_label = ctk.CTkLabel(
                    self.timestamp_list,
                    text="No timestamps yet",
                    font=Theme.FONT_BODY,
                    text_color=Theme.TEXT_DIM,
                )
            self._empty_label.grid(row=0, column=0, padx=12, pady=30)
            return

        if self._empty_label is not None:
            self._empty_label.destroy()
            self._empty_label = None

        # Desired layout in display order: (kind, key, ...) items.
        session = self.session
        items: list[tuple] = []
        earlier = [
            entry for entry in session.entries if entry.recording_number is None
        ]
        if earlier:
            items.append(
                ("header", "earlier", "Earlier timestamps", False, len(earlier))
            )
            items.extend(("entry", entry.id, entry) for entry in earlier)

        for recording in sorted(session.recordings, key=lambda item: item.number):
            grouped = [
                entry
                for entry in session.entries
                if entry.recording_number == recording.number
            ]
            grouped.sort(key=lambda entry: entry.recording_index or 0)
            is_live = (
                session.timer_running
                and session.current_recording_number == recording.number
            )
            # Skip empty past segments, but always show the live one so the
            # user sees the new segment as soon as OBS starts recording.
            if not grouped and not is_live:
                continue
            items.append(
                (
                    "header",
                    ("rec", recording.number),
                    recording.header(),
                    is_live,
                    len(grouped),
                )
            )
            items.extend(("entry", entry.id, entry) for entry in grouped)
            if recording.ended_at is not None:
                items.append(
                    ("footer", ("rec", recording.number), recording)
                )

        seen_rows: set[int] = set()
        seen_headers: set[object] = set()
        for grid_row, item in enumerate(items):
            if item[0] == "header":
                _, key, title, live, count = item
                label = self._header_labels.get(key)
                if label is None:
                    label = ctk.CTkLabel(
                        self.timestamp_list, font=Theme.FONT_SMALL, anchor="w"
                    )
                    self._header_labels[key] = label
                prefix = "● " if live else ""
                suffix = f"   ·   {count} entries" if count else ""
                label.configure(
                    text=f"{prefix}{title}{suffix}",
                    text_color=(Theme.GREEN if live else Theme.TEXT_DIM),
                )
                label.grid(row=grid_row, column=0, padx=12, pady=(8, 1), sticky="w")
                seen_headers.add(key)
            elif item[0] == "footer":
                _, key, footer_recording = item
                label = self._footer_labels.get(key)
                if label is None:
                    label = ctk.CTkLabel(
                        self.timestamp_list,
                        font=Theme.FONT_SMALL,
                        anchor="w",
                    )
                    self._footer_labels[key] = label
                    # A stop marker is new list content: let smart-follow
                    # scroll it into view like a freshly added row.
                    added_row = True
                duration = footer_recording.duration_seconds() or 0.0
                label.configure(
                    text=(
                        "■ Recording stopped — "
                        f"{format_elapsed_display(duration)}"
                    ),
                    text_color=Theme.TEXT_DIM,
                )
                label.grid(row=grid_row, column=0, padx=26, pady=(0, 6), sticky="w")
                seen_headers.add(key)
            else:
                _, entry_id, entry = item
                widgets = self._list_rows.get(entry_id)
                if widgets is None:
                    widgets = self._create_row_widgets(entry)
                    self._list_rows[entry_id] = widgets
                    added_row = True
                self._update_row_widgets(widgets, entry)
                widgets["frame"].grid(
                    row=grid_row, column=0, padx=2, pady=1, sticky="ew"
                )
                seen_rows.add(entry_id)

        # Drop cached widgets for entries/segments that no longer exist.
        for entry_id in [eid for eid in self._list_rows if eid not in seen_rows]:
            self._list_rows.pop(entry_id)["frame"].destroy()
        for key in [k for k in self._header_labels if k not in seen_headers]:
            self._header_labels.pop(key).destroy()
        for key in [k for k in self._footer_labels if k not in seen_headers]:
            self._footer_labels.pop(key).destroy()

        if added_row and follow:
            self._scroll_timestamp_list_to_bottom()

    def _drop_all_rows_and_headers(self) -> None:
        """Destroy every cached row/header/footer widget (not the empty label)."""
        for widgets in self._list_rows.values():
            widgets["frame"].destroy()
        self._list_rows.clear()
        for label in self._header_labels.values():
            label.destroy()
        self._header_labels.clear()
        for label in self._footer_labels.values():
            label.destroy()
        self._footer_labels.clear()

    def _create_row_widgets(self, entry: TimestampEntry) -> dict:
        """Build the widget tree for one compact timestamp row (once per entry).

        One thin line: status dot · ref+time+state text · label/tag chips ·
        mini action icons (📷 on timestamps, 🎬 on replays, ✎, ✕). The whole row — frame and
        every passive child including chips — forwards clicks to the same
        record/stop/play state machine the old full-width button used; only
        the icon buttons are separate click targets. Everything state-
        dependent is applied by ``_update_row_widgets``.
        """
        assert self.session is not None
        entry_id = entry.id
        row_frame = ctk.CTkFrame(
            self.timestamp_list, fg_color="transparent", corner_radius=6
        )
        row_frame.grid_columnconfigure(2, weight=1)

        dot = ctk.CTkLabel(
            row_frame, text="●", width=16, font=Theme.FONT_SMALL, anchor="e"
        )
        dot.grid(row=0, column=0, padx=(8, 3), pady=4, sticky="ns")

        main_label = ctk.CTkLabel(row_frame, text="", font=Theme.FONT_ROW, anchor="w")
        main_label.grid(row=0, column=1, padx=(0, 8), pady=4, sticky="w")

        meta_frame = ctk.CTkFrame(row_frame, fg_color="transparent", height=0)
        meta_frame.grid(row=0, column=2, padx=0, pady=4, sticky="ew")

        actions = ctk.CTkFrame(row_frame, fg_color="transparent")
        actions.grid(row=0, column=3, padx=(2, 6), pady=3, sticky="e")
        next_action_column = 0
        shot_button = None
        if entry.kind == "replay" and entry.replay_file:
            ctk.CTkButton(
                actions,
                text="🎬",
                width=26,
                height=22,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BTN_SURFACE,
                hover_color=Theme.VIOLET_HOVER,
                command=lambda entry_id=entry_id: self._open_replay_video(entry_id),
            ).grid(row=0, column=next_action_column, padx=(0, 2))
            next_action_column += 1
        else:
            # Timestamps get a 📷 opener for their context screenshot. The
            # JPEG attaches asynchronously, so the button starts disabled and
            # is enabled by _update_row_widgets once screenshot_file lands.
            shot_button = ctk.CTkButton(
                actions,
                text="📷",
                width=26,
                height=22,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BTN_SURFACE,
                hover_color=Theme.BLUE_HOVER,
                command=lambda entry_id=entry_id: self._open_screenshot(entry_id),
                state="normal" if entry.screenshot_file else "disabled",
            )
            shot_button.grid(row=0, column=next_action_column, padx=(0, 2))
            next_action_column += 1
        ctk.CTkButton(
            actions,
            text="✎",
            width=26,
            height=22,
            font=Theme.FONT_SMALL,
            fg_color=Theme.BTN_SURFACE,
            hover_color=Theme.BTN_SURFACE_HOVER,
            command=lambda entry_id=entry_id: self._open_edit_dialog(entry_id),
        ).grid(row=0, column=next_action_column, padx=(0, 2))
        ctk.CTkButton(
            actions,
            text="✕",
            width=26,
            height=22,
            font=Theme.FONT_SMALL,
            fg_color=Theme.BTN_SURFACE,
            hover_color=Theme.RED_HOVER,
            command=lambda entry_id=entry_id: self._delete_timestamp(entry_id),
        ).grid(row=0, column=next_action_column + 1)

        # Whole-row click target: forward clicks on passive children too.
        row_click = lambda _event=None, eid=entry_id: self._click_timestamp(eid)
        for widget in (row_frame, dot, main_label, meta_frame):
            widget.bind("<Button-1>", row_click)
            widget.configure(cursor="hand2")

        return {
            "frame": row_frame,
            "dot": dot,
            "label": main_label,
            "meta_frame": meta_frame,
            "meta_signature": None,
            "shot_button": shot_button,
        }

    def _update_row_widgets(self, widgets: dict, entry: TimestampEntry) -> None:
        """Reconfigure a cached row in place to match the entry's current state."""
        assert self.session is not None
        is_playing = bool(
            entry.audio_file
            and self.playback.current_path
            and os.path.abspath(os.path.join(self.session.output_dir, entry.audio_file))
            == os.path.abspath(self.playback.current_path)
        )

        dot_color, text_color = self._entry_state_colors(entry, is_playing)
        widgets["dot"].configure(text_color=dot_color)
        widgets["label"].configure(
            text=self._entry_label(entry, is_playing), text_color=text_color
        )
        shot_button = widgets["shot_button"]
        if shot_button is not None:
            # Greyed until the async capture attaches the JPEG; also covers
            # failed captures and pre-screenshot-era entries.
            shot_button.configure(
                state="normal" if entry.screenshot_file else "disabled"
            )

        signature = (entry.label, tuple(entry.tags))
        if widgets["meta_signature"] != signature:
            self._rebuild_meta_chips(widgets["meta_frame"], entry)
            widgets["meta_signature"] = signature

    def _rebuild_meta_chips(self, meta_frame: ctk.CTkFrame, entry: TimestampEntry) -> None:
        """Recreate the inline label/tag chips inside an existing frame.

        Shows up to ``ROW_TAG_CHIPS`` tag chips plus a "+N” overflow chip so
        a heavily tagged entry can never push the action icons off-row.
        Chips forward clicks to the row's record/stop/play handler.
        """
        for child in meta_frame.winfo_children():
            child.destroy()
        row_click = lambda _event=None, eid=entry.id: self._click_timestamp(eid)

        def add_chip(widget: ctk.CTkLabel) -> None:
            widget.bind("<Button-1>", row_click)
            widget.configure(cursor="hand2")

        next_column = 0
        if entry.label:
            chip = ctk.CTkLabel(
                meta_frame,
                text=elide_middle(f'“{entry.label}”', ROW_LABEL_MAX_CHARS),
                font=Theme.FONT_SMALL,
                text_color=Theme.TEXT_BRIGHT,
                anchor="w",
            )
            chip.grid(row=0, column=next_column, padx=(0, 6), sticky="w")
            add_chip(chip)
            next_column += 1
        visible_tags = entry.tags[:ROW_TAG_CHIPS]
        for tag_index, tag_name in enumerate(visible_tags):
            chip = ctk.CTkLabel(
                meta_frame,
                text=f" {tag_name} ",
                font=Theme.FONT_SMALL,
                fg_color=self._tag_color(tag_name),
                text_color=Theme.TEXT_BRIGHT,
                corner_radius=5,
                height=16,
            )
            chip.grid(row=0, column=next_column + tag_index, padx=(0, 4), sticky="w")
            add_chip(chip)
        hidden_tags = len(entry.tags) - len(visible_tags)
        if hidden_tags > 0:
            chip = ctk.CTkLabel(
                meta_frame,
                text=f"+{hidden_tags}",
                font=Theme.FONT_SMALL,
                text_color=Theme.TEXT_DIM,
            )
            chip.grid(
                row=0, column=next_column + len(visible_tags), padx=(0, 4), sticky="w"
            )
            add_chip(chip)

    @staticmethod
    def _entry_label(entry: TimestampEntry, is_playing: bool) -> str:
        """Compact single-line text: ref · time · short state hint."""
        base = f"{format_entry_ref(entry)}   {format_elapsed_display(entry.elapsed_seconds)}"
        if entry.kind == "replay":
            return f"{base}   REPLAY {elide_middle(replay_display_name(entry.replay_file) or '', 32)}"
        if entry.status == "pending":
            return f"{base}   · click to record"
        if entry.status == "recording":
            return f"{base}   ● recording — click to stop"
        if entry.status == "error":
            reason = elide_middle(str(entry.error or "unknown error"), 40)
            return f"{base}   · error, click to retry: {reason}"
        duration = (
            f" ({entry.duration_seconds:.1f}s)" if entry.duration_seconds else ""
        )
        suffix = "   ■ stop" if is_playing else ""
        return f"{base}{duration}{suffix}"

    @staticmethod
    def _entry_state_colors(entry: TimestampEntry, is_playing: bool) -> tuple[str, str]:
        """(dot color, text color) for one row's current state."""
        if entry.kind == "replay":
            # Pending and error replays keep the violet identity color so
            # they read as footage markers rather than plain timestamps.
            return Theme.VIOLET, Theme.TEXT_BRIGHT
        if entry.status == "recording":
            return Theme.RED, Theme.RED
        if entry.status == "error":
            return Theme.RED, Theme.RED
        if entry.status == "completed":
            if is_playing:
                return Theme.BLUE, Theme.BLUE
            return Theme.GREEN, Theme.TEXT_BRIGHT
        return Theme.GREY, Theme.TEXT_DIM  # pending

    # ── OBS and hotkey integration ────────────────────────────────────────────

    def _setup_obs(self) -> None:
        self.obs_manager.register_callbacks(
            on_status_change=self._on_obs_status_change,
            on_recording_started=self._on_obs_recording_started,
            on_recording_stopped=self._on_obs_recording_stopped,
            on_replay_saved=self._on_obs_replay_saved,
        )
        if self.obs_settings.get("auto_connect", True):
            # The watchdog connects immediately when OBS is reachable and
            # keeps retrying, so OBS started later (or restarted) reconnects
            # on its own without the Connect button.
            self.obs_manager.enable_auto_reconnect(
                host=self.obs_settings.get("host", "localhost"),
                port=self.obs_settings.get("port", 4455),
                password=self.obs_settings.get("password", ""),
            )

    def _connect_obs(self) -> None:
        self.obs_manager.connect(
            self.obs_settings.get("host", "localhost"),
            self.obs_settings.get("port", 4455),
            self.obs_settings.get("password", ""),
        )
        self._on_obs_status_change("connecting")

    def _toggle_obs_connection(self) -> None:
        if self.obs_manager.is_connected:
            self.obs_manager.disconnect()
        else:
            self._connect_obs()

    def _on_obs_recording_started(self, output_path=None) -> None:
        name = recording_display_name(output_path)

        def update() -> None:
            if not self._closing:
                self._start_obs_timer(name)

        try:
            self.root.after(0, update)
        except tk.TclError:
            pass

    def _on_obs_recording_stopped(self, output_path=None) -> None:
        name = recording_display_name(output_path)

        def update() -> None:
            if not self._closing:
                self._stop_obs_timer(name)

        try:
            self.root.after(0, update)
        except tk.TclError:
            pass

    def _start_obs_timer(self, recording_name: str | None = None) -> None:
        if not self.session:
            self._update_action_state()
            self._set_status("OBS is recording. Set a project before adding timestamps.", Theme.AMBER)
            return
        self.session.start_timer(recording_name)
        self._update_action_state()
        self._schedule_list_refresh()
        segment = f" ({recording_name})" if recording_name else ""
        self._set_status(
            f"OBS recording started for '{self.session.project_name}'{segment}.",
            Theme.GREEN,
        )

    def _stop_obs_timer(self, recording_name: str | None = None) -> None:
        # A session that joined mid-recording never learned the file name;
        # the stop event reveals it, so backfill the segment header now.
        if (
            self.session
            and recording_name
            and self.session.current_recording_number is not None
        ):
            self.session.name_recording(
                self.session.current_recording_number, recording_name
            )
        if self.recorder.active and self.recording_entry_id is not None:
            entry = self.session.get(self.recording_entry_id) if self.session else None
            if entry:
                self._stop_entry_recording(entry)
        if self.session:
            self.session.stop_timer()
        self._update_action_state()
        self._schedule_list_refresh()
        self._set_status("OBS recording stopped. Project session locked.", Theme.BLUE)

    def _update_action_state(self) -> None:
        has_session = bool(self.session)
        timer_running = bool(self.session and self.session.timer_running)
        self.new_timestamp_button.configure(
            state="normal" if timer_running else "disabled"
        )
        self.manual_timestamp_button.configure(
            state="normal" if has_session else "disabled"
        )
        self.timer_toggle_button.configure(
            state="normal" if has_session else "disabled",
            text=("■ Stop timer" if timer_running else "▶ Start timer"),
            fg_color=(Theme.RED if timer_running else Theme.GREEN),
            hover_color=(Theme.RED_HOVER if timer_running else Theme.GREEN_HOVER),
        )

    def _on_obs_replay_saved(self, replay_path=None) -> None:
        """Log an OBS replay-buffer save as an entry; marshaled via root.after.

        Unlike new timestamps, creating replay entries is not gated on a
        running timer: OBS events arrive asynchronously and the model itself
        decides the segment (live one, most recent, or ungrouped).
        """

        def update() -> None:
            if self._closing:
                return
            if not replay_path:
                self._set_status(
                    "OBS saved a replay, but its file path was unavailable "
                    "— nothing logged.",
                    Theme.AMBER,
                )
                return
            if not self.session:
                self._set_status(
                    "OBS replay saved, but no project is selected — nothing logged.",
                    Theme.AMBER,
                )
                return
            try:
                entry = self.session.create_replay_entry(replay_path)
            except (ValueError, OSError) as exc:
                self._set_status(f"OBS replay save skipped: {exc}", Theme.AMBER)
                return
            self._schedule_list_refresh()
            name = replay_display_name(entry.replay_file)
            self._set_status(
                f"Replay '{name}' logged as {format_entry_ref(entry)}. "
                "Click it to record a note.",
                Theme.VIOLET,
            )

        try:
            self.root.after(0, update)
        except tk.TclError:
            pass

    def _on_obs_status_change(self, status: str) -> None:
        if self._closing:
            return

        def update() -> None:
            if self._closing:
                return
            if status == "connected":
                self.obs_status_label.configure(text="● OBS connected", text_color=Theme.GREEN)
                self.obs_connect_button.configure(text="Disconnect")
                # Surface the replay-buffer state immediately so silent
                # no-logging has an visible explanation from the start.
                replay_state = self.obs_manager.replay_buffer_active
                if replay_state is False:
                    self._set_status(
                        "Connected. Note: the OBS replay buffer is OFF — enable it in "
                        "OBS (Settings → Output → Replay Buffer) to log replays.",
                        Theme.AMBER,
                    )
                elif replay_state is None:
                    self._set_status(
                        "Connected. Could not read the OBS replay buffer state.",
                        Theme.AMBER,
                    )
            elif status == "connecting":
                self.obs_status_label.configure(text="● OBS connecting", text_color=Theme.AMBER)
                self.obs_connect_button.configure(text="Connecting...")
            elif status == "waiting":
                # The auto-reconnect watchdog is hunting for OBS. If the
                # connection died mid-use, finalize any stale recording
                # exactly like a disconnect would; this status repeats on
                # every retry cycle, so stay quiet when nothing is active.
                if (self.session and self.session.timer_running) or self.recorder.active:
                    self._stop_obs_timer()
                self.obs_status_label.configure(
                    text="● Waiting for OBS…", text_color=Theme.AMBER
                )
                self.obs_connect_button.configure(text="Connect OBS")
            elif status == "disconnected":
                self._stop_obs_timer()
                self.obs_status_label.configure(text="● OBS disconnected", text_color=Theme.RED)
                self.obs_connect_button.configure(text="Connect OBS")
            elif status.startswith("error:"):
                self.obs_status_label.configure(text="● OBS error", text_color=Theme.RED)
                self.obs_connect_button.configure(text="Connect OBS")
                self._set_status(status[6:], Theme.RED)

        try:
            self.root.after(0, update)
        except tk.TclError:
            pass

    @staticmethod
    def _key_string(key) -> str:
        if hasattr(key, "name"):
            return key.name
        if hasattr(key, "char") and key.char:
            return key.char
        return "unknown"

    @classmethod
    def _hotkey_display(cls, key_string: str) -> str:
        return MOUSE_HOTKEYS.get(key_string, key_string.upper())

    def _start_keyboard_listener(self) -> None:
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press, on_release=self._on_key_release
        )
        self._keyboard_listener.daemon = True
        self._keyboard_listener.start()

    def _start_mouse_listener(self) -> None:
        self._mouse_listener = mouse.Listener(on_click=self._on_mouse_click)
        self._mouse_listener.daemon = True
        self._mouse_listener.start()

    def _on_mouse_click(self, x, y, button, pressed):
        if self._capturing_key:
            return
        key_string = self._key_string(button)
        if not pressed:
            self._pressed_keys.pop(key_string, None)
            return
        if key_string not in MOUSE_HOTKEYS:
            return
        now = time.monotonic()
        last = self._pressed_keys.get(key_string)
        if last is not None and now - last < KEY_REPEAT_GUARD_SECONDS:
            return
        self._pressed_keys[key_string] = now
        if key_string == self.timestamp_key:
            try:
                self.root.after(0, self.create_timestamp)
            except tk.TclError:
                return False

    def _on_key_press(self, key):
        if self._capturing_key:
            return
        key_string = self._key_string(key)
        now = time.monotonic()
        last = self._pressed_keys.get(key_string)
        if last is not None and now - last < KEY_REPEAT_GUARD_SECONDS:
            return
        self._pressed_keys[key_string] = now
        if key_string == self.timestamp_key:
            try:
                self.root.after(0, self.create_timestamp)
            except tk.TclError:
                return False

    def _on_key_release(self, key):
        self._pressed_keys.pop(self._key_string(key), None)

    # ── Overlay dispatch ────────────────────────────────────────────────────

    def _setup_overlay_dispatch(self) -> None:
        self.root.bind("<Escape>", self._handle_overlay_escape, add="+")
        self.root.bind("<Return>", self._handle_overlay_return, add="+")

    def _handle_overlay_escape(self, event) -> str | None:
        stack = getattr(self, "_overlay_stack", None)
        if not stack:
            return None
        # Prune stale entries whose widgets were destroyed directly.
        alive = [o for o in stack if o.winfo_exists()]
        if len(alive) != len(stack):
            self._overlay_stack[:] = alive
            stack = alive
        if not stack:
            return None
        top = stack[-1]
        try:
            top.on_escape()
        except Exception:
            pass
        return "break"

    def _handle_overlay_return(self, event) -> str | None:
        stack = getattr(self, "_overlay_stack", None)
        if not stack:
            return None
        alive = [o for o in stack if o.winfo_exists()]
        if len(alive) != len(stack):
            self._overlay_stack[:] = alive
            stack = alive
        if not stack:
            return None
        top = stack[-1]
        try:
            top.on_return()
        except Exception:
            pass
        # Only swallow the event when the top overlay actually handles Return;
        # tag-manager's Return is entry-local (widget binding fires first) and
        # its on_return is a no-op — let the widget-level handler stand.
        if top.__class__.__name__ in ("TimestampEditDialog", "ManualTimestampDialog"):
            return "break"
        return None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _update_clock(self) -> None:
        if self._closing:
            return
        elapsed = self.session.elapsed_seconds() if self.session else 0.0
        self.clock_label.configure(text=format_elapsed_display(elapsed))
        current = self.session.current_recording if self.session else None
        if self.session and self.session.timer_running and current is not None:
            self.rec_indicator_label.configure(
                text=f"● REC {current.number}", text_color=Theme.GREEN
            )
        else:
            self.rec_indicator_label.configure(text="")
        self.root.after(500, self._update_clock)

    def on_closing(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self.recorder.active and self.recording_entry_id is not None and self.session:
            entry = self.session.get(self.recording_entry_id)
            if entry:
                try:
                    output_path, duration = self.recorder.stop()
                    self.session.mark_completed(entry, output_path, duration)
                except AudioError as exc:
                    self.session.mark_error(entry, str(exc))
        self.playback.stop()
        if self.session:
            self.session.save()
        self._save_config()
        if hasattr(self, "_keyboard_listener") and self._keyboard_listener.running:
            self._keyboard_listener.stop()
        if self._mouse_listener and self._mouse_listener.running:
            self._mouse_listener.stop()
        if self._key_capture_listener and self._key_capture_listener.running:
            self._key_capture_listener.stop()
        if self._mouse_capture_listener and self._mouse_capture_listener.running:
            self._mouse_capture_listener.stop()
        self.obs_manager.shutdown()
        self.obs_manager.disconnect()
        self.root.destroy()


class OverlayDialog(ctk.CTkFrame):
    """In-window modal overlay: dimmed backdrop + centered card.

    Subclasses build their UI inside ``self.card``. The backdrop covers the
    entire app window via ``place``, stays centered on resize, and provides
    grab-based modality. Lifecycle (stack + Configure binding + grab) is
    managed here so subclasses stay focused on content.
    """

    def __init__(self, app, width: int, height: int) -> None:
        super().__init__(app.root, fg_color=Theme.BG_DARKEST)
        self.app = app
        self._desired_width = width
        self._desired_height = height
        self._closed = False
        self._configure_bind_id: str | None = None
        # Cover the whole window and sit above everything else.
        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.lift()
        # Centered card — width/height are clamped to the current window size.
        self.card = ctk.CTkFrame(
            self,
            fg_color=Theme.BG_SURFACE,
            corner_radius=12,
            border_width=1,
            border_color=Theme.DIVIDER,
        )
        try:
            self.card.pack_propagate(False)
        except Exception:
            pass
        try:
            self.card.grid_propagate(False)
        except Exception:
            pass
        self._place_card()
        self.app._overlay_stack.append(self)
        self._configure_bind_id = self.app.root.bind(
            "<Configure>", self._on_root_configure, add="+"
        )
        # Backdrop swallows clicks; card clicks must not propagate to backdrop.
        self.bind("<Button-1>", lambda _e: "break")

    # ── Hooks for subclasses ────────────────────────────────────────────

    def on_escape(self) -> None:
        pass

    def on_return(self) -> None:
        pass

    # ── Geometry helpers ────────────────────────────────────────────────

    def _place_card(self) -> None:
        try:
            rw = self.app.root.winfo_width()
            rh = self.app.root.winfo_height()
        except tk.TclError:
            rw = rh = 0
        if rw < 50 or rh < 50:
            w, h = self._desired_width, self._desired_height
        else:
            w = min(self._desired_width, max(200, rw - 24))
            h = min(self._desired_height, max(200, rh - 24))
        try:
            self.card.configure(width=w, height=h)
            self.card.place(relx=0.5, rely=0.5, anchor="center")
        except tk.TclError:
            pass

    def _on_root_configure(self, event) -> None:
        if event.widget is not self.app.root:
            return
        self._place_card()

    # ── Focus & modality ────────────────────────────────────────────────

    def _focus(self) -> None:  # type: ignore[override]
        try:
            self.lift()
            self.card.lift()
            self.grab_set()
        except tk.TclError:
            pass

    def destroy(self) -> None:  # type: ignore[override]
        if getattr(self, "_closed", False):
            try:
                super().destroy()
            except tk.TclError:
                pass
            return
        self._closed = True
        try:
            if self in self.app._overlay_stack:
                self.app._overlay_stack.remove(self)
        except Exception:
            pass
        try:
            if self._configure_bind_id:
                self.app.root.unbind("<Configure>", self._configure_bind_id)
        except Exception:
            pass
        try:
            # Only release if we currently own the grab.
            if self.app.root.grab_current() is self:
                self.grab_release()
            else:
                try:
                    self.grab_release()
                except tk.TclError:
                    pass
        except tk.TclError:
            pass
        try:
            super().destroy()
        except tk.TclError:
            pass


class TagManagerDialog(OverlayDialog):
    """Modal dialog to manage the tag library.

    Add, rename, recolor, and delete tags. Every accepted change is reported
    through a callback immediately — the app persists keybinds.json and
    propagates renames/deletes to the open session — while the dialog works
    on a private copy of the definitions for rendering and validation.
    """

    PALETTE = [
        "#FF5252", "#FF7043", "#FFAB00", "#FFE082",
        "#9CCC65", "#00E676", "#26C6DA", "#448AFF",
        "#AB47BC", "#FF80AB", "#B0BEC5", "#FFFFFF",
    ]

    def __init__(
        self,
        app,
        definitions: list[dict],
        usage_lookup,
        on_add,
        on_rename,
        on_delete,
        on_recolor,
        on_close=None,
        focus_name: bool = False,
    ):
        super().__init__(app, 430, 540)

        self._definitions = [dict(tag) for tag in definitions]
        self._usage_lookup = usage_lookup
        self._on_add = on_add
        self._on_rename = on_rename
        self._on_delete = on_delete
        self._on_recolor = on_recolor
        self._on_close = on_close
        self._editing_original: str | None = None
        self._selected_color: str = self.PALETTE[0]
        self._swatches: list[tuple[str, ctk.CTkButton]] = []

        ctk.CTkLabel(
            self.card,
            text="Tag library",
            font=Theme.FONT_SUBTITLE,
            text_color=Theme.TEXT_BRIGHT,
            anchor="w",
        ).pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            self.card,
            text=(
                "Tags appear as colored chips on timestamps and as #tags in "
                "the Markdown log."
            ),
            font=Theme.FONT_SMALL,
            text_color=Theme.TEXT_DIM,
            anchor="w",
            wraplength=380,
            justify="left",
        ).pack(fill="x", padx=18, pady=(0, 8))

        self._list_frame = ctk.CTkScrollableFrame(
            self.card, fg_color=Theme.BG_ENTRY, height=170
        )
        self._list_frame.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self._rebuild_list()

        form = ctk.CTkFrame(self.card, fg_color="transparent")
        form.pack(fill="x", padx=18)
        ctk.CTkLabel(
            form, text="Name", font=Theme.FONT_SMALL, text_color=Theme.TEXT_DIM
        ).pack(anchor="w")
        name_row = ctk.CTkFrame(form, fg_color="transparent")
        name_row.pack(fill="x")
        self.name_entry = ctk.CTkEntry(
            name_row,
            placeholder_text=f"e.g. boss (max {TAG_NAME_MAX_LENGTH} characters)",
            font=Theme.FONT_BODY,
        )
        self.name_entry.pack(side="left", fill="x", expand=True)
        self.name_entry.bind("<Return>", lambda _event: self._submit())
        self.primary_button = ctk.CTkButton(
            name_row,
            text="Add tag",
            width=96,
            font=Theme.FONT_BUTTON,
            fg_color=Theme.GREEN,
            hover_color=Theme.GREEN_HOVER,
            text_color="#000000",
            command=self._submit,
        )
        self.primary_button.pack(side="left", padx=(8, 0))

        ctk.CTkLabel(
            form, text="Color", font=Theme.FONT_SMALL, text_color=Theme.TEXT_DIM
        ).pack(anchor="w", pady=(8, 0))
        palette = ctk.CTkFrame(form, fg_color="transparent")
        palette.pack(fill="x")
        for index, hex_color in enumerate(self.PALETTE):
            swatch = ctk.CTkButton(
                palette,
                text="",
                width=26,
                height=26,
                corner_radius=6,
                border_width=2,
                border_color="#000000",
                fg_color=hex_color,
                hover_color=hex_color,
                command=lambda c=hex_color: self._select_color(c),
            )
            swatch.grid(row=index // 6, column=index % 6, padx=3, pady=3)
            self._swatches.append((hex_color, swatch))
        ctk.CTkButton(
            palette,
            text="Custom…",
            width=70,
            height=26,
            font=Theme.FONT_SMALL,
            fg_color=Theme.BTN_SURFACE,
            hover_color=Theme.BTN_SURFACE_HOVER,
            command=self._pick_custom_color,
        ).grid(row=1, column=6, padx=(10, 0), pady=3, sticky="w")
        self._update_swatch_states()

        self.feedback_label = ctk.CTkLabel(
            self.card,
            text="",
            font=Theme.FONT_SMALL,
            text_color=Theme.TEXT_DIM,
            anchor="w",
            wraplength=380,
            justify="left",
        )
        self.feedback_label.pack(fill="x", padx=18, pady=(6, 0))

        footer = ctk.CTkFrame(self.card, fg_color="transparent")
        footer.pack(fill="x", padx=18, pady=(8, 16))
        footer.grid_columnconfigure(1, weight=1)
        self.cancel_edit_button = ctk.CTkButton(
            footer,
            text="Cancel edit",
            width=96,
            font=Theme.FONT_SMALL,
            fg_color=Theme.GREY,
            hover_color=Theme.GREY_HOVER,
            command=self._exit_edit_mode,
        )
        self.cancel_edit_button.grid(row=0, column=0, sticky="w")
        self.cancel_edit_button.grid_remove()
        ctk.CTkButton(
            footer,
            text="Close",
            width=100,
            font=Theme.FONT_BUTTON,
            fg_color=Theme.GREY,
            hover_color=Theme.GREY_HOVER,
            command=self._close,
        ).grid(row=0, column=2, sticky="e")

        self._focus_name_requested = focus_name
        self.after(60, self._focus)

    # ── List rendering ──────────────────────────────────────────────────────

    def _rebuild_list(self) -> None:
        for child in self._list_frame.winfo_children():
            child.destroy()
        if not self._definitions:
            ctk.CTkLabel(
                self._list_frame,
                text="No tags yet — add your first one below.",
                font=Theme.FONT_SMALL,
                text_color=Theme.TEXT_DIM,
            ).pack(pady=14)
            return
        for definition in self._definitions:
            name = str(definition.get("name", ""))
            color = str(definition.get("color", Theme.GREY))
            row = ctk.CTkFrame(self._list_frame, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=2)
            ctk.CTkButton(
                row,
                text="Delete",
                width=64,
                height=24,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BTN_SURFACE,
                hover_color=Theme.RED_HOVER,
                command=lambda n=name: self._delete(n),
            ).pack(side="right", padx=(6, 2))
            ctk.CTkButton(
                row,
                text="Edit",
                width=56,
                height=24,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BTN_SURFACE,
                hover_color=Theme.BTN_SURFACE_HOVER,
                command=lambda n=name: self._edit(n),
            ).pack(side="right", padx=2)
            ctk.CTkLabel(
                row,
                text=f"#{name}",
                font=Theme.FONT_BODY,
                anchor="w",
            ).pack(side="left", padx=(2, 8))
            ctk.CTkLabel(
                row,
                text="",
                width=22,
                height=22,
                corner_radius=5,
                fg_color=color,
            ).pack(side="right", padx=2)

    # ── Form state ──────────────────────────────────────────────────────────

    def _focus(self) -> None:
        try:
            self.lift()
            self.card.lift()
            self.grab_set()
            if self._focus_name_requested:
                self.name_entry.focus_set()
        except tk.TclError:
            pass

    def on_escape(self) -> None:
        self._close()

    def on_return(self) -> None:
        pass

    def _select_color(self, hex_color: str) -> None:
        self._selected_color = hex_color
        self._update_swatch_states()

    def _update_swatch_states(self) -> None:
        for hex_color, swatch in self._swatches:
            selected = hex_color.lower() == self._selected_color.lower()
            swatch.configure(border_color="#FFFFFF" if selected else "#000000")

    def _pick_custom_color(self) -> None:
        result = colorchooser.askcolor(
            color=self._selected_color, parent=self, title="Pick a tag color"
        )
        if not result or not result[1]:
            return  # user cancelled the chooser
        self._selected_color = str(result[1]).upper()
        self._update_swatch_states()

    def _feedback(self, message: str | None) -> None:
        if message:
            self.feedback_label.configure(text=message, text_color=Theme.RED)
        else:
            self.feedback_label.configure(text="", text_color=Theme.TEXT_DIM)

    def _submit(self) -> None:
        name = normalize_tag_name(self.name_entry.get())
        if not name:
            self._feedback("Enter a tag name first.")
            return
        if len(name) > TAG_NAME_MAX_LENGTH:
            self._feedback(
                f"Too long — keep tag names to {TAG_NAME_MAX_LENGTH} characters or fewer."
            )
            return
        editing = self._editing_original
        clash = any(
            definition["name"].lower() == name.lower()
            and definition["name"].lower() != (editing or "").lower()
            for definition in self._definitions
        )
        if clash:
            self._feedback(f"'{name}' already exists — pick another name.")
            return
        if editing is not None:
            current = next(
                definition
                for definition in self._definitions
                if definition["name"].lower() == editing.lower()
            )
            if name != current["name"]:
                self._on_rename(editing, name)
                current["name"] = name
            if self._selected_color.lower() != str(current["color"]).lower():
                self._on_recolor(name, self._selected_color)
                current["color"] = self._selected_color
            self._exit_edit_mode()
        else:
            self._on_add(name, self._selected_color)
            self._definitions.append({"name": name, "color": self._selected_color})
            self.name_entry.delete(0, "end")
        self._feedback(None)
        self._rebuild_list()

    def _edit(self, name: str) -> None:
        definition = next(
            item
            for item in self._definitions
            if item["name"].lower() == name.lower()
        )
        self._editing_original = definition["name"]
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, definition["name"])
        self._selected_color = str(definition["color"])
        self._update_swatch_states()
        self.primary_button.configure(text="Save changes")
        self.cancel_edit_button.grid()
        self.name_entry.focus_set()

    def _exit_edit_mode(self) -> None:
        self._editing_original = None
        self.name_entry.delete(0, "end")
        self.primary_button.configure(text="Add tag")
        self.cancel_edit_button.grid_remove()
        self._rebuild_list()

    def _delete(self, name: str) -> None:
        used = int(self._usage_lookup(name))
        if used > 0:
            confirmed = messagebox.askyesno(
                "Delete tag",
                f"'{name}' is used on {used} timestamp"
                f"{'s' if used != 1 else ''}.\n\n"
                "Delete the tag and remove it from those timestamps?",
                parent=self,
            )
            if not confirmed:
                return
        self._on_delete(name)
        self._definitions = [
            definition
            for definition in self._definitions
            if definition["name"].lower() != name.lower()
        ]
        if (
            self._editing_original is not None
            and self._editing_original.lower() == name.lower()
        ):
            self._exit_edit_mode()
        else:
            self._rebuild_list()

    def _close(self) -> None:
        callback = self._on_close
        self.destroy()
        if callback:
            try:
                callback()
            except tk.TclError:
                pass


class TimestampEditDialog(OverlayDialog):
    """Modal dialog to set a timestamp's label and preset tags."""

    def __init__(
        self,
        app,
        entry: TimestampEntry,
        tag_definitions: list[dict],
        on_save,
        on_manage_tags=None,
    ):
        super().__init__(app, 440, 360)

        self._on_save = on_save
        self._definitions = list(tag_definitions)
        self._selected_tags: set[str] = {tag.lower() for tag in entry.tags}

        ctk.CTkLabel(
            self.card,
            text=(
                f"Timestamp {format_entry_ref(entry)} — "
                f"{format_elapsed_display(entry.elapsed_seconds)}"
            ),
            font=Theme.FONT_SUBTITLE,
            text_color=Theme.TEXT_BRIGHT,
            anchor="w",
        ).pack(fill="x", padx=18, pady=(16, 10))

        ctk.CTkLabel(
            self.card,
            text="Label",
            font=Theme.FONT_SMALL,
            text_color=Theme.TEXT_DIM,
            anchor="w",
        ).pack(fill="x", padx=18)
        self.label_entry = ctk.CTkEntry(
            self.card, font=Theme.FONT_BODY, placeholder_text="Optional short note…"
        )
        if entry.label:
            self.label_entry.insert(0, entry.label)
        self.label_entry.pack(fill="x", padx=18, pady=(4, 12))
        self.label_entry.focus_set()

        tags_header = ctk.CTkFrame(self.card, fg_color="transparent")
        tags_header.pack(fill="x", padx=18)
        ctk.CTkLabel(
            tags_header,
            text="Tags",
            font=Theme.FONT_SMALL,
            text_color=Theme.TEXT_DIM,
            anchor="w",
        ).pack(side="left")
        if on_manage_tags is not None:
            ctk.CTkButton(
                tags_header,
                text="＋ New tag",
                width=92,
                height=22,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BTN_SURFACE,
                hover_color=Theme.BTN_SURFACE_HOVER,
                command=on_manage_tags,
            ).pack(side="right")
        self._chips_frame = ctk.CTkFrame(self.card, fg_color="transparent")
        self._chips_frame.pack(fill="x", padx=14, pady=(4, 12))
        self._build_chips()

        buttons = ctk.CTkFrame(self.card, fg_color="transparent")
        buttons.pack(fill="x", padx=18, pady=(2, 16))
        buttons.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            buttons,
            text="Save",
            width=100,
            font=Theme.FONT_BUTTON,
            fg_color=Theme.GREEN,
            hover_color=Theme.GREEN_HOVER,
            text_color="#000000",
            command=self._save,
        ).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(
            buttons,
            text="Cancel",
            width=100,
            font=Theme.FONT_BUTTON,
            fg_color=Theme.GREY,
            hover_color=Theme.GREY_HOVER,
            command=self._cancel,
        ).grid(row=0, column=0, sticky="e")

        self.after(60, self._focus)

    def _focus(self) -> None:
        try:
            self.lift()
            self.card.lift()
            self.grab_set()
            self.label_entry.focus_set()
        except tk.TclError:
            pass

    def on_escape(self) -> None:
        self._cancel()

    def on_return(self) -> None:
        self._save()

    def _build_chips(self) -> None:
        """(Re)create the tag chip grid from the current definitions."""
        for child in self._chips_frame.winfo_children():
            child.destroy()
        for index, definition in enumerate(self._definitions):
            self._chips_frame.grid_columnconfigure(index % 4, weight=1)
            name = str(definition.get("name", ""))
            color = str(definition.get("color", Theme.GREY))
            chip = ctk.CTkButton(
                self._chips_frame,
                text=f"#{name}",
                height=28,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BTN_SURFACE,
                hover_color=Theme.BTN_SURFACE_HOVER,
                text_color=Theme.TEXT_BRIGHT,
            )
            chip.configure(
                command=lambda n=name, c=color, b=chip: self._toggle_tag(n, c, b)
            )
            chip.grid(
                row=index // 4, column=index % 4, padx=4, pady=3, sticky="ew"
            )
            self._apply_chip_state(chip, name, color)

    def refresh_tags(self, tag_definitions: list[dict]) -> None:
        """Adopt updated tag definitions after a live tag-library edit."""
        self._definitions = [dict(tag) for tag in tag_definitions]
        self._build_chips()

    def _apply_chip_state(self, chip: ctk.CTkButton, name: str, color: str) -> None:
        selected = name.lower() in self._selected_tags
        if selected:
            chip.configure(fg_color=color, hover_color=color)
        else:
            chip.configure(fg_color=Theme.BTN_SURFACE, hover_color=Theme.BTN_SURFACE_HOVER)

    def _toggle_tag(self, name: str, color: str, chip: ctk.CTkButton) -> None:
        key = name.lower()
        if key in self._selected_tags:
            self._selected_tags.discard(key)
        else:
            self._selected_tags.add(key)
        self._apply_chip_state(chip, name, color)

    def _selected_tag_names(self) -> list[str]:
        chosen: list[str] = []
        for definition in self._definitions:
            name = str(definition.get("name", ""))
            if name and name.lower() in self._selected_tags:
                chosen.append(name)
        return chosen

    def _save(self) -> None:
        label = self.label_entry.get()
        on_save = self._on_save
        self.destroy()
        on_save(label, self._selected_tag_names())

    def _cancel(self) -> None:
        self.destroy()


class ManualTimestampDialog(OverlayDialog):
    """Modal dialog to create a timestamp from a typed time position."""

    def __init__(self, app, on_create):
        super().__init__(app, 360, 190)
        self._on_create = on_create

        ctk.CTkLabel(
            self.card,
            text="Time position",
            font=Theme.FONT_SUBTITLE,
            text_color=Theme.TEXT_BRIGHT,
            anchor="w",
        ).pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            self.card,
            text="Format: SS, MM:SS, or HH:MM:SS (for example 42:10)",
            font=Theme.FONT_SMALL,
            text_color=Theme.TEXT_DIM,
            anchor="w",
        ).pack(fill="x", padx=18)
        self.time_entry = ctk.CTkEntry(
            self.card, font=Theme.FONT_BODY, placeholder_text="00:00:00"
        )
        self.time_entry.pack(fill="x", padx=18, pady=(8, 12))
        self.time_entry.focus_set()

        buttons = ctk.CTkFrame(self.card, fg_color="transparent")
        buttons.pack(fill="x", padx=18, pady=(0, 16))
        buttons.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            buttons,
            text="Create",
            width=100,
            font=Theme.FONT_BUTTON,
            fg_color=Theme.CRIMSON,
            hover_color=Theme.CRIMSON_HOVER,
            command=self._create,
        ).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(
            buttons,
            text="Cancel",
            width=100,
            font=Theme.FONT_BUTTON,
            fg_color=Theme.GREY,
            hover_color=Theme.GREY_HOVER,
            command=self.destroy,
        ).grid(row=0, column=0, sticky="e")

        self.after(60, self._focus)

    def _focus(self) -> None:
        try:
            self.lift()
            self.card.lift()
            self.grab_set()
            self.time_entry.focus_set()
        except tk.TclError:
            pass

    def on_escape(self) -> None:
        self.destroy()

    def on_return(self) -> None:
        self._create()

    def _create(self) -> None:
        if self._on_create(self.time_entry.get()):
            self.destroy()


def main() -> None:
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    TimestampApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
