"""Core audio timestamp functionality for the focused MVP.

The module intentionally keeps GUI code out of the session and audio services so
that timestamp persistence and state transitions can be tested without Tkinter.
"""

from __future__ import annotations

import json
import os
import re
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
    """

    number: int
    name: Optional[str]
    started_at: float
    ended_at: Optional[float] = None

    @classmethod
    def from_dict(cls, data: dict) -> "RecordingInfo":
        raw_name = data.get("name")
        raw_ended = data.get("ended_at")
        return cls(
            number=int(data["number"]),
            name=str(raw_name) if raw_name else None,
            started_at=float(data["started_at"]),
            ended_at=float(raw_ended) if raw_ended is not None else None,
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
        )

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
            recording = RecordingInfo(
                number=self.next_recording_number,
                name=recording_name or None,
                started_at=self.started_at,
            )
            self.recordings.append(recording)
            self.current_recording_number = recording.number
            self.next_recording_number += 1
            self.save()

    def stop_timer(self) -> None:
        """Lock timestamp creation until the next OBS recording starts."""
        if self.timer_running:
            self.timer_running = False
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
        while os.path.exists(candidate):
            root, extension = os.path.splitext(candidate)
            candidate = f"{root}_{suffix}{extension}"
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

    def mark_completed(self, entry: TimestampEntry, audio_path: str, duration: float) -> None:
        entry.status = "completed"
        entry.audio_file = os.path.relpath(audio_path, self.output_dir)
        entry.duration_seconds = max(0.0, float(duration))
        entry.error = None
        self.save()

    def mark_error(self, entry: TimestampEntry, error: str) -> None:
        entry.status = "error"
        entry.error = error
        self.save()

    def reset_for_retry(self, entry: TimestampEntry) -> None:
        entry.status = "pending"
        entry.error = None
        self.save()

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
        """Remove an entry from the log and delete its media files.

        The entry's WAV note (``audio_file``) and screenshot JPEG
        (``screenshot_file``) are unlinked from disk when they exist; the
        linked OBS replay video (``replay_file``) is never touched. Cleanup
        is best effort: a locked or undeletable file never blocks removing
        the log entry — it is reported instead. Missing files are not
        reported.

        Returns ``(entry, problems)`` where ``problems`` holds one
        human-readable string per file left behind on disk.
        """
        entry = self.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        if entry.status == "recording":
            raise ValueError("Cannot delete a timestamp while it is recording")
        base = os.path.normcase(os.path.abspath(self.output_dir)) + os.sep
        problems: list[str] = []
        for relative in (entry.audio_file, entry.screenshot_file):
            if not relative:
                continue
            path = os.path.abspath(os.path.join(self.output_dir, relative))
            if not os.path.normcase(path).startswith(base):
                # Hand-edited session.json must not make us unlink arbitrary
                # files outside the project folder.
                problems.append(
                    f"{relative}: outside the project folder, left untouched"
                )
                continue
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                problems.append(f"{relative}: {exc}")
        self.entries.remove(entry)
        self.save()
        return entry, problems

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

        # A process cannot resume a live microphone stream or OBS timer. The
        # next OBS recording start creates a fresh segment for this project.
        self.timer_running = False
        self.current_recording_number = None
        for entry in self.entries:
            if entry.status == "recording":
                entry.status = "pending"
                entry.error = None

        self.next_id = max((entry.id for entry in self.entries), default=0) + 1


class AudioError(RuntimeError):
    """Raised when microphone capture cannot be started or completed."""


class AudioRecorder:
    """Capture one microphone stream and write signed 16-bit mono WAV audio."""

    def __init__(self, sample_rate: int = AUDIO_SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._stream = None
        self._chunks: list[bytes] = []
        self._lock = threading.Lock()
        self._active = False
        self._path: Optional[str] = None
        self._started_at = 0.0

    @property
    def active(self) -> bool:
        return self._active

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

        def callback(indata, _frames, _time_info, _status):
            chunks.append(bytes(indata))

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
