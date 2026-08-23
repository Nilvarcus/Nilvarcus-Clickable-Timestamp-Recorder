"""Core audio timestamp functionality for the focused MVP.

The module intentionally keeps GUI code out of the session and audio services so
that timestamp persistence and state transitions can be tested without Tkinter.
"""

from __future__ import annotations

import json
import os
import re
import struct
import sys
import subprocess
import threading
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, Optional


SESSION_FILENAME = "session.json"
SESSION_FORMAT_VERSION = 6
SCREENSHOTS_DIRNAME = "Screenshots"
RECENT_PROJECTS_LIMIT = 5
LABEL_MAX_LENGTH = 200
TRANSCRIPT_MAX_LENGTH = 10000
TAG_NAME_MAX_LENGTH = 24
DEFAULT_TAG_COLOR = "#616161"
AUDIO_SAMPLE_RATE = 44_100
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_WIDTH = 2  # signed 16-bit PCM
# Silence detection defaults: int16 RMS below ``threshold`` counts as
# "no voice"; a recording that stays voiceless for ``timeout`` seconds is
# auto-stopped and discarded. Both are overridable in keybinds.json.
MIC_SILENCE_THRESHOLD_DEFAULT = 250.0
MIC_SILENCE_TIMEOUT_DEFAULT = 5.0
# Meter full scale: RMS mapped to this many int16 counts reads as 1.0.
MIC_LEVEL_CLAMP_RMS = 5000.0


def format_elapsed(seconds: float) -> str:
    """Return elapsed seconds as a filesystem-safe HH-MM-SS string."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}-{minutes:02d}-{secs:02d}"


def format_elapsed_display(seconds: float) -> str:
    """Return elapsed seconds as a human-readable HH:MM:SS string."""
    return format_elapsed(seconds).replace("-", ":")


def sanitize_project_name(name: str) -> str:
    """Make a project name safe for a Windows folder and Markdown filename."""
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", name.strip())
    cleaned = cleaned.rstrip(" .")
    return cleaned or "Project"


def clean_label(label: Optional[str]) -> Optional[str]:
    """Normalize a timestamp label to a single short line without quotes."""
    if label is None:
        return None
    flattened = " ".join(str(label).split())
    cleaned = flattened.replace('"', "'").strip()
    cleaned = cleaned[:LABEL_MAX_LENGTH].strip()
    return cleaned or None


def clean_transcript(transcript: Optional[str]) -> Optional[str]:
    """Normalize a transcript: preserve paragraph breaks, strip, cap length."""
    if transcript is None:
        return None
    text = str(transcript).replace("\r\n", "\n").replace("\r", "\n")
    # Strip each line, drop leading/trailing blank lines, collapse 3+ breaks to 2.
    lines = [line.strip() for line in text.split("\n")]
    # Remove leading/trailing empty lines
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    # Collapse consecutive blank lines to at most one
    collapsed: list[str] = []
    prev_blank = False
    for line in lines:
        is_blank = not line
        if is_blank and prev_blank:
            continue
        collapsed.append(line)
        prev_blank = is_blank
    cleaned = "\n".join(collapsed).strip()
    if not cleaned:
        return None
    if len(cleaned) > TRANSCRIPT_MAX_LENGTH:
        cleaned = cleaned[:TRANSCRIPT_MAX_LENGTH].rstrip() + " …"
    return cleaned


def clean_tags(tags: Optional[list]) -> list[str]:
    """Normalize tag names: stripped, non-empty, de-duplicated, order kept."""
    if not tags:
        return []
    cleaned: list[str] = []
    for tag in tags:
        name = normalize_tag_name(tag)
        if name and name not in cleaned:
            cleaned.append(name)
    return cleaned


def normalize_tag_name(name) -> str:
    """Normalize one tag name: stringified, trimmed, leading '#' stripped."""
    return str(name or "").strip().lstrip("#").strip()


def is_valid_hex_color(value) -> bool:
    """True when value is a ``#RRGGBB`` hex color string."""
    if not isinstance(value, str) or not value.startswith("#") or len(value) != 7:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value[1:])


def sanitize_tag_definitions(
    raw,
    defaults: list[dict],
    fallback_color: str = DEFAULT_TAG_COLOR,
) -> list[dict]:
    """Validate a stored tag-library list into ``{"name", "color"}`` dicts.

    Names are normalized like entry tags (stripped, leading ``#`` removed)
    and must be non-empty; duplicates compare case-insensitively. Colors must
    be ``#RRGGBB`` hex strings or they fall back to ``fallback_color``. When
    ``raw`` is not a list or nothing usable survives, a copy of ``defaults``
    is returned so callers always have a usable library.
    """
    safe_defaults = [dict(tag) for tag in defaults if isinstance(tag, dict)]
    if not isinstance(raw, list):
        return safe_defaults
    definitions: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = normalize_tag_name(item.get("name", ""))
        color = str(item.get("color", fallback_color)).strip()
        if not name or name.lower() in seen:
            continue
        if not is_valid_hex_color(color):
            color = fallback_color
        seen.add(name.lower())
        definitions.append({"name": name, "color": color})
    return definitions or safe_defaults


def replay_display_name(replay_path: Optional[str]) -> str:
    """Display name for an OBS replay file: its filename without extension.

    "E:/vids/replay- [21-08][17-44-10].mp4" becomes
    "replay- [21-08][17-44-10]"; anything unusable yields a generic label.
    """
    if not replay_path:
        return "Unknown replay"
    stem = os.path.splitext(os.path.basename(str(replay_path).strip()))[0].strip()
    return stem or "Unknown replay"


def replay_file_uri(replay_path: Optional[str]) -> Optional[str]:
    """Return an absolute file:/// URI for a replay video, or None.

    Used for Markdown links to footage that lives outside the project folder.
    pathlib handles Windows drive letters and percent-escaping of spaces.
    """
    if not replay_path:
        return None
    try:
        return Path(os.path.abspath(str(replay_path))).as_uri()
    except (ValueError, OSError):
        return None


def parse_time_input(text: str) -> Optional[float]:
    """Parse SS, MM:SS, or HH:MM:SS text into seconds; None when invalid."""
    parts = str(text).strip().split(":")
    if not 1 <= len(parts) <= 3:
        return None
    if any(not part.isdigit() for part in parts):
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def sanitize_recent_projects(recents, limit: int = RECENT_PROJECTS_LIMIT) -> list[dict]:
    """Validate a stored recent-projects list: dicts only, trimmed to limit."""
    cleaned: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in recents or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        raw_folder = item.get("output_folder")
        if not name or not isinstance(raw_folder, str) or not raw_folder.strip():
            continue
        folder = os.path.abspath(os.path.expanduser(raw_folder.strip()))
        key = (name.casefold(), os.path.normcase(folder))
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"name": name, "output_folder": folder})
        if len(cleaned) >= limit:
            break
    return cleaned


def update_recent_projects(
    recents,
    project_name: str,
    output_folder: str,
    limit: int = RECENT_PROJECTS_LIMIT,
) -> list[dict]:
    """Return recents with this project moved/inserted at the front.

    Matching compares names case-insensitively and folders as normalized
    absolute paths, so the same project recorded twice never duplicates.
    Malformed stored entries are dropped; the result is capped at limit with
    the oldest entries falling off first.
    """
    cleaned = sanitize_recent_projects(recents, limit=limit)
    name = str(project_name).strip()
    if not name or not str(output_folder).strip():
        return cleaned
    folder = os.path.abspath(os.path.expanduser(str(output_folder).strip()))
    key = (name.casefold(), os.path.normcase(folder))
    remaining = [
        entry
        for entry in cleaned
        if (entry["name"].casefold(), os.path.normcase(entry["output_folder"])) != key
    ]
    updated = [{"name": name, "output_folder": folder}] + remaining
    return updated[:limit]


def remove_recent_project(recents, project_name: str, output_folder: str) -> list[dict]:
    """Return recents without the entry matching this name + folder.

    Matching mirrors update_recent_projects: names case-insensitively and
    folders as normalized absolute paths. Unknown entries leave the list
    unchanged; malformed entries are still sanitized away.
    """
    cleaned = sanitize_recent_projects(recents)
    name = str(project_name).strip()
    folder = str(output_folder).strip()
    if not name or not folder:
        return cleaned
    key = (name.casefold(), os.path.normcase(os.path.abspath(os.path.expanduser(folder))))
    return [
        entry
        for entry in cleaned
        if (entry["name"].casefold(), os.path.normcase(entry["output_folder"])) != key
    ]


def read_project_stats(output_folder) -> dict:
    """Count a stored project's activity from its session.json.

    Returns {"timestamps": int, "recordings": int} where timestamps counts
    clickable entries and recordings counts OBS/timer segments. Missing,
    corrupt, or malformed data yields zeros so the recent-projects popup can
    always render; the file is only read, never written.
    """
    stats = {"timestamps": 0, "recordings": 0}
    folder = str(output_folder or "").strip()
    if not folder:
        return stats
    metadata_path = os.path.join(os.path.expanduser(folder), SESSION_FILENAME)
    try:
        with open(metadata_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return stats
    if not isinstance(payload, dict):
        return stats
    for key, target in (("entries", "timestamps"), ("recordings", "recordings")):
        raw = payload.get(key)
        if isinstance(raw, list):
            stats[target] = len(raw)
    return stats


@dataclass
class RecordingInfo:
    """One OBS recording segment inside a project.

    A segment opens when OBS starts recording and closes when it stops. Its
    name is derived from the recording file name OBS reports (for example
    "[21-08][14-55-19]"), or falls back to a generic label while unknown.
    ``path`` keeps the absolute recording video path OBS reported so the GUI
    can open the file; segments recorded without OBS (in-app timer toggle)
    or by older app versions stay without one.
    """

    number: int
    name: Optional[str]
    started_at: float
    ended_at: Optional[float] = None
    path: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "RecordingInfo":
        raw_name = data.get("name")
        raw_ended = data.get("ended_at")
        raw_path = data.get("path")
        return cls(
            number=int(data["number"]),
            name=str(raw_name) if raw_name else None,
            started_at=float(data["started_at"]),
            ended_at=float(raw_ended) if raw_ended is not None else None,
            path=str(raw_path) if raw_path else None,
        )

    def header(self) -> str:
        """Display/Markdown header for this segment."""
        return self.name or f"Recording {self.number}"

    def duration_seconds(self) -> Optional[float]:
        """Total recording length in seconds, or None while still open."""
        if self.ended_at is None:
            return None
        return max(0.0, self.ended_at - self.started_at)


@dataclass
class TimestampEntry:
    """One clickable log entry and the audio note associated with it.

    ``kind`` distinguishes regular timestamps from automatic replay-buffer
    entries; replay entries additionally carry ``replay_file``, the absolute
    path of the OBS-saved replay video they were created from.
    """

    id: int
    elapsed_seconds: float
    created_at: str
    status: str = "pending"
    kind: str = "timestamp"
    audio_file: Optional[str] = None
    duration_seconds: Optional[float] = None
    screenshot_file: Optional[str] = None
    error: Optional[str] = None
    label: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    recording_number: Optional[int] = None
    recording_index: Optional[int] = None
    transcript: Optional[str] = None
    replay_file: Optional[str] = None
    # Audio retakes, oldest first. Each item is {file, duration_seconds,
    # created_at}; ``audio_file``/``duration_seconds`` above always point at
    # the ACTIVE take (the newest one unless set_active_take chose another).
    takes: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "TimestampEntry":
        raw_tags = data.get("tags")
        tags = [str(tag) for tag in raw_tags] if isinstance(raw_tags, list) else []
        recording_number = data.get("recording_number")
        recording_index = data.get("recording_index")
        # Files written before replays existed have no kind field; treat any
        # unknown value as a plain timestamp so old logs always load.
        kind = str(data.get("kind", "timestamp"))
        if kind not in ("timestamp", "replay"):
            kind = "timestamp"
        return cls(
            id=int(data["id"]),
            elapsed_seconds=float(data["elapsed_seconds"]),
            created_at=str(data.get("created_at", "")),
            status=str(data.get("status", "pending")),
            kind=kind,
            audio_file=data.get("audio_file"),
            duration_seconds=(
                float(data["duration_seconds"])
                if data.get("duration_seconds") is not None
                else None
            ),
            screenshot_file=data.get("screenshot_file"),
            error=data.get("error"),
            label=data.get("label"),
            tags=tags,
            recording_number=(
                int(recording_number) if recording_number is not None else None
            ),
            recording_index=(
                int(recording_index) if recording_index is not None else None
            ),
            transcript=clean_transcript(data.get("transcript")),
            replay_file=data.get("replay_file"),
            takes=cls._takes_from_dict(data),
        )

    @staticmethod
    def _takes_from_dict(data: dict) -> list[dict]:
        """Parse persisted retakes tolerantly.

        Session files written before takes existed have no ``takes`` key;
        entries that already carry audio get one synthesized take so every
        loaded entry exposes a consistent take list (and re-saving migrates
        old files naturally).
        """
        raw = data.get("takes")
        takes: list[dict] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                file_value = item.get("file")
                if not file_value:
                    continue
                duration = item.get("duration_seconds")
                created_at = item.get("created_at")
                takes.append(
                    {
                        "file": str(file_value),
                        "duration_seconds": (
                            float(duration) if duration is not None else None
                        ),
                        "created_at": str(created_at) if created_at else "",
                    }
                )
        audio_file = data.get("audio_file")
        if not takes and audio_file:
            duration = data.get("duration_seconds")
            takes.append(
                {
                    "file": str(audio_file),
                    "duration_seconds": (
                        float(duration) if duration is not None else None
                    ),
                    "created_at": str(data.get("created_at") or ""),
                }
            )
        return takes

    def annotation_suffix(self) -> str:
        """Return the ' — "label" #tag' Markdown suffix, empty when unannotated."""
        parts = []
        if self.label:
            parts.append(f'"{self.label}"')
        parts.extend(f"#{tag}" for tag in self.tags)
        return f" — {' '.join(parts)}" if parts else ""


class TimestampSession:
    """Persisted collection of timestamp entries for one output folder."""

    def __init__(
        self,
        output_dir: str,
        project_name: str = "",
        load_existing: bool = True,
    ):
        self.output_dir = os.path.abspath(os.path.expanduser(output_dir))
        self.metadata_path = os.path.join(self.output_dir, SESSION_FILENAME)
        self.project_name = project_name.strip()
        self.started_at = time.time()
        self.timer_running = False
        self.timer_paused = False
        self._pause_started_at: Optional[float] = None
        self.entries: list[TimestampEntry] = []
        self.next_id = 1
        self.recordings: list[RecordingInfo] = []
        self.next_recording_number = 1
        self.current_recording_number: Optional[int] = None

        if load_existing:
            self.load()

    def elapsed_seconds(self) -> float:
        if not self.timer_running:
            return 0.0
        if self.timer_paused and self._pause_started_at is not None:
            return max(0.0, self._pause_started_at - self.started_at)
        return max(0.0, time.time() - self.started_at)

    @property
    def current_recording(self) -> Optional[RecordingInfo]:
        if self.current_recording_number is None:
            return None
        return self._get_recording(self.current_recording_number)

    def _get_recording(self, number: int) -> Optional[RecordingInfo]:
        return next(
            (recording for recording in self.recordings if recording.number == number),
            None,
        )

    def start_timer(self, recording_name: Optional[str] = None) -> None:
        """Start a fresh elapsed timer and segment for the current OBS recording."""
        if not self.timer_running:
            self.started_at = time.time()
            self.timer_running = True
            self.timer_paused = False
            self._pause_started_at = None
            recording = RecordingInfo(
                number=self.next_recording_number,
                name=recording_name or None,
                started_at=self.started_at,
            )
            self.recordings.append(recording)
            self.current_recording_number = recording.number
            self.next_recording_number += 1
            self.save()

    def pause_timer(self) -> None:
        """Pause the manual timer; timestamp offsets freeze until resume."""
        if self.timer_running and not self.timer_paused:
            self.timer_paused = True
            self._pause_started_at = time.time()
            self.save()

    def resume_timer(self) -> None:
        """Resume a paused manual timer, shifting the epoch by the paused duration."""
        if self.timer_running and self.timer_paused and self._pause_started_at is not None:
            paused_duration = time.time() - self._pause_started_at
            self.started_at += paused_duration
            self.timer_paused = False
            self._pause_started_at = None
            self.save()

    def stop_timer(self) -> None:
        """Lock timestamp creation until the next OBS recording starts."""
        if self.timer_running:
            self.timer_running = False
            self.timer_paused = False
            self._pause_started_at = None
            current = self.current_recording
            if current is not None and current.ended_at is None:
                current.ended_at = time.time()
            self.current_recording_number = None
            self.save()

    def name_recording(self, number: int, name: str) -> None:
        """Backfill a segment's OBS file name once it becomes known."""
        recording = self._get_recording(number)
        cleaned = (name or "").strip()
        if recording is None or not cleaned or recording.name == cleaned:
            return
        recording.name = cleaned
        self.save()

    def set_recording_path(self, number: int, path: str) -> None:
        """Backfill a segment's OBS recording video path once it is known.

        The start and stop record events both carry the output path; the
        later event confirms (or corrects) the stored value. Unknown segment
        numbers and empty paths are safe no-ops.
        """
        recording = self._get_recording(number)
        cleaned = (path or "").strip()
        if recording is None or not cleaned or recording.path == cleaned:
            return
        recording.path = cleaned
        self.save()

    def _attach_to_segment(self, entry: TimestampEntry) -> None:
        """Assign a recording segment and per-segment index to a new entry."""
        if self.timer_running and self.current_recording_number is not None:
            entry.recording_number = self.current_recording_number
        elif self.recordings:
            # Entries created while locked attach to the most recent segment;
            # before any recording they stay ungrouped.
            entry.recording_number = self.recordings[-1].number
        if entry.recording_number is not None:
            entry.recording_index = (
                sum(
                    1
                    for existing in self.entries
                    if existing.recording_number == entry.recording_number
                )
                + 1
            )

    def create_timestamp(
        self,
        elapsed_seconds: Optional[float] = None,
        label: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> TimestampEntry:
        entry = TimestampEntry(
            id=self.next_id,
            elapsed_seconds=(
                self.elapsed_seconds() if elapsed_seconds is None else float(elapsed_seconds)
            ),
            created_at=datetime.now(timezone.utc).isoformat(),
            label=clean_label(label),
            tags=clean_tags(tags),
        )
        self._attach_to_segment(entry)
        self.entries.append(entry)
        self.next_id += 1
        self.save()
        return entry

    def create_replay_entry(self, replay_path: str) -> TimestampEntry:
        """Log one OBS replay-buffer save as its own clickable entry.

        The entry is tagged with the OBS replay file's name and links to its
        video. Segment attachment mirrors create_timestamp: the live segment
        while the timer runs, otherwise the most recent one (or ungrouped
        before any recording), so replays saved outside an active timer are
        still captured. Saving the same replay file twice never duplicates.
        """
        cleaned_path = str(replay_path or "").strip()
        if not cleaned_path:
            raise ValueError("Replay file path is required")
        absolute = os.path.abspath(cleaned_path)
        key = os.path.normcase(absolute)
        for existing in self.entries:
            if (
                existing.kind == "replay"
                and existing.replay_file
                and os.path.normcase(os.path.abspath(existing.replay_file)) == key
            ):
                return existing

        entry = TimestampEntry(
            id=self.next_id,
            elapsed_seconds=self.elapsed_seconds(),
            created_at=datetime.now(timezone.utc).isoformat(),
            kind="replay",
            replay_file=absolute,
        )
        self._attach_to_segment(entry)
        self.entries.append(entry)
        self.next_id += 1
        self.save()
        return entry

    def get(self, entry_id: int) -> Optional[TimestampEntry]:
        return next((entry for entry in self.entries if entry.id == entry_id), None)

    def _entry_stem(self, entry: TimestampEntry) -> str:
        """Filesystem stem shared by a timestamp's WAV and screenshot files."""
        if entry.recording_number is not None and entry.recording_index is not None:
            return (
                f"R{entry.recording_number:02d}-{entry.recording_index:03d}"
                f"_{format_elapsed(entry.elapsed_seconds)}"
            )
        return f"{entry.id:03d}_{format_elapsed(entry.elapsed_seconds)}"

    @staticmethod
    def _unique_path(candidate: str, suffix: int = 2) -> str:
        """Return candidate, or candidate with an appended _N suffix."""
        base_root, base_ext = os.path.splitext(candidate)
        while os.path.exists(candidate):
            candidate = f"{base_root}_{suffix}{base_ext}"
            suffix += 1
        return candidate

    def audio_path(self, entry: TimestampEntry) -> str:
        """Return a unique, timestamp-based WAV path without overwriting audio."""
        return self._unique_path(
            os.path.join(self.output_dir, f"{self._entry_stem(entry)}.wav")
        )

    def screenshot_path(self, entry: TimestampEntry) -> str:
        """Return a unique JPEG path inside the project's Screenshots folder."""
        folder = os.path.join(self.output_dir, SCREENSHOTS_DIRNAME)
        return self._unique_path(
            os.path.join(folder, f"{self._entry_stem(entry)}.jpg")
        )

    def mark_recording(self, entry: TimestampEntry) -> None:
        entry.status = "recording"
        entry.error = None
        self.save()

    def mark_completed(
        self, entry: TimestampEntry, audio_path: str, duration: float
    ) -> None:
        """Finish a recording; the saved file is registered as the active take.

        Works for both first recordings and re-records: each completed capture
        is appended to ``takes`` (deduplicated by path) and becomes the entry's
        active audio, leaving earlier takes playable via the edit dialog.
        """
        relative = os.path.relpath(audio_path, self.output_dir)
        entry.status = "completed"
        entry.audio_file = relative
        entry.duration_seconds = max(0.0, float(duration))
        entry.error = None
        registered = False
        for take in entry.takes:
            if take.get("file") == relative:
                take["duration_seconds"] = entry.duration_seconds
                registered = True
                break
        if not registered:
            entry.takes.append(
                {
                    "file": relative,
                    "duration_seconds": entry.duration_seconds,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        self.save()

    def mark_error(self, entry: TimestampEntry, error: str) -> None:
        entry.status = "error"
        entry.error = error
        self.save()

    def reset_for_retry(self, entry: TimestampEntry) -> None:
        """Make an aborted capture clickable again.

        An entry that already owns takes (a re-record or quick-take attempt
        on a completed timestamp) returns to ``completed`` instead of
        regressing to pending: ``mark_recording`` never touches the active
        take fields, so the previous audio simply stays valid. Entries
        without takes go back to ``pending`` as before.
        """
        entry.status = "completed" if entry.takes else "pending"
        entry.error = None
        self.save()

    def _take_at(self, entry: TimestampEntry, index: int) -> dict:
        if not 0 <= index < len(entry.takes):
            raise IndexError(f"Take {index + 1} does not exist")
        return entry.takes[index]

    def _unlink_project_file(self, relative: str) -> Optional[str]:
        """Best-effort delete of an output-dir-relative media file.

        Returns a human-readable problem string when the file had to stay on
        disk (outside the project folder, locked); None when it was deleted
        or was already gone. Hand-edited session.json must not make us unlink
        arbitrary files outside the project folder.
        """
        base = os.path.normcase(os.path.abspath(self.output_dir)) + os.sep
        path = os.path.abspath(os.path.join(self.output_dir, relative))
        if not os.path.normcase(path).startswith(base):
            return f"{relative}: outside the project folder, left untouched"
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            return f"{relative}: {exc}"
        return None

    def _recycle_project_file(self, relative: str) -> Optional[str]:
        """Move an output-dir-relative media file to the Recycle Bin.

        Returns a problem string when the file could not be recycled
        (outside project, locked, missing send2trash); None when recycled
        or already gone. Falls back to os.remove when send2trash is absent.
        """
        base = os.path.normcase(os.path.abspath(self.output_dir)) + os.sep
        path = os.path.abspath(os.path.join(self.output_dir, relative))
        if not os.path.normcase(path).startswith(base):
            return f"{relative}: outside the project folder, left untouched"
        if not os.path.exists(path):
            return None
        try:
            from send2trash import send2trash
            send2trash(path)
        except ImportError:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                return f"{relative}: {exc}"
        except OSError as exc:
            return f"{relative}: {exc}"
        except Exception as exc:
            return f"{relative}: {exc}"
        return None

    def set_active_take(self, entry: TimestampEntry, index: int) -> dict:
        """Make ``takes[index]`` the entry's active audio take."""
        take = self._take_at(entry, index)
        path = os.path.join(self.output_dir, str(take["file"]))
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        entry.audio_file = str(take["file"])
        duration = take.get("duration_seconds")
        entry.duration_seconds = float(duration) if duration is not None else None
        self.save()
        return take

    def remove_take(self, entry: TimestampEntry, index: int) -> dict:
        """Delete ``takes[index]`` from disk and from the entry.

        Removing the active take promotes the newest remaining one. Removing
        the last take clears the audio pointers and returns the entry to
        ``pending`` so it can be recorded again like a fresh timestamp.
        """
        if entry.status == "recording":
            raise ValueError("Cannot delete a take while it is recording")
        take = self._take_at(entry, index)
        was_active = take.get("file") == entry.audio_file
        self._unlink_project_file(str(take["file"]))
        entry.takes.remove(take)
        if was_active:
            if entry.takes:
                newest = entry.takes[-1]
                entry.audio_file = str(newest["file"])
                duration = newest.get("duration_seconds")
                entry.duration_seconds = (
                    float(duration) if duration is not None else None
                )
            else:
                entry.audio_file = None
                entry.duration_seconds = None
                entry.status = "pending"
        self.save()
        return take

    def update_entry(
        self,
        entry_id: int,
        label: Optional[str],
        tags: Optional[list[str]] = None,
    ) -> TimestampEntry:
        """Set an entry's label and tags, then persist. Recording entries are locked."""
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        if entry.status == "recording":
            raise ValueError("Cannot edit a timestamp while it is recording")
        entry.label = clean_label(label)
        entry.tags = clean_tags(tags)
        self.save()
        return entry

    def update_transcript(
        self,
        entry_id: int,
        transcript: Optional[str],
    ) -> TimestampEntry:
        """Set an entry's transcript, then persist. Recording entries are locked."""
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        if entry.status == "recording":
            raise ValueError("Cannot edit a timestamp while it is recording")
        entry.transcript = clean_transcript(transcript)
        self.save()
        return entry

    def remove_entry(self, entry_id: int) -> tuple[TimestampEntry, list[str]]:
        """Remove an entry from the log and recycle its media files.

        The entry's WAV notes (all takes) and screenshot JPEG are moved to
        the Recycle Bin when they exist; the linked OBS replay video
        (``replay_file``) is never touched. Files are recycled first — if
        any file cannot be recycled (locked, permission error) the entry
        remains and the caller receives the problems so the user can retry
        after freeing the file. Missing files are not reported.

        Returns ``(entry, problems)`` where ``problems`` holds one
        human-readable string per file that could not be recycled. When
        problems is non-empty the entry was NOT removed.
        """
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        if entry.status == "recording":
            raise ValueError("Cannot delete a timestamp while it is recording")
        relatives = [entry.audio_file, entry.screenshot_file]
        relatives.extend(str(take.get("file")) for take in entry.takes)
        problems: list[str] = []
        blocking: list[str] = []
        for relative in dict.fromkeys(r for r in relatives if r):
            problem = self._recycle_project_file(relative)
            if problem:
                problems.append(problem)
                if "outside the project folder" not in problem:
                    blocking.append(problem)
        if blocking:
            return entry, problems
        self.entries.remove(entry)
        self.save()
        return entry, problems

    def update_entry_time(self, entry_id: int, elapsed_seconds: float) -> TimestampEntry:
        """Set an entry's timestamp offset, then persist.

        Validates the new offset is non-negative; recording entries remain
        locked. The entry stays in its current segment — only the displayed
        time changes.
        """
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        if entry.status == "recording":
            raise ValueError("Cannot edit a timestamp while it is recording")
        seconds = float(elapsed_seconds)
        if seconds < 0:
            raise ValueError("Timestamp time cannot be negative")
        entry.elapsed_seconds = seconds
        self.save()
        return entry

    def count_entries_with_tag(self, name: str) -> int:
        """Number of entries currently carrying this tag (case-insensitive)."""
        key = normalize_tag_name(name).lower()
        if not key:
            return 0
        return sum(
            1 for entry in self.entries if any(tag.lower() == key for tag in entry.tags)
        )

    def rename_tag(self, old_name: str, new_name: str) -> int:
        """Rename a tag across every entry (case-insensitive), then persist.

        Tag propagation deliberately bypasses the recording-row edit lock —
        labels are never touched, only the tag list is rewritten. Duplicates
        created by the rewrite (tag matching is case-insensitive) collapse,
        keeping first occurrences. Returns the number of entries changed.
        """
        old_key = normalize_tag_name(old_name).lower()
        new_tag = normalize_tag_name(new_name)
        if not old_key or not new_tag:
            raise ValueError("Both the old and new tag name are required")
        changed = 0
        for entry in self.entries:
            rewritten: list[str] = []
            seen: set[str] = set()
            entry_changed = False
            for tag in entry.tags:
                replacement = new_tag if tag.lower() == old_key else tag
                replacement_key = replacement.lower()
                if replacement_key in seen:
                    entry_changed = True  # a case-variant duplicate collapsed
                    continue
                seen.add(replacement_key)
                if replacement != tag:
                    entry_changed = True
                rewritten.append(replacement)
            if entry_changed:
                entry.tags = rewritten
                changed += 1
        if changed:
            self.save()
        return changed

    def remove_tag(self, name: str) -> int:
        """Strip one tag (case-insensitive) from every entry, then persist.

        Like rename_tag this bypasses the recording-row edit lock; labels and
        all other fields stay untouched. Returns the number of entries that
        actually carried the tag.
        """
        key = normalize_tag_name(name).lower()
        if not key:
            raise ValueError("A tag name is required")
        changed = 0
        for entry in self.entries:
            kept = [tag for tag in entry.tags if tag.lower() != key]
            if len(kept) != len(entry.tags):
                entry.tags = kept
                changed += 1
        if changed:
            self.save()
        return changed

    def save(self) -> None:
        os.makedirs(self.output_dir, exist_ok=True)
        # Keep one-generation backup so "restore from backup" is real.
        if os.path.isfile(self.metadata_path):
            backup_path = os.path.join(self.output_dir, "session.backup.json")
            try:
                import shutil
                shutil.copy2(self.metadata_path, backup_path)
            except OSError:
                pass
        payload = {
            "version": SESSION_FORMAT_VERSION,
            "project_name": self.project_name,
            "started_at": self.started_at,
            "timer_running": self.timer_running,
            "next_recording_number": self.next_recording_number,
            "recordings": [asdict(recording) for recording in self.recordings],
            "entries": [asdict(entry) for entry in self.entries],
        }
        temporary_path = f"{self.metadata_path}.tmp"
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(temporary_path, self.metadata_path)
        self._write_markdown()

    def _markdown_sections(
        self,
    ) -> list[tuple[str, list[TimestampEntry], Optional[RecordingInfo]]]:
        """Group entries into (header, entries, recording) display sections.

        ``recording`` is the owning segment (used for stop-duration footers)
        or None for pre-recording entries.
        """
        sections: list[
            tuple[str, list[TimestampEntry], Optional[RecordingInfo]]
        ] = []
        earlier = [
            entry for entry in self.entries if entry.recording_number is None
        ]
        if earlier:
            sections.append(("Earlier timestamps", earlier, None))
        for recording in sorted(self.recordings, key=lambda item: item.number):
            grouped = [
                entry
                for entry in self.entries
                if entry.recording_number == recording.number
            ]
            grouped.sort(key=lambda entry: entry.recording_index or 0)
            if not grouped and recording.number != self.current_recording_number:
                continue
            sections.append((recording.header(), grouped, recording))
        return sections

    @staticmethod
    def _markdown_entry_lines(entry: TimestampEntry) -> list[str]:
        timestamp = format_elapsed_display(entry.elapsed_seconds)
        state = entry.status
        if entry.duration_seconds is not None:
            state += f", {entry.duration_seconds:.1f}s"
        number_prefix = (
            f"{entry.recording_index:03d} "
            if entry.recording_index is not None
            else ""
        )
        if entry.audio_file:
            lead = f"{number_prefix}[{timestamp}]({entry.audio_file})"
        else:
            lead = f"{number_prefix}{timestamp}"
        if entry.kind == "replay":
            head = f"- {lead} — 🎬 Replay: {replay_display_name(entry.replay_file)}, {state}"
        else:
            head = f"- {lead} — {state}"
        lines = [head + entry.annotation_suffix()]
        if entry.kind == "replay":
            uri = replay_file_uri(entry.replay_file)
            if uri:
                footage_name = os.path.basename(str(entry.replay_file))
                lines.append(f"  - Footage: [{footage_name}]({uri})")
        if entry.screenshot_file:
            # Obsidian wikilink embed: bare filename resolves vault-wide,
            # so no folder path is needed.
            lines.append(f"  - ![[{os.path.basename(entry.screenshot_file)}]]")
        if len(entry.takes) > 1:
            # Alternate retakes under the headline link; the active take is
            # already linked there, so only the others are listed.
            for take_number, take in enumerate(entry.takes, start=1):
                relative = str(take.get("file") or "")
                if not relative or relative == entry.audio_file:
                    continue
                duration = take.get("duration_seconds")
                length = f" ({duration:.1f}s)" if duration else ""
                lines.append(
                    f"  - Take {take_number}: [{os.path.basename(relative)}]"
                    f"({relative}){length}"
                )
        if entry.error:
            lines.append(f"  - Error: {entry.error}")
        if entry.transcript:
            # Token-efficient: no "Transcript:" label — just an indented fenced block.
            # If transcript itself contains fences, escalate to four backticks.
            fence = "```"
            if "```" in entry.transcript:
                fence = "````"
            lines.append(f"    {fence}")
            for t_line in entry.transcript.split("\n"):
                lines.append(f"    {t_line}" if t_line else "")
            lines.append(f"    {fence}")
        return lines

    def _write_markdown(self) -> None:
        """Write a readable project log with relative WAV links."""
        if not self.project_name:
            return
        markdown_path = os.path.join(
            self.output_dir, f"{sanitize_project_name(self.project_name)}.md"
        )
        lines = [f"# {self.project_name}", "", "## Timestamps", ""]
        wrote_any = False
        for header, entries, recording in self._markdown_sections():
            if not entries:
                continue
            wrote_any = True
            lines.append(f"### {header}")
            lines.append("")
            if recording is not None and recording.path:
                # Same Footage line shape replay entries use; the video lives
                # outside the project folder, so link it by file:/// URI.
                uri = replay_file_uri(recording.path)
                if uri:
                    footage_name = os.path.basename(str(recording.path))
                    lines.append(f"- Footage: [{footage_name}]({uri})")
                    lines.append("")
            for entry in entries:
                lines.extend(self._markdown_entry_lines(entry))
            duration = (
                recording.duration_seconds() if recording is not None else None
            )
            if duration is not None:
                lines.append("")
                lines.append(
                    f"_Recording stopped — {format_elapsed_display(duration)}_"
                )
            lines.append("")
        if not wrote_any:
            lines.append("_No timestamps yet._")
        with open(markdown_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines).rstrip("\n") + "\n")

    def load(self) -> None:
        try:
            with open(self.metadata_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            return

        try:
            self.project_name = str(payload.get("project_name", self.project_name))
            self.started_at = float(payload.get("started_at", self.started_at))
            self.timer_running = bool(payload.get("timer_running", False))
            raw_recordings = payload.get("recordings")
            self.recordings = (
                [
                    RecordingInfo.from_dict(item)
                    for item in raw_recordings
                    if isinstance(item, dict)
                ]
                if isinstance(raw_recordings, list)
                else []
            )
            derived_next = max(
                (recording.number for recording in self.recordings), default=0
            ) + 1
            try:
                stored_next = int(payload.get("next_recording_number", 0))
            except (TypeError, ValueError):
                stored_next = 0
            # Version 1/2 files have no segments: numbering starts fresh at 1.
            self.next_recording_number = max(stored_next, derived_next, 1)
            self.entries = [
                TimestampEntry.from_dict(item)
                for item in payload.get("entries", [])
                if isinstance(item, dict)
            ]
        except (KeyError, TypeError, ValueError):
            self.entries = []

        # A process cannot resume a live microphone stream or OBS timer.
        # Closing while paused counts as stopped, so pause never survives a restart.
        self.timer_running = False
        self.timer_paused = False
        self._pause_started_at = None
        self.current_recording_number = None
        for entry in self.entries:
            if entry.status == "recording":
                entry.status = "pending"
                entry.error = None

        self.next_id = max((entry.id for entry in self.entries), default=0) + 1


class AudioError(RuntimeError):
    """Raised when microphone capture cannot be started or completed."""


def rms_int16(chunk: bytes) -> float:
    """Root-mean-square amplitude of signed 16-bit little-endian PCM data.

    Pure helper (no sounddevice dependency) so silence-detection math can be
    unit-tested with synthetic buffers.
    """
    sample_count = len(chunk) // 2
    if not sample_count:
        return 0.0
    samples = struct.unpack(f"<{sample_count}h", chunk[: sample_count * 2])
    sum_squares = sum(sample * sample for sample in samples)
    return (sum_squares / sample_count) ** 0.5


def normalize_level(rms: float, clamp: float = MIC_LEVEL_CLAMP_RMS) -> float:
    """Map int16 RMS counts onto a 0..1 meter scale."""
    if rms <= 0:
        return 0.0
    return min(1.0, float(rms) / float(clamp))


class SilenceMonitor:
    """Detect a microphone that never picked up any input while recording.

    The GUI feeds every captured chunk's RMS into :meth:`update` and polls
    :meth:`should_stop`; when no level ever rises above ``threshold`` for
    ``timeout`` seconds, the capture is auto-stopped and discarded so the
    user can retry instead of saving a silent file. Once any activity is
    detected the monitor is disarmed for the rest of the take: pauses in
    speech must never cause recorded audio to be thrown away.
    """

    def __init__(
        self,
        threshold: float = MIC_SILENCE_THRESHOLD_DEFAULT,
        timeout: float = MIC_SILENCE_TIMEOUT_DEFAULT,
        min_elapsed: float = 1.0,
    ):
        self.threshold = max(0.0, float(threshold))
        self.timeout = max(0.0, float(timeout))
        # Grace period after start before a stop may fire, so stream-startup
        # hiccups cannot trigger an instant discard.
        self.min_elapsed = max(0.0, float(min_elapsed))
        self.reset()

    @property
    def voice_detected(self) -> bool:
        """Whether any chunk has reached the threshold since the last reset."""
        return self._voice_seen

    def reset(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        self.started_at = now
        self.last_voice_at = now
        self._voice_seen = False

    def update(self, rms: float, now: Optional[float] = None) -> None:
        """Feed one chunk's RMS; any detection disarms the auto-stop."""
        if rms >= self.threshold:
            self._voice_seen = True
            self.last_voice_at = time.monotonic() if now is None else now

    def silent_seconds(self, now: Optional[float] = None) -> float:
        now = time.monotonic() if now is None else now
        return max(0.0, now - self.last_voice_at)

    def should_stop(self, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        # Any detected activity makes the take immune to auto-discard; only a
        # capture that stayed completely silent from start to finish may fire.
        if self._voice_seen:
            return False
        if now - self.started_at < self.min_elapsed:
            return False
        return self.silent_seconds(now) >= self.timeout


class AudioRecorder:
    """Capture one microphone stream and write signed 16-bit mono WAV audio."""

    def __init__(
        self,
        sample_rate: int = AUDIO_SAMPLE_RATE,
        silence_threshold: float = MIC_SILENCE_THRESHOLD_DEFAULT,
        silence_timeout: float = MIC_SILENCE_TIMEOUT_DEFAULT,
    ):
        self.sample_rate = sample_rate
        self.silence_threshold = max(0.0, float(silence_threshold))
        self.silence_timeout = max(0.0, float(silence_timeout))
        self._stream = None
        self._chunks: list[bytes] = []
        self._lock = threading.Lock()
        self._active = False
        self._path: Optional[str] = None
        self._started_at = 0.0
        self._latest_rms = 0.0
        self._monitor = SilenceMonitor(self.silence_threshold, self.silence_timeout)

    @property
    def active(self) -> bool:
        return self._active

    @property
    def level(self) -> float:
        """Latest input level normalized to 0..1 for meter widgets."""
        with self._lock:
            rms = self._latest_rms
        return normalize_level(rms)

    @property
    def monitor(self) -> SilenceMonitor:
        """Silence watchdog fed by the capture callback."""
        return self._monitor

    @staticmethod
    def _sounddevice():
        try:
            import sounddevice as sd
        except Exception as exc:
            if getattr(sys, "frozen", False):
                raise AudioError(
                    "The packaged app is missing its audio backend (sounddevice/PortAudio). "
                    "Rebuild the app with: pyinstaller --clean timestamp_gui.spec"
                ) from exc
            raise AudioError(
                "Microphone recording needs sounddevice and PortAudio. "
                "Install them with: pip install sounddevice"
            ) from exc
        return sd

    @classmethod
    def list_devices(cls) -> list[tuple[Optional[int], str]]:
        """Return selectable input devices as (index, display name) pairs."""
        sd = cls._sounddevice()
        devices = [(None, "System default")]
        try:
            queried_devices = sd.query_devices()
            hostapis = sd.query_hostapis()
            for index, device in enumerate(queried_devices):
                if int(device.get("max_input_channels", 0)) <= 0:
                    continue
                hostapi_index = int(device.get("hostapi", -1))
                hostapi_name = ""
                if 0 <= hostapi_index < len(hostapis):
                    hostapi_name = str(hostapis[hostapi_index].get("name", ""))
                name = str(device.get("name", f"Device {index}"))
                suffix = f" — {hostapi_name}" if hostapi_name else ""
                devices.append((index, f"{name} (input {index}){suffix}"))
        except Exception as exc:
            raise AudioError(f"Could not enumerate microphones: {exc}") from exc
        return devices

    def start(self, output_path: str, device: Optional[int] = None) -> None:
        if self.active:
            raise AudioError("Another timestamp is already recording")

        sd = self._sounddevice()
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        chunks: list[bytes] = []
        monitor = SilenceMonitor(self.silence_threshold, self.silence_timeout)

        def callback(indata, _frames, _time_info, _status):
            chunk = bytes(indata)
            chunks.append(chunk)
            level = rms_int16(chunk)
            monitor.update(level)
            with self._lock:
                self._latest_rms = level

        try:
            # Check the device before opening the callback stream so common
            # Windows permission/channel problems produce a useful message.
            sd.check_input_settings(
                device=device,
                channels=AUDIO_CHANNELS,
                dtype="int16",
                samplerate=self.sample_rate,
            )
            stream = sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=0,
                device=device,
                channels=AUDIO_CHANNELS,
                dtype="int16",
                callback=callback,
            )
            stream.start()
        except Exception as exc:
            try:
                stream.close()  # type: ignore[union-attr]
            except Exception:
                pass
            raise AudioError(f"Could not start microphone: {exc}") from exc

        with self._lock:
            self._stream = stream
            self._chunks = chunks
            self._path = output_path
            self._started_at = time.monotonic()
            self._latest_rms = 0.0
            self._monitor = monitor
            self._active = True

    def stop(self) -> tuple[str, float]:
        with self._lock:
            if not self._active or self._stream is None or self._path is None:
                raise AudioError("No microphone recording is active")
            stream = self._stream
            output_path = self._path
            chunks = self._chunks
            started_at = self._started_at
            self._active = False
            self._stream = None
            self._path = None
            self._chunks = []

        try:
            stream.stop()
            stream.close()
            audio_data = b"".join(chunks)
            with wave.open(output_path, "wb") as handle:
                handle.setnchannels(AUDIO_CHANNELS)
                handle.setsampwidth(AUDIO_SAMPLE_WIDTH)
                handle.setframerate(self.sample_rate)
                handle.writeframes(audio_data)
        except Exception as exc:
            raise AudioError(f"Could not save microphone recording: {exc}") from exc

        frames = len(audio_data) // (AUDIO_CHANNELS * AUDIO_SAMPLE_WIDTH)
        duration = frames / self.sample_rate
        # A very short recording can contain no callback block, but its real
        # elapsed time is still useful in the session metadata.
        return output_path, max(duration, time.monotonic() - started_at)

    def cancel(self) -> None:
        """Stop capture and discard its output when the app is closing."""
        if not self.active:
            return
        try:
            path, _ = self.stop()
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    def discard(self) -> None:
        """Stop capture and throw away everything recorded, writing nothing.

        Used by the silence watchdog: a recording that never picked up any
        audible input is not worth keeping, so no WAV file is created.
        """
        with self._lock:
            if not self._active or self._stream is None:
                raise AudioError("No microphone recording is active")
            stream = self._stream
            self._active = False
            self._stream = None
            self._path = None
            self._chunks = []
            self._latest_rms = 0.0
        try:
            stream.stop()
            stream.close()
        except Exception as exc:
            raise AudioError(f"Could not stop microphone: {exc}") from exc


class PlaybackController:
    """Play and stop WAV files using the Windows-native WAV player."""

    def __init__(self):
        self.current_path: Optional[str] = None
        self._finished_callback: Optional[Callable[[], None]] = None

    @property
    def active(self) -> bool:
        return self.current_path is not None

    def play(self, path: str, on_finished: Optional[Callable[[], None]] = None) -> float:
        if not os.path.isfile(path):
            raise AudioError(f"Recording file not found: {path}")

        self.stop()
        duration = 0.0
        try:
            with wave.open(path, "rb") as handle:
                duration = handle.getnframes() / float(handle.getframerate() or 1)
        except wave.Error as exc:
            raise AudioError(f"Invalid WAV file: {exc}") from exc

        if sys.platform == "win32":
            import winsound

            winsound.PlaySound(
                path,
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, path])

        self.current_path = path
        self._finished_callback = on_finished
        return duration

    def stop(self) -> None:
        if sys.platform == "win32" and self.current_path:
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
        self.current_path = None
        self._finished_callback = None

    def finish(self) -> None:
        callback = self._finished_callback
        self.stop()
        if callback:
            callback()
