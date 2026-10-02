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
    MIC_SILENCE_THRESHOLD_DEFAULT,
    MIC_SILENCE_TIMEOUT_DEFAULT,
    PlaybackController,
    RecordingInfo,
    SESSION_FILENAME,
    TAG_NAME_MAX_LENGTH,
    TimestampEntry,
    TimestampSession,
    format_elapsed_display,
    normalize_tag_name,
    parse_time_input,
    read_project_stats,
    remove_recent_project,
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

# Recent-projects rows show "<name> · N timestamps · M recordings"; long
# names get middle-elided so the counts stay visible.
RECENT_ROW_NAME_MAX_CHARS = 36

# A hotkey press only re-fires after this many seconds. This swallows OS
# auto-repeat, and — more importantly — means a missed release event can never
# permanently disable a key: after this window a fresh press works again.
KEY_REPEAT_GUARD_SECONDS = 0.3


class TimestampApp:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title("Nilvarcus Clickable Timestamp Recorder")
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
        self._popup_configure_bind_id: str | None = None
        self._popup_unmap_bind_id: str | None = None

        self.session: TimestampSession | None = None
        self.obs_manager = OBSManager()
        mic_settings = (
            self.config.get("mic_settings")
            if isinstance(self.config.get("mic_settings"), dict)
            else {}
        )
        try:
            silence_threshold = float(
                mic_settings.get("silence_threshold", MIC_SILENCE_THRESHOLD_DEFAULT)
            )
            silence_timeout = float(
                mic_settings.get("silence_timeout", MIC_SILENCE_TIMEOUT_DEFAULT)
            )
        except (TypeError, ValueError):
            silence_threshold = MIC_SILENCE_THRESHOLD_DEFAULT
            silence_timeout = MIC_SILENCE_TIMEOUT_DEFAULT
        self.recorder = AudioRecorder(
            silence_threshold=silence_threshold,
            silence_timeout=silence_timeout,
        )
        self.playback = PlaybackController()
        self.device_choices: dict[str, int | None] = {}
        self.recording_entry_id: int | None = None
        self._playback_job = None
        # Live mic-meter poll job while a recording is active (None = idle).
        self._mic_meter_job: str | None = None
        # True only while an OBS connection is actually up. Watchdog
        # "waiting" passes repeat every few seconds while OBS is simply off;
        # they may finalize a running timer/recording ONLY on a real
        # connected→dropped transition, never on every retry.
        self._obs_was_up = False
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
        self._obs_settings_dialog = None
        self._overlay_stack: list = []
        self._obs_last_failure_reason: str | None = None
        self._filter_text: str = ""
        self._filter_var: tk.StringVar | None = None
        # "Missing audio" view toggle: shows only replay entries without a
        # saved audio take. Transient (not persisted); reset on project switch.
        self._missing_audio_only: bool = False
        # Timestamp-list repaint state: cached row/header/footer widgets so
        # refreshes reconfigure in place instead of destroying and recreating
        # everything (the old full-rebuild behavior flickered on every
        # interaction).
        self._list_rows: dict[int, dict] = {}
        self._header_widgets: dict[object, dict] = {}
        self._footer_labels: dict[object, ctk.CTkLabel] = {}
        self._empty_label: ctk.CTkLabel | None = None
        self._list_rows_session: TimestampSession | None = None
        self._list_refresh_job: str | None = None
        # Debounced Markdown-log write: one trailing job per burst of saves.
        self._markdown_flush_job: str | None = None
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
            "mic_settings": {
                "silence_threshold": self.recorder.silence_threshold,
                "silence_timeout": self.recorder.silence_timeout,
            },
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
        """Single-row header: REC · clock · OBS status · Tags · Connect."""
        header = ctk.CTkFrame(self.root, fg_color=Theme.BG_SURFACE, corner_radius=12)
        header.grid(row=0, column=0, padx=14, pady=(14, 6), sticky="ew")
        # Column 0 stays empty and takes the full stretch, acting as a left
        # spacer so every control below remains right-aligned.
        header.grid_columnconfigure(0, weight=1)

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

        self.obs_settings_button = ctk.CTkButton(
            header,
            text="⚙",
            width=28,
            height=26,
            font=Theme.FONT_SMALL,
            fg_color=Theme.BTN_SURFACE,
            hover_color=Theme.BTN_SURFACE_HOVER,
            command=self._open_obs_settings,
        )
        self.obs_settings_button.grid(row=0, column=5, padx=(0, 6), pady=6, sticky="e")

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
        self.obs_connect_button.grid(row=0, column=6, padx=(0, 14), pady=6, sticky="e")

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

        # Compact toolbar: filter on the left, three controls on the right.
        toolbar = ctk.CTkFrame(list_frame, fg_color="transparent")
        toolbar.grid(row=0, column=0, padx=10, pady=(8, 6), sticky="ew")
        toolbar.grid_columnconfigure(0, weight=1)
        filter_frame = ctk.CTkFrame(toolbar, fg_color="transparent")
        filter_frame.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(filter_frame, text="🔍", font=Theme.FONT_SMALL, text_color=Theme.TEXT_DIM).pack(side="left", padx=(0, 4))
        self._filter_var = tk.StringVar(value="")
        self.filter_entry = ctk.CTkEntry(filter_frame, textvariable=self._filter_var, placeholder_text="Filter by label, tag, time…", font=Theme.FONT_SMALL, height=26, width=200)
        self.filter_entry.pack(side="left")
        self.filter_entry.bind("<KeyRelease>", lambda _e: self._on_filter_change())
        self.filter_entry.bind("<Escape>", lambda _e: self._clear_filter())
        # "Missing audio" toggle: when checked, the list shows only replay
        # entries that have no saved audio take (pending or error).
        self.missing_audio_checkbox = ctk.CTkCheckBox(
            filter_frame,
            text="Missing audio",
            font=Theme.FONT_SMALL,
            width=110,
            height=26,
            checkbox_width=16,
            checkbox_height=16,
            command=self._on_missing_audio_toggle,
        )
        self.missing_audio_checkbox.pack(side="left", padx=(8, 0))
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
        self.timer_toggle_button.grid(row=0, column=1, padx=(8, 4), sticky="e")
        self.pause_button = ctk.CTkButton(
            toolbar,
            text="⏸ Pause",
            width=80,
            height=28,
            font=Theme.FONT_BUTTON,
            fg_color=Theme.GREY,
            hover_color=Theme.GREY_HOVER,
            command=self._toggle_pause,
            state="disabled",
        )
        self.pause_button.grid(row=0, column=2, padx=(0, 4), sticky="e")
        self.pause_button.grid_remove()
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
        self.new_timestamp_button.grid(row=0, column=3, padx=4, sticky="e")

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
        self.manual_timestamp_button.grid(row=0, column=4, padx=(4, 0), sticky="e")

        self.timestamp_list = ctk.CTkScrollableFrame(
            list_frame, fg_color=Theme.BG_ENTRY, corner_radius=8
        )
        self.timestamp_list.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self.timestamp_list.grid_columnconfigure(0, weight=1)

    def _create_footer(self) -> None:
        footer = ctk.CTkFrame(self.root, fg_color="transparent")
        footer.grid(row=3, column=0, padx=16, pady=(0, 14), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(
            footer,
            text="Create a timestamp, then click it to record a microphone note.",
            font=Theme.FONT_SMALL,
            text_color=Theme.TEXT_DIM,
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="ew")

        # Live microphone level meter, shown only while a recording runs so
        # the user can immediately see whether any input is coming through.
        self.mic_meter_frame = ctk.CTkFrame(footer, fg_color="transparent")
        self.mic_meter_frame.grid(row=0, column=1, padx=(12, 0), sticky="e")
        ctk.CTkLabel(
            self.mic_meter_frame,
            text="🎤",
            font=Theme.FONT_SMALL,
            text_color=Theme.TEXT_DIM,
        ).pack(side="left")
        self.mic_meter_bar = ctk.CTkProgressBar(
            self.mic_meter_frame,
            width=120,
            height=10,
            fg_color=Theme.BG_ENTRY,
            progress_color=Theme.GREEN,
        )
        self.mic_meter_bar.set(0)
        self.mic_meter_bar.pack(side="left", padx=(6, 0))
        self.mic_meter_frame.grid_remove()

    def _set_status(self, message: str, color=Theme.TEXT_DIM) -> None:
        self.status_label.configure(text=message, text_color=color)

    # ── Microphone meter and silence watchdog ────────────────────────────────

    MIC_METER_POLL_MS = 120

    def _start_mic_meter(self) -> None:
        """Show the input meter and start polling level + silence."""
        try:
            self.mic_meter_bar.set(0)
            self.mic_meter_frame.grid()
        except tk.TclError:
            return
        if self._mic_meter_job is None:
            self._poll_mic_meter()

    def _stop_mic_meter(self) -> None:
        """Hide the meter and cancel its poll loop."""
        if self._mic_meter_job is not None:
            try:
                self.root.after_cancel(self._mic_meter_job)
            except tk.TclError:
                pass
            self._mic_meter_job = None
        try:
            self.mic_meter_bar.set(0)
            self.mic_meter_frame.grid_remove()
        except tk.TclError:
            pass

    def _poll_mic_meter(self) -> None:
        """One meter tick: update the bar, enforce the silence timeout."""
        self._mic_meter_job = None
        if self._closing:
            return
        if not self.recorder.active:
            self._stop_mic_meter()
            return
        try:
            self.mic_meter_bar.set(min(1.0, max(0.0, self.recorder.level)))
        except tk.TclError:
            return
        if self.recorder.monitor.should_stop():
            self._abort_silent_recording()
            return
        self._mic_meter_job = self.root.after(
            self.MIC_METER_POLL_MS, self._poll_mic_meter
        )

    def _abort_silent_recording(self) -> None:
        """Discard a voiceless capture and make the entry clickable again."""
        entry = (
            self.session.get(self.recording_entry_id)
            if (self.session and self.recording_entry_id is not None)
            else None
        )
        try:
            self.recorder.discard()
        except AudioError:
            pass
        self.recording_entry_id = None
        self._stop_mic_meter()
        if entry is not None:
            self.session.reset_for_retry(entry)
            self._schedule_list_refresh()
        self._set_status(
            "No microphone input detected — check the selected mic and try again.",
            Theme.RED,
        )

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
            # Flush the derived Markdown log before its session is replaced.
            self.session.flush_markdown()
        safe_name = sanitize_project_name(project_name)
        project_folder = os.path.join(self.output_folder, safe_name)
        self.session = TimestampSession(project_folder, project_name=project_name)
        self.session.markdown_autoflush = False
        self.session.save()
        self.session.flush_markdown()
        self.project_name_var.set(project_name)
        self.recent_projects = update_recent_projects(
            self.recent_projects, project_name, project_folder
        )
        # Switching projects clears any active filter (and the toggle).
        self._filter_text = ""
        self._missing_audio_only = False
        if self._filter_var is not None:
            try:
                self._filter_var.set("")
            except tk.TclError:
                pass
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
            # Flush the derived Markdown log before dropping the session.
            self.session.flush_markdown()
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
        self._render_recent_rows(popup)
        self._place_recent_popup(popup)

        self._recent_popup = popup
        # Any click elsewhere in the main window dismisses the popup.
        self._popup_bind_id = self.root.bind(
            "<Button-1>", self._on_root_click_during_popup, add="+"
        )
        # Keep the popup anchored under the field while the main window moves
        # or resizes; an unmapped (minimized) window dismisses it so an
        # orphaned borderless popup can never linger on screen.
        self._popup_configure_bind_id = self.root.bind(
            "<Configure>", self._on_root_configure_during_popup, add="+"
        )
        self._popup_unmap_bind_id = self.root.bind(
            "<Unmap>", self._on_root_unmap_during_popup, add="+"
        )

    def _render_recent_rows(self, popup: ctk.CTkToplevel) -> None:
        """(Re)draw the popup header plus one stats row per recent project."""
        for child in popup.winfo_children():
            child.destroy()
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
            text, color = self._recent_row_text(entry)
            row = ctk.CTkFrame(popup, fg_color="transparent")
            row.pack(fill="x", padx=8, pady=2)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkButton(
                row,
                text=text,
                anchor="w",
                height=34,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BG_ENTRY,
                hover_color=Theme.BTN_SURFACE_HOVER,
                text_color=color,
                command=lambda n=name, f=folder: self._load_recent_project(n, f),
            ).grid(row=0, column=0, sticky="ew")
            ctk.CTkButton(
                row,
                text="✎",
                width=34,
                height=34,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BG_ENTRY,
                hover_color=Theme.BTN_SURFACE_HOVER,
                text_color=Theme.TEXT_DIM,
                command=lambda e=dict(entry): self._rename_recent_project(e),
            ).grid(row=0, column=1, padx=(4, 0))
            ctk.CTkButton(
                row,
                text="\U0001F5D1",
                width=34,
                height=34,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BG_ENTRY,
                hover_color=Theme.CRIMSON_HOVER,
                text_color=Theme.TEXT_DIM,
                command=lambda e=dict(entry): self._delete_recent_project(e),
            ).grid(row=0, column=2, padx=(4, 0))

    def _recent_row_text(self, entry: dict) -> tuple[str, str]:
        """Return (row text, text color) for one recent-project row.

        Rows show activity read from the project's session.json instead of
        the folder path; stale entries whose folder vanished render a dim
        missing marker so deletes degrade gracefully.
        """
        name = str(entry.get("name", ""))
        stored = str(entry.get("output_folder", ""))
        display_name = elide_middle(name, RECENT_ROW_NAME_MAX_CHARS)
        folder = self._resolve_project_folder(name, stored) if stored else ""
        if not folder or not os.path.isdir(folder):
            return f"{display_name}   ·   (folder missing)", Theme.TEXT_DIM
        stats = read_project_stats(folder)
        timestamps = stats["timestamps"]
        recordings = stats["recordings"]
        ts_word = "timestamp" if timestamps == 1 else "timestamps"
        rec_word = "recording" if recordings == 1 else "recordings"
        text = f"{display_name}   ·   {timestamps} {ts_word} · {recordings} {rec_word}"
        return text, Theme.TEXT_BRIGHT

    def _place_recent_popup(self, popup: ctk.CTkToplevel) -> None:
        """Size the popup to its row count and keep it near the entry field."""
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

    def _close_recent_popup(self) -> None:
        """Dismiss the recent-projects popup if it is showing."""
        popup = self._recent_popup
        self._recent_popup = None
        for attr, sequence in (
            ("_popup_bind_id", "<Button-1>"),
            ("_popup_configure_bind_id", "<Configure>"),
            ("_popup_unmap_bind_id", "<Unmap>"),
        ):
            bind_id = getattr(self, attr, None)
            if bind_id:
                try:
                    self.root.unbind(sequence, bind_id)
                except KeyError:
                    pass
                setattr(self, attr, None)
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

    def _on_root_configure_during_popup(self, event) -> None:
        """Re-anchor the open popup when the main window moves or resizes."""
        if self._closing:
            return
        popup = self._recent_popup
        if popup is None or not popup.winfo_exists():
            return
        # <Configure> bindings on root also fire for child widgets via
        # bindtag propagation; only the root window's own events may move us.
        if event.widget is not self.root:
            return
        self._place_recent_popup(popup)

    def _on_root_unmap_during_popup(self, event) -> None:
        """Dismiss the popup when the main window unmaps (e.g. minimize)."""
        if getattr(event, "widget", None) is not self.root:
            return
        self._close_recent_popup()

    def _resolve_project_folder(self, name: str, stored: str) -> str:
        """Return the actual project folder for a recent entry.

        Older entries stored the base output folder; newer ones store the
        project folder directly. Try stored directly first (contains
        session.json or basename matches sanitized name), otherwise join.
        """
        if not stored:
            return ""
        safe = sanitize_project_name(name)
        # Direct hit: stored itself holds the session.
        if os.path.isfile(os.path.join(stored, SESSION_FILENAME)):
            return os.path.abspath(stored)
        if os.path.normcase(os.path.basename(os.path.abspath(stored.rstrip(os.sep)))) == os.path.normcase(safe):
            return os.path.abspath(stored)
        return os.path.abspath(os.path.join(stored, safe))

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
        project_folder = self._resolve_project_folder(name, output_folder)
        # Base is parent of the project folder for future _set_project joins.
        base = os.path.dirname(project_folder) if project_folder else output_folder
        self.output_folder = base
        self.folder_label.configure(text=elide_middle(base, FOLDER_LABEL_MAX_CHARS))
        self.project_name_var.set(name)
        self._set_project()

    def _delete_recent_project(self, entry: dict) -> None:
        """Forget one recent project, recycling its folder behind a confirm.

        The folder is moved to the Recycle Bin first; only a successful move
        removes the entry from keybinds.json, so a locked or undeletable
        folder can never leave a phantom entry pointing at live files. A
        missing folder degrades to removing just the stale list entry.
        """
        name = str(entry.get("name", ""))
        stored = str(entry.get("output_folder", ""))
        folder = self._resolve_project_folder(name, stored)
        # Check if this is the currently open project.
        if self.session is not None and self._same_path(folder, self.session.output_dir):
            messagebox.showinfo(
                "Project in use",
                f'"{name}" is currently open.\nSelect another project before deleting it.',
                parent=self.root,
            )
            return
        if folder and os.path.isdir(folder):
            if not messagebox.askyesno(
                "Delete project",
                f'Delete "{name}"?\n\nIts folder will be moved to the Recycle Bin:\n{folder}',
                parent=self.root,
            ):
                return
            try:
                from send2trash import send2trash
            except ImportError:
                messagebox.showerror(
                    "send2trash missing",
                    "Moving folders to the Recycle Bin needs the send2trash package.\n\n"
                    "Install it with:\npython -m pip install send2trash",
                    parent=self.root,
                )
                return
            try:
                send2trash(folder)
            except Exception as exc:  # locked files, permission errors, …
                messagebox.showerror(
                    "Delete failed",
                    f'Could not recycle "{folder}":\n{exc}\n\nThe project stays in recent projects.',
                    parent=self.root,
                )
                return
            status_note = "Folder moved to Recycle Bin."
        else:
            if not messagebox.askyesno(
                "Remove project",
                f'Remove "{name}" from recent projects?\n\n'
                "Its folder was not found (already deleted or moved), so only this list entry is removed.",
                parent=self.root,
            ):
                return
            status_note = "Folder was already gone; removed the stale entry only."
        self.recent_projects = remove_recent_project(self.recent_projects, name, stored)
        self._save_config()
        self._set_status(f'Deleted "{name}". {status_note}', Theme.CRIMSON)
        self._refresh_recent_popup()

    def _rename_recent_project(self, entry: dict) -> None:
        """Rename a recent project after validating and renaming its folder."""
        name = str(entry.get("name", ""))
        stored = str(entry.get("output_folder", ""))
        folder = self._resolve_project_folder(name, stored)
        # Block renaming the currently open project (same file-lock hazard as delete).
        if self.session is not None:
            open_folder = self.session.output_dir
            if self._same_path(folder, open_folder):
                messagebox.showinfo("Project in use", f'"{name}" is currently open.\nSwitch to another project before renaming it.', parent=self.root)
                return
            # Also block when recent stores base but open project derives from it? Check name collision.
        from tkinter import simpledialog
        new_name = simpledialog.askstring("Rename project", f"New name for \"{name}\":", parent=self.root)
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name:
            messagebox.showwarning("Rename failed", "Project name cannot be empty.", parent=self.root)
            return
        if new_name == name:
            return
        new_safe = sanitize_project_name(new_name)
        # Check duplicate name+folder collision.
        for existing in self.recent_projects:
            if existing.get("name", "").casefold() == new_name.casefold() and self._same_path(str(existing.get("output_folder", "")), folder):
                # Same folder path would be same entry, skip; but if name collides with another entry's folder? Keep simple: name clash across recents blocks.
                pass
        if any(e.get("name", "").lower() == new_name.lower() for e in self.recent_projects if e is not entry):
            # Allow same name if folders differ, but warn if exact duplicate would occur.
            pass
        if not folder or not os.path.isdir(folder):
            # Missing folder: just update the list entry (migrate to resolved path).
            updated = []
            for e in self.recent_projects:
                if str(e.get("name", "")) == name and self._same_path(str(e.get("output_folder", "")), stored):
                    updated.append({"name": new_name, "output_folder": folder or stored})
                else:
                    updated.append(dict(e))
            self.recent_projects = sanitize_recent_projects(updated)
            self._save_config()
            self._set_status(f'Renamed "{name}" → "{new_name}" (folder was missing, only list updated).', Theme.BLUE)
            self._refresh_recent_popup()
            return
        parent_dir = os.path.dirname(os.path.abspath(folder))
        new_folder = os.path.join(parent_dir, new_safe)
        if os.path.normcase(os.path.abspath(new_folder)) != os.path.normcase(os.path.abspath(folder)) and os.path.exists(new_folder):
            messagebox.showerror("Rename failed", f'A folder already exists at:\n{new_folder}', parent=self.root)
            return
        try:
            if os.path.normcase(os.path.abspath(new_folder)) != os.path.normcase(os.path.abspath(folder)):
                os.rename(folder, new_folder)
            # Rename markdown file inside if present.
            old_md = os.path.join(new_folder, f"{sanitize_project_name(name)}.md")
            new_md = os.path.join(new_folder, f"{new_safe}.md")
            if os.path.isfile(old_md) and os.path.normcase(old_md) != os.path.normcase(new_md) and not os.path.exists(new_md):
                try:
                    os.rename(old_md, new_md)
                except OSError:
                    pass
            # Update session.json project_name if exists.
            meta = os.path.join(new_folder, SESSION_FILENAME)
            if os.path.isfile(meta):
                try:
                    with open(meta, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    data["project_name"] = new_name
                    with open(meta, "w", encoding="utf-8") as fh:
                        json.dump(data, fh, indent=2)
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
        except OSError as exc:
            messagebox.showerror("Rename failed", f"Could not rename folder:\n{exc}", parent=self.root)
            return
        # Update recent list entry (match by original stored, not resolved).
        updated = []
        for e in self.recent_projects:
            if str(e.get("name", "")) == name and self._same_path(str(e.get("output_folder", "")), stored):
                updated.append({"name": new_name, "output_folder": new_folder})
            else:
                updated.append(dict(e))
        self.recent_projects = sanitize_recent_projects(updated)
        self._save_config()
        self._set_status(f'Renamed "{name}" → "{new_name}".', Theme.GREEN)
        self._refresh_recent_popup()

    def _refresh_recent_popup(self) -> None:
        """Redraw popup rows after a delete; close it when none remain."""
        popup = self._recent_popup
        if popup is None or not popup.winfo_exists():
            return
        if not self.recent_projects:
            self._close_recent_popup()
            return
        self._render_recent_rows(popup)
        self._place_recent_popup(popup)

    @staticmethod
    def _same_path(left: str, right: str) -> bool:
        """Compare two paths Windows-style: absolute, separator- and case-blind."""
        normalize = lambda path: os.path.normcase(
            os.path.abspath(os.path.expanduser(str(path).strip()))
        )
        return normalize(left) == normalize(right)

    # ── Timestamp actions ─────────────────────────────────────────────────────

    def create_timestamp(self) -> None:
        if self._closing or not self.session or not self.session.timer_running:
            self._set_status(
                "Start the timer (in OBS or with ▶ Start timer) before creating timestamps.",
                Theme.AMBER,
            )
            return
        if getattr(self.session, "timer_paused", False):
            self._set_status("Timer is paused — resume before creating timestamps.", Theme.AMBER)
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

    def _quick_take(self, entry_id: int) -> None:
        """Record one more audio take straight from a completed row.

        Toggle behavior: starts mic capture exactly like clicking a pending
        row; while that row records, pressing again stops it and the file
        lands as a new active take via mark_completed. Entries without
        saved audio fall through — their rows already record on click.
        """
        if not self.session:
            return
        entry = self.session.get(entry_id)
        if not entry:
            return
        if entry.status == "recording":
            self._stop_entry_recording(entry)
        elif entry.status == "completed":
            self._start_entry_recording(entry)

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
        self._start_mic_meter()
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
            self._stop_mic_meter()
            self._schedule_list_refresh()
            self._set_status(
                f"Saved {os.path.basename(output_path)} ({duration:.1f}s).", Theme.GREEN
            )
        except AudioError as exc:
            self.session.mark_error(entry, str(exc))
            self.recording_entry_id = None
            self._stop_mic_meter()
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
        # If the edit dialog for this entry is open, enable its header button live.
        if (
            self._edit_dialog is not None
            and self._edit_dialog.winfo_exists()
            and getattr(self._edit_dialog, "_entry_id", None) == entry_id
        ):
            try:
                self._edit_dialog.refresh_screenshot_state()
            except tk.TclError:
                pass
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
            on_save=lambda label, tags, seconds: self._apply_entry_edit(entry_id, label, tags, seconds),
            on_manage_tags=lambda: self._open_tag_manager(focus_name=True),
        )

    def _apply_entry_edit(self, entry_id: int, label: str, tags: list[str], elapsed_seconds=None) -> None:
        if not self.session or self._closing:
            return
        try:
            if elapsed_seconds is not None:
                self.session.update_entry_time(entry_id, float(elapsed_seconds))
            self.session.update_entry(entry_id, label, tags)
        except (KeyError, ValueError) as exc:
            self._set_status(str(exc), Theme.RED)
            return
        self._schedule_list_refresh()
        entry = self.session.get(entry_id)
        ref = format_entry_ref(entry) if entry else str(entry_id)
        self._set_status(f"Updated timestamp {ref}.", Theme.GREEN)

    # ── Edit-dialog audio takes ─────────────────────────────────────────────

    def _toggle_take_playback(self, entry_id: int, take_index: int, on_state_change=None) -> None:
        """Play/stop one specific take from the edit dialog's take list."""
        if not self.session:
            return
        entry = self.session.get(entry_id)
        if not entry or not (0 <= take_index < len(entry.takes)):
            return
        path = os.path.join(
            self.session.output_dir, str(entry.takes[take_index].get("file") or "")
        )
        if (
            self.playback.current_path
            and os.path.abspath(self.playback.current_path) == os.path.abspath(path)
        ):
            self.playback.stop()
            if self._playback_job:
                self.root.after_cancel(self._playback_job)
                self._playback_job = None
            self._set_status("Playback stopped.")
            if on_state_change:
                on_state_change()
            self._schedule_list_refresh()
            return
        if self.playback.active:
            self.playback.stop()
            if self._playback_job:
                self.root.after_cancel(self._playback_job)
                self._playback_job = None
        try:
            duration = self.playback.play(path)
        except AudioError as exc:
            self._set_status(str(exc), Theme.RED)
            return
        self._set_status(f"Playing {os.path.basename(path)}.", Theme.BLUE)
        if on_state_change:
            on_state_change()
        self._schedule_list_refresh()

        def finished() -> None:
            self._playback_job = None
            if on_state_change:
                on_state_change()
            self._schedule_list_refresh()

        self._playback_job = self.root.after(
            max(100, int(duration * 1000) + 100), finished
        )

    def _dialog_rerecord_start(self, entry_id: int, dialog) -> None:
        """Start a re-record for an entry from inside its edit dialog."""
        if not self.session:
            return
        entry = self.session.get(entry_id)
        if not entry:
            return
        if self.recorder.active:
            self._set_status("Stop the current timestamp recording first.", Theme.AMBER)
            return
        if self.playback.active:
            self.playback.stop()
            if self._playback_job:
                self.root.after_cancel(self._playback_job)
                self._playback_job = None
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
        self._start_mic_meter()
        try:
            if dialog.winfo_exists():
                dialog.enter_recording_mode()
        except tk.TclError:
            pass
        self._set_status(
            f"Re-recording {format_entry_ref(entry)}. Press ■ Stop when done.",
            Theme.RED,
        )

    def _dialog_rerecord_stop(self, entry_id: int, dialog) -> None:
        """Stop the in-dialog re-record; the new file becomes the active take."""
        entry = self.session.get(entry_id) if self.session else None
        if not entry or self.recording_entry_id != entry.id:
            return
        self._stop_entry_recording(entry)
        try:
            if dialog.winfo_exists():
                dialog.exit_recording_mode()
                dialog.refresh_takes()
        except tk.TclError:
            pass

    def _dialog_set_active_take(self, entry_id: int, take_index: int, dialog) -> None:
        """Make one of the entry's takes the active (headline) audio."""
        if not self.session:
            return
        entry = self.session.get(entry_id)
        if not entry:
            return
        try:
            self.session.set_active_take(entry, take_index)
        except (IndexError, FileNotFoundError) as exc:
            self._set_status(f"Could not switch take: {exc}", Theme.RED)
            return
        try:
            if dialog.winfo_exists():
                dialog.refresh_takes()
        except tk.TclError:
            pass
        self._schedule_list_refresh()
        self._set_status(
            f"Take {take_index + 1} is now active for {format_entry_ref(entry)}.",
            Theme.GREEN,
        )

    def _dialog_delete_take(self, entry_id: int, take_index: int, dialog) -> None:
        """Confirm, then permanently delete one take of an entry."""
        if not self.session:
            return
        entry = self.session.get(entry_id)
        if not entry or not (0 <= take_index < len(entry.takes)):
            return
        take_name = os.path.basename(str(entry.takes[take_index].get("file") or "take"))
        if not messagebox.askyesno(
            "Delete take",
            f"Permanently delete '{take_name}'?",
            parent=dialog if dialog.winfo_exists() else self.root,
        ):
            return
        # Stop playback first so Windows does not hold the WAV file open.
        path = os.path.abspath(
            os.path.join(
                self.session.output_dir,
                str(entry.takes[take_index].get("file") or ""),
            )
        )
        if (
            self.playback.current_path
            and os.path.abspath(self.playback.current_path) == path
        ):
            self.playback.stop()
            if self._playback_job:
                self.root.after_cancel(self._playback_job)
                self._playback_job = None
        try:
            removed = self.session.remove_take(entry, take_index)
        except (IndexError, ValueError, OSError) as exc:
            self._set_status(f"Could not delete take: {exc}", Theme.RED)
            return
        try:
            if dialog.winfo_exists():
                dialog.refresh_takes()
        except tk.TclError:
            pass
        self._schedule_list_refresh()
        message = f"Deleted {os.path.basename(str(removed.get('file') or 'take'))}."
        if entry.status == "pending":
            message += " Timestamp has no audio left — click it to record again."
        self._set_status(message, Theme.BLUE)

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
            message += "\n\nIts WAV file will be moved to the Recycle Bin."
        if entry.screenshot_file:
            message += "\nIts screenshot will be moved to the Recycle Bin."
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
        ref = format_entry_ref(entry)
        if problems:
            self._set_status(
                f"Could not delete timestamp {ref}: " + "; ".join(problems) + " — entry kept so you can retry after freeing the file.",
                Theme.RED,
            )
            return
        self._schedule_list_refresh()
        self._set_status(f"Deleted timestamp {ref} (files moved to Recycle Bin).", Theme.GREEN)

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

    def _open_with_default_app(self, path: str, label: str) -> bool:
        """Open a file with the OS default application; False on failure.

        Windows uses os.startfile; macOS/Linux shell out to open/xdg-open.
        Failures are reported in the status bar with ``label`` for context.
        """
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([opener, path])
        except Exception as exc:
            self._set_status(f"Could not open {label}: {exc}", Theme.RED)
            return False
        return True

    def _open_recording_video(self, recording_number: int) -> None:
        """Open a segment's main OBS recording video in the default player."""
        if not self.session:
            return
        recording = next(
            (
                item
                for item in self.session.recordings
                if item.number == recording_number
            ),
            None,
        )
        if not recording or not recording.path:
            return
        path = recording.path
        if not os.path.isfile(path):
            self._set_status(f"Recording video not found: {path}", Theme.RED)
            return
        if not self._open_with_default_app(path, "recording"):
            return
        self._set_status(f"Opened recording {os.path.basename(path)}.", Theme.BLUE)

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
        if not self._open_with_default_app(path, "replay video"):
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
        if not self._open_with_default_app(path, "screenshot"):
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

    # ── Filter & pause helpers ─────────────────────────────────────────────

    def _on_filter_change(self) -> None:
        text = self._filter_var.get().strip().lower() if self._filter_var else ""
        if text != self._filter_text:
            self._filter_text = text
            self._schedule_list_refresh()

    def _on_missing_audio_toggle(self) -> None:
        self._missing_audio_only = bool(self.missing_audio_checkbox.get())
        self._schedule_list_refresh()

    def _clear_filter(self) -> None:
        if self._filter_var:
            self._filter_var.set("")
        self._filter_text = ""
        self._missing_audio_only = False
        try:
            self.missing_audio_checkbox.deselect()
        except tk.TclError:
            pass
        self._schedule_list_refresh()

    @staticmethod
    def _is_missing_audio_replay(entry: TimestampEntry) -> bool:
        """A replay still needing an audio take: pending or error.

        A currently-recording replay is deliberately excluded — it is being
        handled right now — and re-enters this set if the take discards.
        """
        return entry.kind == "replay" and entry.status in ("pending", "error")

    @property
    def _filter_active(self) -> bool:
        """True when any list filter is active (search text or the
        Missing-audio toggle), so hidden rows stay cached instead of
        being destroyed and rebuilt when the filter is later cleared."""
        return bool(self._filter_text or self._missing_audio_only)

    def _filtered_empty_text(self) -> str:
        """Placeholder shown when the session has entries but none match."""
        if self._filter_text:
            return f"No timestamps match \"{self._filter_text}\" — clear the filter to see all."
        if self._missing_audio_only:
            return "No replays missing audio 🎉 — uncheck Missing audio to see all."
        return "No timestamps match the active filter."

    def _entry_matches_filter(self, entry: TimestampEntry) -> bool:
        if self._missing_audio_only and not self._is_missing_audio_replay(entry):
            return False
        if not self._filter_text:
            return True
        needle = self._filter_text
        haystack_parts = [
            entry.label or "",
            format_entry_ref(entry),
            format_elapsed_display(entry.elapsed_seconds),
        ]
        haystack_parts.extend(entry.tags)
        if entry.kind == "replay" and entry.replay_file:
            haystack_parts.append(replay_display_name(entry.replay_file))
        haystack = " ".join(haystack_parts).lower()
        return needle in haystack

    def _toggle_pause(self) -> None:
        if not self.session or not self.session.timer_running:
            return
        # Pause is manual-timer only: while OBS drives the session the app clock must match footage.
        if self.obs_manager.is_connected and self.obs_manager.is_recording:
            self._set_status("Pause is available only for the manual ▶ timer (OBS is recording).", Theme.AMBER)
            return
        if self.session.timer_paused:
            self.session.resume_timer()
            self._set_status("Timer resumed.", Theme.GREEN)
        else:
            self.session.pause_timer()
            self._set_status("Timer paused — timestamps keep the frozen time until resumed.", Theme.AMBER)
        self._update_action_state()
        self._schedule_list_refresh()

    # ── Timestamp list ────────────────────────────────────────────────────────

    def _schedule_list_refresh(self) -> None:
        """Coalesce list repaints: a burst of state changes produces one
        refresh ~30 ms later instead of one full pass per mutation.

        Every session mutation flows through here, so this is also where the
        debounced Markdown-log write is armed (see _schedule_markdown_flush).
        """
        self._schedule_markdown_flush()
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

    def _schedule_markdown_flush(self) -> None:
        """Coalesce Markdown log writes: one flush ~1 s after the last burst.

        session.json (the product's data) is written synchronously on every
        mutation; only the derived Markdown log is debounced. Destructive
        transitions flush immediately via explicit flush_markdown() calls.
        """
        if self._closing or self._markdown_flush_job is not None:
            return
        try:
            self._markdown_flush_job = self.root.after(
                1000, self._run_markdown_flush
            )
        except tk.TclError:
            self._markdown_flush_job = None

    def _run_markdown_flush(self) -> None:
        self._markdown_flush_job = None
        if not self._closing and self.session is not None:
            self.session.flush_markdown()

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
        """Scroll the timestamp list to its newest entry once layout settles.

        No ``update_idletasks()`` here: forcing a full layout pass over every
        widget made adding a row to a long list feel heavy. Tk runs the
        geometry idle handlers (registered by the fresh ``grid()`` calls)
        before this ``after_idle`` callback, so the scrollregion is already
        up to date; a second chained idle pass retries in case layout
        landed later than expected.
        """
        if self._closing:
            return

        def _scroll() -> None:
            canvas = self._timestamp_list_canvas()
            if canvas is None:
                return

            def _retry() -> None:
                try:
                    if canvas.yview()[1] < 0.999:
                        canvas.yview_moveto(1.0)
                except tk.TclError:
                    pass

            try:
                canvas.yview_moveto(1.0)
                canvas.after_idle(_retry)
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
                    text="",
                    font=Theme.FONT_BODY,
                    text_color=Theme.TEXT_DIM,
                    wraplength=520,
                    justify="center",
                )
            if not self.session:
                empty_text = "Choose an output folder, enter a project name, then click Set project."
            elif not self.session.timer_running:
                empty_text = "Start recording in OBS or click ▶ Start timer to create timestamps."
            else:
                hotkey = self._hotkey_display(self.timestamp_key)
                empty_text = f"Press {hotkey} or New timestamp to capture your first note."
                if self._filter_text:
                    empty_text = f"No timestamps match \"{self._filter_text}\" — clear the filter to see all."
            self._empty_label.configure(text=empty_text)
            self._empty_label.grid(row=0, column=0, padx=12, pady=30)
            return

        if self._empty_label is not None:
            self._empty_label.destroy()
            self._empty_label = None

        # Single pass over the entries: group matched and unfiltered entries
        # per recording number so the layout build below costs O(entries +
        # recordings) instead of re-scanning every entry once per segment (and
        # running the substring filter more than once per entry).
        session = self.session
        matched_by_recording: dict[int | None, list[TimestampEntry]] = {}
        all_by_recording: dict[int | None, list[TimestampEntry]] = {}
        matched_count = 0
        for entry in session.entries:
            key = entry.recording_number
            all_by_recording.setdefault(key, []).append(entry)
            if self._entry_matches_filter(entry):
                matched_count += 1
                matched_by_recording.setdefault(key, []).append(entry)

        # Filtered-out: no visible entries but session has entries. (A zero
        # match count can only happen with an active filter here, since
        # non-empty sessions without a filter match everything.)
        if matched_count == 0:
                self._drop_all_rows_and_headers()
                if self._empty_label is None:
                    self._empty_label = ctk.CTkLabel(
                        self.timestamp_list,
                        text=self._filtered_empty_text(),
                        font=Theme.FONT_BODY,
                        text_color=Theme.TEXT_DIM,
                        wraplength=520,
                        justify="center",
                    )
                self._empty_label.configure(text=self._filtered_empty_text())
                self._empty_label.grid(row=0, column=0, padx=12, pady=30)
                return

        # Desired layout in display order: (kind, key, ...) items.
        items: list[tuple] = []
        earlier = matched_by_recording.get(None, [])
        earlier_all = all_by_recording.get(None, [])
        # When filtering, hide the "Earlier" header if none of its entries match.
        if earlier_all and not earlier and self._filter_active:
            earlier = []
        if earlier:
            items.append(
                ("header", "earlier", "Earlier timestamps", False, len(earlier), None)
            )
            items.extend(("entry", entry.id, entry) for entry in earlier)

        for recording in sorted(session.recordings, key=lambda item: item.number):
            grouped = matched_by_recording.get(recording.number, [])
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
                    recording,
                )
            )
            items.extend(("entry", entry.id, entry) for entry in grouped)
            if recording.ended_at is not None:
                items.append(
                    ("footer", ("rec", recording.number), recording)
                )

        seen_rows: set[int] = set()
        seen_headers: set[object] = set()
        # Playing state is the same comparison for every row: resolve the
        # playback path once instead of calling os.path.abspath per row.
        playback_key = (
            os.path.abspath(self.playback.current_path)
            if self.playback.current_path
            else None
        )
        for grid_row, item in enumerate(items):
            if item[0] == "header":
                _, key, title, live, count, recording = item
                widgets = self._header_widgets.get(key)
                if widgets is None:
                    widgets = self._create_header_widgets(recording)
                    self._header_widgets[key] = widgets
                prefix = "● " if live else ""
                suffix = f"   ·   {count} entries" if count else ""
                rendered = (
                    f"{prefix}{title}{suffix}",
                    Theme.GREEN if live else Theme.TEXT_DIM,
                )
                if widgets["rendered"] != rendered:
                    widgets["label"].configure(
                        text=rendered[0], text_color=rendered[1]
                    )
                    widgets["rendered"] = rendered
                open_button = widgets["open_button"]
                if open_button is not None:
                    # The path lands with the OBS record-start event and is
                    # confirmed by the stop event, so re-derive each refresh.
                    open_state = "normal" if recording.path else "disabled"
                    if widgets["open_state"] != open_state:
                        open_button.configure(state=open_state)
                        widgets["open_state"] = open_state
                if widgets["grid_row"] != grid_row:
                    widgets["frame"].grid(
                        row=grid_row, column=0, padx=0, pady=0, sticky="ew"
                    )
                    widgets["grid_row"] = grid_row
                seen_headers.add(key)
            elif item[0] == "footer":
                _, key, footer_recording = item
                footer = self._footer_labels.get(key)
                if footer is None:
                    footer = {
                        "label": ctk.CTkLabel(
                            self.timestamp_list,
                            font=Theme.FONT_SMALL,
                            anchor="w",
                        ),
                        "rendered": None,
                        "grid_row": None,
                    }
                    self._footer_labels[key] = footer
                    # A stop marker is new list content: let smart-follow
                    # scroll it into view like a freshly added row.
                    added_row = True
                duration = footer_recording.duration_seconds() or 0.0
                text = (
                    "■ Recording stopped — "
                    f"{format_elapsed_display(duration)}"
                )
                label = footer["label"]
                if footer["rendered"] != text:
                    label.configure(text=text, text_color=Theme.TEXT_DIM)
                    footer["rendered"] = text
                if footer["grid_row"] != grid_row:
                    label.grid(row=grid_row, column=0, padx=26, pady=(0, 6), sticky="w")
                    footer["grid_row"] = grid_row
                seen_headers.add(key)
            else:
                _, entry_id, entry = item
                widgets = self._list_rows.get(entry_id)
                if widgets is None:
                    widgets = self._create_row_widgets(entry)
                    self._list_rows[entry_id] = widgets
                    added_row = True
                self._update_row_widgets(widgets, entry, playback_key)
                if widgets["grid_row"] != grid_row:
                    widgets["frame"].grid(
                        row=grid_row, column=0, padx=2, pady=1, sticky="ew"
                    )
                    widgets["grid_row"] = grid_row
                seen_rows.add(entry_id)

        # Drop cached widgets for entries/segments that no longer exist. With
        # an active filter, entries that merely stopped matching keep their
        # rows cached and hidden (grid_remove) so clearing the filter restores
        # them without recreation; without a filter, gone rows were deleted.
        for entry_id in [eid for eid in self._list_rows if eid not in seen_rows]:
            widgets = self._list_rows.pop(entry_id)
            if self._filter_active:
                try:
                    widgets["frame"].grid_remove()
                    widgets["grid_row"] = None
                except tk.TclError:
                    pass
            else:
                widgets["frame"].destroy()
        for key in [k for k in self._header_widgets if k not in seen_headers]:
            self._header_widgets.pop(key)["frame"].destroy()
        for key in [k for k in self._footer_labels if k not in seen_headers]:
            self._footer_labels.pop(key)["label"].destroy()

        if added_row and follow:
            self._scroll_timestamp_list_to_bottom()

    def _drop_all_rows_and_headers(self) -> None:
        """Destroy every cached row/header/footer widget (not the empty label)."""
        for widgets in self._list_rows.values():
            widgets["frame"].destroy()
        self._list_rows.clear()
        for widgets in self._header_widgets.values():
            widgets["frame"].destroy()
        self._header_widgets.clear()
        for footer in self._footer_labels.values():
            footer["label"].destroy()
        self._footer_labels.clear()

    def _create_row_widgets(self, entry: TimestampEntry) -> dict:
        """Build the widget tree for one compact timestamp row (once per entry).

        One thin line: status dot · ref+time+state text · label/tag chips ·
        mini action icons (📷 and 🎙 on timestamps, 🎬 and 🎙 on replays, ✎, ✕). The whole row — frame and
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
        take_button = None
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
            # Replays get the same quick-take control as timestamps so a
            # clip can be (re-)recorded without opening the edit dialog.
            # Visibility and look are driven by _update_row_widgets.
            take_button = ctk.CTkButton(
                actions,
                text="🎙",
                width=26,
                height=22,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BTN_SURFACE,
                hover_color=Theme.BTN_SURFACE_HOVER,
                command=lambda entry_id=entry_id: self._quick_take(entry_id),
            )
            take_button.grid(row=0, column=next_action_column, padx=(0, 2))
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
            # Quick "record another take" for completed entries. Visibility
            # and look are driven by _update_row_widgets: hidden while
            # pending/error, ■ Stop while this row captures.
            take_button = ctk.CTkButton(
                actions,
                text="🎙",
                width=26,
                height=22,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BTN_SURFACE,
                hover_color=Theme.BTN_SURFACE_HOVER,
                command=lambda entry_id=entry_id: self._quick_take(entry_id),
            )
            take_button.grid(row=0, column=next_action_column, padx=(0, 2))
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
            "signature": None,
            "grid_row": None,
            "shot_button": shot_button,
            "take_button": take_button,
        }

    def _create_header_widgets(self, recording: RecordingInfo | None) -> dict:
        """Build the widget tree for one section header (once per segment).

        Recording headers carry a small 📼 button that opens the segment's
        OBS recording video; it starts disabled and stays greyed until the
        session knows the file path (segments from the in-app timer toggle
        or older sessions never get one). The "Earlier timestamps" header
        passes ``None`` and renders label-only.
        """
        frame = ctk.CTkFrame(self.timestamp_list, fg_color="transparent")
        label = ctk.CTkLabel(frame, font=Theme.FONT_SMALL, anchor="w")
        label.grid(row=0, column=0, padx=12, pady=(8, 1), sticky="w")
        open_button = None
        if recording is not None:
            open_button = ctk.CTkButton(
                frame,
                text="📼",
                width=26,
                height=20,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BTN_SURFACE,
                hover_color=Theme.GREEN_HOVER,
                command=lambda number=recording.number: self._open_recording_video(
                    number
                ),
                state="normal" if recording.path else "disabled",
            )
            open_button.grid(row=0, column=1, padx=(6, 0), pady=(8, 1), sticky="w")
        return {
            "frame": frame,
            "label": label,
            "open_button": open_button,
            "rendered": None,
            "open_state": None,
            "grid_row": None,
        }

    def _update_row_widgets(
        self,
        widgets: dict,
        entry: TimestampEntry,
        playback_key: str | None,
    ) -> None:
        """Reconfigure a cached row in place to match the entry's current state.

        A full-signature skip guards everything below: entries are immutable
        between user actions, so a steady-state refresh (no entry changed)
        performs no ``configure`` calls at all — canvas-backed CTk widgets
        redraw on every configure, which used to make long lists repaint
        themselves wholesale on each refresh.
        """
        assert self.session is not None
        is_playing = bool(
            entry.audio_file
            and playback_key is not None
            and os.path.abspath(os.path.join(self.session.output_dir, entry.audio_file))
            == playback_key
        )
        signature = (
            entry.status,
            entry.error,
            entry.duration_seconds,
            entry.audio_file,
            entry.screenshot_file,
            len(entry.takes),
            is_playing,
            entry.label,
            tuple(entry.tags),
            entry.elapsed_seconds,
        )
        if widgets["signature"] == signature:
            return
        widgets["signature"] = signature

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

        take_button = widgets.get("take_button")
        if take_button is not None:
            # Quick-take control: hidden until the entry has saved audio,
            # turns into ■ Stop while this very row captures.
            if entry.status == "recording":
                take_button.grid()
                take_button.configure(
                    text="■ Stop",
                    fg_color=Theme.RED,
                    hover_color=Theme.RED_HOVER,
                    state="normal",
                )
            elif entry.status == "completed":
                take_button.grid()
                take_button.configure(
                    text="🎙",
                    fg_color=Theme.BTN_SURFACE,
                    hover_color=Theme.BTN_SURFACE_HOVER,
                    state="normal",
                )
            else:
                take_button.grid_remove()

        meta_signature = (entry.label, tuple(entry.tags))
        if widgets["meta_signature"] != meta_signature:
            self._rebuild_meta_chips(widgets["meta_frame"], entry)
            widgets["meta_signature"] = meta_signature

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
        if len(entry.takes) > 1:
            chip = ctk.CTkLabel(
                meta_frame,
                text=f"🎙 {len(entry.takes)} takes",
                font=Theme.FONT_SMALL,
                text_color=Theme.TEXT_DIM,
            )
            chip.grid(
                row=0,
                column=next_column + len(visible_tags) + (1 if hidden_tags > 0 else 0),
                padx=(0, 4),
                sticky="w",
            )
            add_chip(chip)

    @staticmethod
    def _entry_label(entry: TimestampEntry, is_playing: bool) -> str:
        """Compact single-line text: ref · time · short state hint."""
        base = f"{format_entry_ref(entry)}   {format_elapsed_display(entry.elapsed_seconds)}"
        if entry.kind == "replay":
            base = f"{base}   REPLAY {elide_middle(replay_display_name(entry.replay_file) or '', 32)}"
            if entry.status == "pending":
                return f"{base}   · no audio — click to record"
            if entry.status == "recording":
                return f"{base}   ● recording — click to stop"
            if entry.status == "error":
                reason = elide_middle(str(entry.error or "unknown error"), 40)
                return f"{base}   · error, click to retry: {reason}"
            audio = f"   · audio {entry.duration_seconds:.1f}s" if entry.duration_seconds else ""
            suffix = "   ■ stop" if is_playing else ""
            return f"{base}{audio}{suffix}"
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
            # Replay rows are state-aware so missing audio stands out:
            # amber means the clip has no audio take yet (attention), red
            # means recording or a failed take, green means a take exists.
            # Identity comes from the REPLAY text and 🎬 button instead of
            # a dedicated dot color.
            if entry.status == "pending":
                return Theme.AMBER, Theme.AMBER
            if entry.status == "recording":
                return Theme.RED, Theme.RED
            if entry.status == "error":
                return Theme.RED, Theme.RED
            if entry.status == "completed":
                if is_playing:
                    return Theme.BLUE, Theme.BLUE
                return Theme.GREEN, Theme.TEXT_BRIGHT
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

    def _open_obs_settings(self) -> None:
        if self._obs_settings_dialog is not None and self._obs_settings_dialog.winfo_exists():
            try:
                self._obs_settings_dialog._focus()
            except tk.TclError:
                pass
            return
        self._obs_settings_dialog = ObsSettingsDialog(self, dict(self.obs_settings), self._apply_obs_settings)

    def _apply_obs_settings(self, new_settings: dict) -> None:
        self.obs_settings = dict(new_settings)
        self._save_config()
        self._set_status(f"OBS settings saved ({self.obs_settings['host']}:{self.obs_settings['port']}).", Theme.BLUE)
        was_connected = self.obs_manager.is_connected
        auto = bool(self.obs_settings.get("auto_connect", True))
        # Tear down with new params, then reconnect per new auto flag.
        if was_connected:
            try:
                self.obs_manager.disconnect()
            except Exception:
                pass
            if auto:
                self.obs_manager.enable_auto_reconnect(host=self.obs_settings.get("host", "localhost"), port=self.obs_settings.get("port", 4455), password=self.obs_settings.get("password", ""))
            self.obs_manager.connect(self.obs_settings.get("host", "localhost"), self.obs_settings.get("port", 4455), self.obs_settings.get("password", ""))
            self._on_obs_status_change("connecting")
        elif auto:
            self.obs_manager.enable_auto_reconnect(host=self.obs_settings.get("host", "localhost"), port=self.obs_settings.get("port", 4455), password=self.obs_settings.get("password", ""))
            self.obs_manager.connect(self.obs_settings.get("host", "localhost"), self.obs_settings.get("port", 4455), self.obs_settings.get("password", ""))
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
                self._start_obs_timer(name, output_path)

        try:
            self.root.after(0, update)
        except tk.TclError:
            pass

    def _on_obs_recording_stopped(self, output_path=None) -> None:
        name = recording_display_name(output_path)

        def update() -> None:
            if not self._closing:
                self._stop_obs_timer(name, output_path)

        try:
            self.root.after(0, update)
        except tk.TclError:
            pass

    def _start_obs_timer(
        self,
        recording_name: str | None = None,
        recording_path: str | None = None,
    ) -> None:
        if not self.session:
            self._update_action_state()
            self._set_status("OBS is recording. Set a project before adding timestamps.", Theme.AMBER)
            return
        self.session.start_timer(recording_name)
        if recording_path and self.session.current_recording_number is not None:
            self.session.set_recording_path(
                self.session.current_recording_number, str(recording_path)
            )
        self._update_action_state()
        self._schedule_list_refresh()
        segment = f" ({recording_name})" if recording_name else ""
        self._set_status(
            f"OBS recording started for '{self.session.project_name}'{segment}.",
            Theme.GREEN,
        )

    def _stop_obs_timer(
        self,
        recording_name: str | None = None,
        recording_path: str | None = None,
    ) -> None:
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
        # Same for the video path; must run before stop_timer clears the
        # current segment number. The later event confirms/corrects the
        # value stored at start.
        if (
            self.session
            and recording_path
            and self.session.current_recording_number is not None
        ):
            self.session.set_recording_path(
                self.session.current_recording_number, str(recording_path)
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
        is_paused = bool(self.session and getattr(self.session, "timer_paused", False))
        self.new_timestamp_button.configure(
            state="normal" if timer_running and not is_paused else "disabled"
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
        if timer_running:
            self.pause_button.grid()
            obs_drives = bool(self.obs_manager.is_connected and self.obs_manager.is_recording)
            if is_paused:
                self.pause_button.configure(text="▶ Resume", fg_color=Theme.GREEN, hover_color=Theme.GREEN_HOVER, state="normal")
            else:
                self.pause_button.configure(text="⏸ Pause", fg_color=Theme.GREY if obs_drives else Theme.BTN_SURFACE, hover_color=Theme.GREY_HOVER if obs_drives else Theme.BTN_SURFACE_HOVER, state="disabled" if obs_drives else "normal")
        else:
            try:
                self.pause_button.grid_remove()
            except tk.TclError:
                pass

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
                self._obs_last_failure_reason = None
                self._obs_was_up = True
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
                # exactly like a disconnect would — but ONLY on that one
                # connected→dropped transition. While OBS is merely
                # unreachable this status repeats on every retry cycle and
                # must leave a manual ▶ timer or microphone recording alone.
                if self._obs_was_up:
                    self._obs_was_up = False
                    if (self.session and self.session.timer_running) or self.recorder.active:
                        self._stop_obs_timer()
                self.obs_status_label.configure(text="● Waiting for OBS…", text_color=Theme.AMBER)
                self.obs_connect_button.configure(text="Connect OBS")
                # Surface the waiting reason once per distinct failure so
                # "Wrong password" does not hide behind a silent amber dot.
                # Duplicate "waiting" pulses every ~5 s are otherwise quiet.
            elif status == "disconnected":
                was_up = self._obs_was_up
                self._obs_was_up = False
                if was_up:
                    self._stop_obs_timer()
                self.obs_status_label.configure(text="● OBS disconnected", text_color=Theme.RED)
                self.obs_connect_button.configure(text="Connect OBS")
            elif status.startswith("auth_error:"):
                self._obs_was_up = False
                reason = status[11:].strip()
                if reason != self._obs_last_failure_reason:
                    self._obs_last_failure_reason = reason
                    self.obs_status_label.configure(text="● OBS auth failed", text_color=Theme.RED)
                    self.obs_connect_button.configure(text="Connect OBS")
                    self._set_status(f"OBS refused the password — click ⚙ to edit connection settings. ({reason})", Theme.RED)
                else:
                    self.obs_status_label.configure(text="● OBS auth failed", text_color=Theme.RED)
                    self.obs_connect_button.configure(text="Connect OBS")
            elif status.startswith("error:"):
                self._obs_was_up = False
                reason = status[6:].strip()
                if reason != self._obs_last_failure_reason:
                    self._obs_last_failure_reason = reason
                    self.obs_status_label.configure(text="● OBS error", text_color=Theme.RED)
                    self.obs_connect_button.configure(text="Connect OBS")
                    self._set_status(reason, Theme.RED)
                else:
                    self.obs_status_label.configure(text="● OBS error", text_color=Theme.RED)
                    self.obs_connect_button.configure(text="Connect OBS")

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
        is_paused = bool(self.session and getattr(self.session, "timer_paused", False))
        if self.session and self.session.timer_running and current is not None:
            if is_paused:
                self.rec_indicator_label.configure(text=f"● PAUSED {current.number}", text_color=Theme.AMBER)
            else:
                self.rec_indicator_label.configure(text=f"● REC {current.number}", text_color=Theme.GREEN)
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
            self.session.flush_markdown()
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


class ObsSettingsDialog(OverlayDialog):
    """Modal dialog to edit OBS WebSocket connection settings."""

    def __init__(self, app, obs_settings: dict, on_save) -> None:
        super().__init__(app, 380, 360)
        self._on_save = on_save
        header = ctk.CTkFrame(self.card, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(14, 4))
        ctk.CTkLabel(
            header, text="OBS connection", font=Theme.FONT_SUBTITLE,
            text_color=Theme.TEXT_BRIGHT, anchor="w",
        ).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            header, text="✕", width=28, height=28, font=Theme.FONT_SMALL,
            fg_color=Theme.BTN_SURFACE, hover_color=Theme.RED_HOVER, command=self.destroy
        ).pack(side="right")
        ctk.CTkLabel(
            self.card,
            text="Host, port, and password for OBS WebSocket (Tools → WebSocket Server Settings).",
            font=Theme.FONT_SMALL, text_color=Theme.TEXT_DIM, anchor="w", wraplength=340, justify="left",
        ).pack(fill="x", padx=18, pady=(0, 10))
        # Host
        ctk.CTkLabel(self.card, text="Host", font=Theme.FONT_SMALL, text_color=Theme.TEXT_DIM, anchor="w").pack(fill="x", padx=18)
        self.host_entry = ctk.CTkEntry(self.card, font=Theme.FONT_BODY, placeholder_text="localhost")
        self.host_entry.insert(0, str(obs_settings.get("host", "localhost")))
        self.host_entry.pack(fill="x", padx=18, pady=(2, 8))
        # Port
        ctk.CTkLabel(self.card, text="Port", font=Theme.FONT_SMALL, text_color=Theme.TEXT_DIM, anchor="w").pack(fill="x", padx=18)
        self.port_entry = ctk.CTkEntry(self.card, font=Theme.FONT_BODY, placeholder_text="4455")
        self.port_entry.insert(0, str(obs_settings.get("port", 4455)))
        self.port_entry.pack(fill="x", padx=18, pady=(2, 8))
        # Password
        ctk.CTkLabel(self.card, text="Password (leave empty if none)", font=Theme.FONT_SMALL, text_color=Theme.TEXT_DIM, anchor="w").pack(fill="x", padx=18)
        pw_row = ctk.CTkFrame(self.card, fg_color="transparent")
        pw_row.pack(fill="x", padx=18, pady=(2, 4))
        self.password_entry = ctk.CTkEntry(pw_row, font=Theme.FONT_BODY, placeholder_text="Password", show="•")
        self.password_entry.insert(0, str(obs_settings.get("password", "")))
        self.password_entry.pack(side="left", fill="x", expand=True)
        self._pw_visible = False
        ctk.CTkButton(pw_row, text="Show", width=56, height=28, font=Theme.FONT_SMALL, fg_color=Theme.BTN_SURFACE, hover_color=Theme.BTN_SURFACE_HOVER, command=self._toggle_pw).pack(side="left", padx=(6, 0))
        self._show_btn = pw_row.winfo_children()[-1]
        # Auto-connect
        self.auto_var = tk.BooleanVar(value=bool(obs_settings.get("auto_connect", True)))
        self.auto_check = ctk.CTkCheckBox(self.card, text="Auto-connect & reconnect", variable=self.auto_var, font=Theme.FONT_SMALL, text_color=Theme.TEXT_BRIGHT)
        self.auto_check.pack(anchor="w", padx=18, pady=(10, 4))
        self.feedback_label = ctk.CTkLabel(self.card, text="", font=Theme.FONT_SMALL, text_color=Theme.RED, anchor="w", wraplength=340, justify="left")
        self.feedback_label.pack(fill="x", padx=18, pady=(4, 0))
        btns = ctk.CTkFrame(self.card, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(10, 16))
        btns.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(btns, text="Save", width=100, font=Theme.FONT_BUTTON, fg_color=Theme.GREEN, hover_color=Theme.GREEN_HOVER, text_color="#000000", command=self._save).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(btns, text="Cancel", width=100, font=Theme.FONT_BUTTON, fg_color=Theme.GREY, hover_color=Theme.GREY_HOVER, command=self._close).grid(row=0, column=0, sticky="e")
        self.after(60, self._focus)

    def _focus(self) -> None:
        try:
            self.lift(); self.card.lift(); self.grab_set(); self.host_entry.focus_set()
        except tk.TclError:
            pass

    def on_escape(self) -> None:
        self._close()

    def _close(self) -> None:
        try:
            self.app._obs_settings_dialog = None
        except Exception:
            pass
        self.destroy()

    def destroy(self) -> None:
        try:
            self.app._obs_settings_dialog = None
        except Exception:
            pass
        super().destroy()

    def _toggle_pw(self) -> None:
        self._pw_visible = not self._pw_visible
        self.password_entry.configure(show="" if self._pw_visible else "•")
        self._show_btn.configure(text="Hide" if self._pw_visible else "Show")

    def _save(self) -> None:
        host = self.host_entry.get().strip() or "localhost"
        port_text = self.port_entry.get().strip() or "4455"
        try:
            port = int(port_text)
            if not 1 <= port <= 65535:
                raise ValueError()
        except ValueError:
            self.feedback_label.configure(text="Port must be 1–65535.")
            return
        password = self.password_entry.get()
        auto = bool(self.auto_var.get())
        self.destroy()
        self._on_save({"host": host, "port": port, "password": password, "auto_connect": auto})


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
        super().__init__(app, 440, 540)

        self._on_save = on_save
        self._entry_id = entry.id
        self._recording_in_dialog = False
        self._definitions = list(tag_definitions)
        self._selected_tags: set[str] = {tag.lower() for tag in entry.tags}

        header = ctk.CTkFrame(self.card, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(16, 10))
        ctk.CTkLabel(
            header,
            text=(
                f"Timestamp {format_entry_ref(entry)} — "
                f"{format_elapsed_display(entry.elapsed_seconds)}"
            ),
            font=Theme.FONT_SUBTITLE,
            text_color=Theme.TEXT_BRIGHT,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        has_screenshot = bool(entry.screenshot_file) and entry.kind != "replay"
        self.screenshot_button = ctk.CTkButton(
            header,
            text="\U0001F4F7 Screenshot",
            width=118,
            height=22,
            font=Theme.FONT_SMALL,
            fg_color=Theme.BTN_SURFACE,
            hover_color=Theme.BLUE_HOVER,
            command=lambda: self.app._open_screenshot(self._entry_id),
            state="normal" if has_screenshot else "disabled",
        )
        self.screenshot_button.pack(side="right")

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
        self.label_entry.pack(fill="x", padx=18, pady=(4, 6))
        ctk.CTkLabel(self.card, text="Time (HH:MM:SS, MM:SS, or SS)", font=Theme.FONT_SMALL, text_color=Theme.TEXT_DIM, anchor="w").pack(fill="x", padx=18, pady=(4, 0))
        self.time_entry = ctk.CTkEntry(self.card, font=Theme.FONT_BODY, placeholder_text=format_elapsed_display(entry.elapsed_seconds))
        self.time_entry.insert(0, format_elapsed_display(entry.elapsed_seconds))
        self.time_entry.pack(fill="x", padx=18, pady=(2, 0))
        self.time_feedback = ctk.CTkLabel(self.card, text="", font=Theme.FONT_SMALL, text_color=Theme.RED, anchor="w")
        self.time_feedback.pack(fill="x", padx=18, pady=(0, 4))
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
            self._new_tag_button = ctk.CTkButton(
                tags_header,
                text="＋ New tag",
                width=92,
                height=22,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BTN_SURFACE,
                hover_color=Theme.BTN_SURFACE_HOVER,
                command=on_manage_tags,
            )
            self._new_tag_button.pack(side="right")
        else:
            self._new_tag_button = None
        self._chips_frame = ctk.CTkFrame(self.card, fg_color="transparent")
        self._chips_frame.pack(fill="x", padx=14, pady=(2, 6))
        self._build_chips()

        # ── Audio takes ───────────────────────────────────────────────────
        takes_header = ctk.CTkFrame(self.card, fg_color="transparent")
        takes_header.pack(fill="x", padx=18, pady=(2, 0))
        ctk.CTkLabel(
            takes_header,
            text="Audio takes",
            font=Theme.FONT_SMALL,
            text_color=Theme.TEXT_DIM,
            anchor="w",
        ).pack(side="left")
        self.rerecord_button = ctk.CTkButton(
            takes_header,
            text="🎙 Re-record",
            width=104,
            height=22,
            font=Theme.FONT_SMALL,
            fg_color=Theme.BTN_SURFACE,
            hover_color=Theme.BTN_SURFACE_HOVER,
            command=self._toggle_rerecord,
        )
        self.rerecord_button.pack(side="right")
        buttons = ctk.CTkFrame(self.card, fg_color="transparent")
        buttons.pack(side="bottom", fill="x", padx=18, pady=(8, 12))
        buttons.grid_columnconfigure(0, weight=1)
        self.save_button = ctk.CTkButton(
            buttons,
            text="Save",
            width=100,
            font=Theme.FONT_BUTTON,
            fg_color=Theme.GREEN,
            hover_color=Theme.GREEN_HOVER,
            text_color="#000000",
            command=self._save,
        )
        self.save_button.grid(row=0, column=1, padx=(8, 0))
        self.cancel_button = ctk.CTkButton(
            buttons,
            text="Cancel",
            width=100,
            font=Theme.FONT_BUTTON,
            fg_color=Theme.GREY,
            hover_color=Theme.GREY_HOVER,
            command=self._cancel,
        )
        self.cancel_button.grid(row=0, column=0, sticky="e")

        self._takes_frame = ctk.CTkScrollableFrame(
            self.card, height=110, fg_color="transparent"
        )
        self._takes_frame.pack(fill="both", expand=True, padx=14, pady=(4, 8))
        self._build_takes_rows()

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

    def refresh_screenshot_state(self) -> None:
        """Enable the header screenshot button once the async capture lands."""
        try:
            entry = self._current_entry()
            has_shot = bool(entry and entry.screenshot_file and entry.kind != "replay")
            self.screenshot_button.configure(state="normal" if has_shot else "disabled")
        except (tk.TclError, AttributeError):
            pass

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

    # ── Audio takes ──────────────────────────────────────────────────────

    def _current_entry(self) -> TimestampEntry | None:
        session = getattr(self.app, "session", None)
        return session.get(self._entry_id) if session else None

    def _take_list(self) -> list[dict]:
        """Takes of the entry; pre-takes entries synthesize their single one."""
        entry = self._current_entry()
        if entry is None:
            return []
        if entry.takes:
            return list(entry.takes)
        if entry.audio_file:
            return [
                {
                    "file": entry.audio_file,
                    "duration_seconds": entry.duration_seconds,
                    "created_at": "",
                }
            ]
        return []

    def _build_takes_rows(self) -> None:
        for child in self._takes_frame.winfo_children():
            child.destroy()
        takes = self._take_list()
        if not takes:
            ctk.CTkLabel(
                self._takes_frame,
                text="No audio yet — click the timestamp row to record the first take.",
                font=Theme.FONT_SMALL,
                text_color=Theme.TEXT_DIM,
                anchor="w",
            ).pack(fill="x", padx=4, pady=6)
            return
        session = getattr(self.app, "session", None)
        output_dir = session.output_dir if session else ""
        playing_path = (
            os.path.abspath(self.app.playback.current_path)
            if self.app.playback.current_path
            else None
        )
        entry = self._current_entry()
        active_file = (entry.audio_file if entry else None) or ""
        # Newest take first.
        for index in range(len(takes) - 1, -1, -1):
            take = takes[index]
            relative = str(take.get("file") or "")
            duration = take.get("duration_seconds")
            row = ctk.CTkFrame(
                self._takes_frame, fg_color=Theme.BTN_SURFACE, corner_radius=6
            )
            row.pack(fill="x", padx=2, pady=3)
            is_active = relative == active_file
            marker = "● " if is_active else ""
            ctk.CTkLabel(
                row,
                text=f"{marker}Take {index + 1}",
                font=Theme.FONT_SMALL,
                text_color=Theme.GREEN if is_active else Theme.TEXT_BRIGHT,
                width=78,
                anchor="w",
            ).pack(side="left", padx=(8, 0))
            length = f"{duration:.1f}s" if duration else "—"
            ctk.CTkLabel(
                row,
                text=length,
                font=Theme.FONT_SMALL,
                text_color=Theme.TEXT_DIM,
                width=46,
                anchor="w",
            ).pack(side="left")
            actions = ctk.CTkFrame(row, fg_color="transparent")
            actions.pack(side="right", padx=6)
            play_icon = "▶"
            if playing_path and output_dir:
                try:
                    full = os.path.abspath(os.path.join(output_dir, relative))
                    if full == playing_path:
                        play_icon = "■"
                except OSError:
                    pass
            ctk.CTkButton(
                actions,
                text=play_icon,
                width=32,
                height=22,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BTN_SURFACE,
                hover_color=Theme.BTN_SURFACE_HOVER,
                command=lambda i=index: self.app._toggle_take_playback(
                    self._entry_id, i, self.refresh_takes
                ),
            ).pack(side="left", padx=2)
            if not is_active:
                ctk.CTkButton(
                    actions,
                    text="★ Use",
                    width=52,
                    height=22,
                    font=Theme.FONT_SMALL,
                    fg_color=Theme.BTN_SURFACE,
                    hover_color=Theme.BTN_SURFACE_HOVER,
                    text_color=Theme.TEXT_BRIGHT,
                    command=lambda i=index: self.app._dialog_set_active_take(
                        self._entry_id, i, self
                    ),
                ).pack(side="left", padx=2)
            ctk.CTkButton(
                actions,
                text="🗑",
                width=32,
                height=22,
                font=Theme.FONT_SMALL,
                fg_color=Theme.BTN_SURFACE,
                hover_color=Theme.RED_HOVER,
                text_color=Theme.TEXT_BRIGHT,
                command=lambda i=index: self.app._dialog_delete_take(
                    self._entry_id, i, self
                ),
            ).pack(side="left", padx=(2, 0))

    def refresh_takes(self, _entry=None) -> None:
        """Rebuild the take rows after a record/delete/switch."""
        try:
            self._build_takes_rows()
        except tk.TclError:
            pass

    def enter_recording_mode(self) -> None:
        """Lock the form while an in-dialog re-record captures audio."""
        self._recording_in_dialog = True
        self._set_form_locked(True)

    def exit_recording_mode(self) -> None:
        self._recording_in_dialog = False
        self._set_form_locked(False)

    def _set_form_locked(self, locked: bool) -> None:
        state = "disabled" if locked else "normal"
        for widget in (
            self.label_entry,
            self.time_entry,
            self.save_button,
            self.cancel_button,
            self._new_tag_button,
        ):
            if widget is None:
                continue
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        for chip in self._chips_frame.winfo_children():
            try:
                chip.configure(state=state)
            except tk.TclError:
                pass
        try:
            if locked:
                self.rerecord_button.configure(
                    text="■ Stop",
                    fg_color=Theme.RED,
                    hover_color=Theme.RED_HOVER,
                )
            else:
                self.rerecord_button.configure(
                    text="🎙 Re-record",
                    fg_color=Theme.BTN_SURFACE,
                    hover_color=Theme.BTN_SURFACE_HOVER,
                )
        except tk.TclError:
            pass

    def _toggle_rerecord(self) -> None:
        if self._recording_in_dialog:
            self.app._dialog_rerecord_stop(self._entry_id, self)
        else:
            self.app._dialog_rerecord_start(self._entry_id, self)

    def _save(self) -> None:
        time_text = self.time_entry.get().strip()
        seconds = parse_time_input(time_text) if time_text else None
        if time_text and seconds is None:
            self.time_feedback.configure(text="Enter time as SS, MM:SS, or HH:MM:SS.")
            return
        label = self.label_entry.get()
        tags = self._selected_tag_names()
        on_save = self._on_save
        pending_time = seconds
        self.destroy()
        on_save(label, tags, pending_time)

    def _cancel(self) -> None:
        # Closing mid-re-record is allowed on purpose: the entry stays in its
        # recording state, so clicking the timestamp row stops the capture.
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
