import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    import mss  # noqa: F401
    from PIL import Image  # noqa: F401

    SCREENSHOT_DEPS_AVAILABLE = True
except ImportError:
    SCREENSHOT_DEPS_AVAILABLE = False

from timestamp_audio import (
    AudioError,
    AudioRecorder,
    DEFAULT_TAG_COLOR,
    RecordingInfo,
    SESSION_FILENAME,
    TAG_NAME_MAX_LENGTH,
    TimestampSession,
    clean_label,
    clean_tags,
    format_elapsed,
    format_elapsed_display,
    normalize_tag_name,
    parse_time_input,
    read_project_stats,
    remove_recent_project,
    replay_display_name,
    replay_file_uri,
    sanitize_project_name,
    sanitize_recent_projects,
    sanitize_tag_definitions,
    update_recent_projects,
)
from timestamp_screenshot import ScreenshotError, compute_target_size


class AudioDeviceTests(unittest.TestCase):
    def test_input_devices_include_index_and_host_api(self):
        class FakeSoundDevice:
            @staticmethod
            def query_devices():
                return [{"name": "My Mic", "max_input_channels": 1, "hostapi": 0}]

            @staticmethod
            def query_hostapis():
                return [{"name": "Windows MME"}]

        with patch.object(AudioRecorder, "_sounddevice", return_value=FakeSoundDevice):
            devices = AudioRecorder.list_devices()

        self.assertEqual(devices[0], (None, "System default"))
        self.assertEqual(devices[1], (0, "My Mic (input 0) — Windows MME"))

    def test_device_query_errors_become_audio_errors(self):
        class FailingSoundDevice:
            @staticmethod
            def query_devices():
                raise RuntimeError("PortAudio unavailable")

            @staticmethod
            def query_hostapis():
                return []

        with patch.object(AudioRecorder, "_sounddevice", return_value=FailingSoundDevice):
            with self.assertRaisesRegex(AudioError, "Could not enumerate microphones"):
                AudioRecorder.list_devices()


class TimestampSessionTests(unittest.TestCase):
    def test_elapsed_format_is_display_and_filename_safe(self):
        self.assertEqual(format_elapsed(3723), "01-02-03")
        self.assertEqual(format_elapsed_display(3723), "01:02:03")

    def test_timestamp_filename_contains_id_and_elapsed_time(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(12 * 60 + 34)
            self.assertEqual(session.audio_path(entry), os.path.join(folder, "001_00-12-34.wav"))

    def test_segmented_filename_contains_recording_and_index(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            session.start_timer("[21-08][14-55-19]")
            entry = session.create_timestamp(12 * 60 + 34)
            self.assertEqual(
                session.audio_path(entry),
                os.path.join(folder, "R01-001_00-12-34.wav"),
            )

    def test_audio_path_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(10)
            first_path = session.audio_path(entry)
            open(first_path, "wb").close()
            self.assertEqual(
                os.path.basename(session.audio_path(entry)), "001_00-00-10_2.wav"
            )

    def test_project_markdown_links_completed_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            project_folder = os.path.join(folder, "My Project")
            session = TimestampSession(project_folder, "My Project", load_existing=False)
            session.start_timer("[21-08][14-55-19]")
            entry = session.create_timestamp(10)
            audio_path = os.path.join(project_folder, "R01-001_00-00-10.wav")
            session.mark_completed(entry, audio_path, 2.5)

            with open(os.path.join(project_folder, "My Project.md"), encoding="utf-8") as handle:
                markdown = handle.read()
            self.assertIn("# My Project", markdown)
            self.assertIn("### [21-08][14-55-19]", markdown)
            self.assertIn("[00:00:10](R01-001_00-00-10.wav)", markdown)

    def test_project_name_is_safe_for_windows_paths(self):
        self.assertEqual(sanitize_project_name("My: Project/Take 1"), "My_ Project_Take 1")

    def test_session_round_trips_completed_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(10)
            audio_path = os.path.join(folder, "001_00-00-10.wav")
            session.mark_completed(entry, audio_path, 2.5)

            restored = TimestampSession(folder)
            self.assertEqual(len(restored.entries), 1)
            restored_entry = restored.entries[0]
            self.assertEqual(restored_entry.status, "completed")
            self.assertEqual(restored_entry.audio_file, "001_00-00-10.wav")
            self.assertEqual(restored_entry.duration_seconds, 2.5)

    def test_interrupted_recording_is_pending_after_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(4)
            session.mark_recording(entry)

            restored = TimestampSession(folder)
            self.assertEqual(restored.entries[0].status, "pending")
            self.assertIsNone(restored.entries[0].error)

    def test_metadata_is_valid_json(self):
        with tempfile.TemporaryDirectory() as folder:
            TimestampSession(folder, load_existing=False).create_timestamp(1)
            with open(os.path.join(folder, "session.json"), encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertEqual(data["version"], 6)
            self.assertEqual(data["entries"][0]["id"], 1)
            self.assertEqual(data["recordings"], [])
            self.assertEqual(data["next_recording_number"], 1)


class TimestampAnnotationTests(unittest.TestCase):
    def test_label_and_tags_round_trip_through_save_and_load(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            session.create_timestamp(10, label="Good take", tags=["kill", "bug"])

            restored = TimestampSession(folder)
            entry = restored.entries[0]
            self.assertEqual(entry.label, "Good take")
            self.assertEqual(entry.tags, ["kill", "bug"])

    def test_version_one_session_loads_without_annotations(self):
        with tempfile.TemporaryDirectory() as folder:
            legacy = {
                "version": 1,
                "project_name": "Legacy",
                "started_at": 1000.0,
                "timer_running": False,
                "entries": [
                    {
                        "id": 1,
                        "elapsed_seconds": 12.0,
                        "created_at": "2024-01-01T00:00:00+00:00",
                        "status": "completed",
                        "audio_file": "001_00-00-12.wav",
                        "duration_seconds": 3.0,
                    }
                ],
            }
            with open(os.path.join(folder, "session.json"), "w", encoding="utf-8") as handle:
                json.dump(legacy, handle)

            restored = TimestampSession(folder)
            self.assertEqual(len(restored.entries), 1)
            self.assertIsNone(restored.entries[0].label)
            self.assertEqual(restored.entries[0].tags, [])

    def test_update_entry_persists_annotations_to_markdown(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, "My Project", load_existing=False)
            entry = session.create_timestamp(754)
            session.update_entry(entry.id, "Check this later", ["idea"])

            with open(os.path.join(folder, "session.json"), encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertEqual(data["entries"][0]["label"], "Check this later")
            self.assertEqual(data["entries"][0]["tags"], ["idea"])

            with open(os.path.join(folder, "My Project.md"), encoding="utf-8") as handle:
                markdown = handle.read()
            self.assertIn('"Check this later"', markdown)
            self.assertIn("#idea", markdown)

    def test_remove_entry_deletes_media_files(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, "My Project", load_existing=False)
            entry = session.create_timestamp(10)
            audio_path = os.path.join(folder, "001_00-00-10.wav")
            open(audio_path, "wb").close()
            session.mark_completed(entry, audio_path, 2.5)
            screenshot_path = session.screenshot_path(entry)
            os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
            open(screenshot_path, "wb").close()
            entry.screenshot_file = os.path.relpath(screenshot_path, folder)
            session.save()

            removed, problems = session.remove_entry(entry.id)
            self.assertEqual(removed.id, 1)
            self.assertEqual(problems, [])
            self.assertEqual(session.entries, [])
            self.assertFalse(os.path.exists(audio_path))
            self.assertFalse(os.path.exists(screenshot_path))

            with open(os.path.join(folder, "My Project.md"), encoding="utf-8") as handle:
                markdown = handle.read()
            self.assertIn("_No timestamps yet._", markdown)

    def test_remove_entry_tolerates_missing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(5)
            entry.audio_file = "ghost.wav"
            entry.screenshot_file = os.path.join("Screenshots", "ghost.jpg")

            removed, problems = session.remove_entry(entry.id)
            self.assertEqual(removed.id, 1)
            self.assertEqual(problems, [])
            self.assertEqual(session.entries, [])

    def test_remove_entry_deletes_note_but_keeps_replay_video(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, "My Project", load_existing=False)
            session.start_timer("[21-08][10-00-00]")
            replay_video = os.path.join(folder, "replay.mp4")
            open(replay_video, "wb").close()
            replay = session.create_replay_entry(replay_video)
            audio_path = os.path.join(folder, "R01-001_00-00-00.wav")
            open(audio_path, "wb").close()
            session.mark_completed(replay, audio_path, 3.0)

            removed, problems = session.remove_entry(replay.id)
            self.assertEqual(removed.id, replay.id)
            self.assertEqual(problems, [])
            self.assertEqual(session.entries, [])
            self.assertFalse(os.path.exists(audio_path))
            self.assertTrue(os.path.isfile(replay_video))

    def test_remove_entry_reports_undeletable_file(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(5)
            blocked = os.path.join(folder, "blocked.jpg")
            os.makedirs(blocked)  # a directory forces OSError on remove
            entry.screenshot_file = "blocked.jpg"

            _, problems = session.remove_entry(entry.id)
            self.assertEqual(len(problems), 1)
            self.assertIn("blocked.jpg", problems[0])
            self.assertTrue(os.path.isdir(blocked))
            self.assertEqual(len(session.entries), 0)

    def test_remove_entry_leaves_out_of_project_paths_alone(self):
        with tempfile.TemporaryDirectory() as outer:
            protected = os.path.join(outer, "precious.txt")
            open(protected, "w").close()
            project = os.path.join(outer, "project")
            os.makedirs(project)
            session = TimestampSession(project, load_existing=False)
            entry = session.create_timestamp(5)
            entry.audio_file = os.path.relpath(protected, project)

            _, problems = session.remove_entry(entry.id)
            self.assertTrue(os.path.isfile(protected))
            self.assertEqual(len(problems), 1)
            self.assertIn("outside the project folder", problems[0])

    def test_remove_entry_rejects_active_recording(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(5)
            session.mark_recording(entry)
            with self.assertRaisesRegex(ValueError, "recording"):
                session.remove_entry(entry.id)
            self.assertEqual(len(session.entries), 1)

    def test_update_entry_rejects_active_recording(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(5)
            session.mark_recording(entry)
            with self.assertRaisesRegex(ValueError, "recording"):
                session.update_entry(entry.id, "nope")

    def test_manual_creation_with_explicit_time_and_label(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(2530, label="Missed live", tags=["bug"])
            self.assertEqual(entry.elapsed_seconds, 2530)
            self.assertEqual(entry.label, "Missed live")
            self.assertEqual(entry.tags, ["bug"])

    def test_locked_session_still_records_and_completes_old_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            session.start_timer("[21-08][10-00-00]")
            entry = session.create_timestamp(10)
            session.stop_timer()

            # Rows stay usable while locked: pending notes can still record.
            session.mark_recording(entry)
            audio_path = os.path.join(folder, "R01-001_00-00-10.wav")
            session.mark_completed(entry, audio_path, 2.0)

            restored = TimestampSession(folder)
            self.assertEqual(restored.entries[0].status, "completed")
            self.assertEqual(restored.entries[0].duration_seconds, 2.0)

    def test_markdown_includes_label_and_tag_suffix(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, "My Project", load_existing=False)
            session.start_timer("[21-08][14-55-19]")
            entry = session.create_timestamp(10, label="Good take", tags=["kill", "bug"])
            audio_path = os.path.join(folder, "R01-001_00-00-10.wav")
            session.mark_completed(entry, audio_path, 2.5)

            with open(os.path.join(folder, "My Project.md"), encoding="utf-8") as handle:
                markdown = handle.read()
            self.assertIn(
                '001 [00:00:10](R01-001_00-00-10.wav) — completed, 2.5s — "Good take" #kill #bug',
                markdown,
            )


class RecordingSegmentTests(unittest.TestCase):
    def _session(self, folder):
        return TimestampSession(folder, "My Project", load_existing=False)

    def test_recording_duration_is_none_while_open_then_delta_when_closed(self):
        recording = RecordingInfo(number=1, name="[21-08][10-00-00]", started_at=1000.0)
        self.assertIsNone(recording.duration_seconds())
        recording.ended_at = 1065.5
        self.assertAlmostEqual(recording.duration_seconds(), 65.5)
        # Defensive clamp: an ended_at earlier than started_at is never negative.
        recording.ended_at = 999.0
        self.assertEqual(recording.duration_seconds(), 0.0)

    def test_markdown_shows_stop_duration_for_finished_segments_not_live_one(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._session(folder)
            session.start_timer("[21-08][10-00-00]")
            finished = session.create_timestamp(10)
            session.stop_timer()
            session.recordings[0].started_at = 1000.0
            session.recordings[0].ended_at = 1165.0
            session.start_timer("[21-08][11-00-00]")
            session.create_timestamp(20)  # live segment: still open
            session.recordings[1].started_at = 5000.0
            session.save()

            with open(os.path.join(folder, "My Project.md"), encoding="utf-8") as handle:
                markdown = handle.read()

            self.assertIn("_Recording stopped — 00:02:45_", markdown)
            self.assertEqual(markdown.count("_Recording stopped"), 1)
            finished_at = markdown.index("### [21-08][10-00-00]")
            footer_at = markdown.index("_Recording stopped")
            live_at = markdown.index("### [21-08][11-00-00]")
            self.assertLess(finished_at, footer_at)
            self.assertLess(footer_at, live_at)
            self.assertIn(f"{finished.recording_index:03d}", markdown)

    def test_successive_recordings_increment_numbers_and_restart_indices(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._session(folder)
            session.start_timer("[21-08][10-00-00]")
            first = session.create_timestamp(5)
            second = session.create_timestamp(10)
            session.stop_timer()
            session.start_timer("[21-08][11-00-00]")
            third = session.create_timestamp(7)
            session.stop_timer()

            self.assertEqual(first.recording_number, 1)
            self.assertEqual(first.recording_index, 1)
            self.assertEqual(second.recording_number, 1)
            self.assertEqual(second.recording_index, 2)
            self.assertEqual(third.recording_number, 2)
            self.assertEqual(third.recording_index, 1)
            self.assertEqual(len(session.recordings), 2)
            self.assertEqual(session.recordings[0].name, "[21-08][10-00-00]")
            self.assertIsNotNone(session.recordings[0].ended_at)
            self.assertIsNotNone(session.recordings[1].ended_at)

    def test_manual_timestamp_while_locked_attaches_to_latest_segment(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._session(folder)
            session.start_timer("[21-08][10-00-00]")
            session.stop_timer()
            manual = session.create_timestamp(42)
            self.assertEqual(manual.recording_number, 1)
            self.assertEqual(manual.recording_index, 1)

    def test_manual_timestamp_before_any_recording_has_no_segment(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._session(folder)
            manual = session.create_timestamp(42)
            self.assertIsNone(manual.recording_number)
            self.assertIsNone(manual.recording_index)

    def test_markdown_groups_by_recording_with_earlier_first(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._session(folder)
            pre = session.create_timestamp(60)  # created before any recording
            session.start_timer("[21-08][10-00-00]")
            first = session.create_timestamp(10)
            session.stop_timer()
            session.start_timer()  # no OBS name known: fallback header
            second = session.create_timestamp(20)
            session.stop_timer()

            with open(os.path.join(folder, "My Project.md"), encoding="utf-8") as handle:
                markdown = handle.read()

            earlier_at = markdown.index("### Earlier timestamps")
            first_at = markdown.index("### [21-08][10-00-00]")
            fallback_at = markdown.index("### Recording 2")
            self.assertLess(earlier_at, first_at)
            self.assertLess(first_at, fallback_at)
            self.assertIn("- 00:01:00 — pending", markdown)
            self.assertIn("- 001 00:00:10 — pending", markdown)
            self.assertIn("- 001 00:00:20 — pending", markdown)
            self.assertNotIn("002 [00:00", markdown)

    def test_version_two_session_migrates_to_earlier_group_and_saves_v4(self):
        with tempfile.TemporaryDirectory() as folder:
            legacy = {
                "version": 2,
                "project_name": "Legacy",
                "started_at": 1000.0,
                "timer_running": False,
                "entries": [
                    {
                        "id": 1,
                        "elapsed_seconds": 12.0,
                        "created_at": "2024-01-01T00:00:00+00:00",
                        "status": "completed",
                        "audio_file": "001_00-00-12.wav",
                        "duration_seconds": 3.0,
                    }
                ],
            }
            with open(os.path.join(folder, "session.json"), "w", encoding="utf-8") as handle:
                json.dump(legacy, handle)

            restored = TimestampSession(folder)
            self.assertEqual(len(restored.entries), 1)
            self.assertIsNone(restored.entries[0].recording_number)
            self.assertIsNone(restored.entries[0].recording_index)
            self.assertEqual(restored.recordings, [])
            self.assertEqual(restored.next_recording_number, 1)

            restored.save()
            with open(os.path.join(folder, "session.json"), encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertEqual(data["version"], 6)

            with open(os.path.join(folder, "Legacy.md"), encoding="utf-8") as handle:
                markdown = handle.read()
            self.assertIn("### Earlier timestamps", markdown)
            self.assertIn("[00:00:12](001_00-00-12.wav)", markdown)

    def test_v3_session_round_trips_recordings(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._session(folder)
            session.start_timer("[21-08][10-00-00]")
            first = session.create_timestamp(5)
            session.stop_timer()
            session.start_timer("[21-08][11-00-00]")
            second = session.create_timestamp(6)
            session.stop_timer()

            restored = TimestampSession(folder)
            self.assertEqual(len(restored.recordings), 2)
            self.assertEqual(restored.recordings[0].number, 1)
            self.assertEqual(restored.recordings[0].name, "[21-08][10-00-00]")
            self.assertEqual(restored.recordings[1].number, 2)
            self.assertIsNotNone(restored.recordings[1].ended_at)
            self.assertEqual(restored.next_recording_number, 3)
            self.assertEqual(restored.get(first.id).recording_number, 1)
            self.assertEqual(restored.get(second.id).recording_index, 1)

    def test_recording_numbering_continues_after_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._session(folder)
            session.start_timer("[21-08][10-00-00]")
            session.stop_timer()
            session.start_timer("[21-08][11-00-00]")
            session.stop_timer()

            restored = TimestampSession(folder)
            restored.start_timer("[21-08][12-00-00]")
            entry = restored.create_timestamp(1)
            self.assertEqual(entry.recording_number, 3)
            self.assertEqual(entry.recording_index, 1)

    def test_audio_paths_unique_across_segments(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._session(folder)
            session.start_timer("[21-08][10-00-00]")
            first = session.create_timestamp(10)
            session.stop_timer()
            session.start_timer("[21-08][11-00-00]")
            second = session.create_timestamp(10)
            self.assertEqual(
                os.path.basename(session.audio_path(first)),
                "R01-001_00-00-10.wav",
            )
            self.assertEqual(
                os.path.basename(session.audio_path(second)),
                "R02-001_00-00-10.wav",
            )

    def test_collision_suffix_within_segment(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._session(folder)
            session.start_timer("[21-08][10-00-00]")
            entry = session.create_timestamp(10)
            first_path = session.audio_path(entry)
            open(first_path, "wb").close()
            self.assertEqual(
                os.path.basename(session.audio_path(entry)),
                "R01-001_00-00-10_2.wav",
            )

    def test_name_recording_backfills_markdown_header(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._session(folder)
            session.start_timer()
            session.create_timestamp(10)
            session.stop_timer()

            with open(os.path.join(folder, "My Project.md"), encoding="utf-8") as handle:
                self.assertIn("### Recording 1", handle.read())

            session.name_recording(1, "[21-08][15-00-00]")
            self.assertEqual(session.recordings[0].name, "[21-08][15-00-00]")
            with open(os.path.join(folder, "My Project.md"), encoding="utf-8") as handle:
                markdown = handle.read()
            self.assertIn("### [21-08][15-00-00]", markdown)
            self.assertNotIn("### Recording 1", markdown)


class ScreenshotSupportTests(unittest.TestCase):
    def _project_session(self, folder):
        return TimestampSession(folder, "My Project", load_existing=False)

    def test_compute_target_size_fits_height_preserving_aspect(self):
        self.assertEqual(compute_target_size(1920, 1080), (1280, 720))
        self.assertEqual(compute_target_size(2560, 1440), (1280, 720))
        self.assertEqual(compute_target_size(3440, 1440), (1720, 720))

    def test_compute_target_size_never_upscales(self):
        self.assertEqual(compute_target_size(1280, 600), (1280, 600))
        self.assertEqual(compute_target_size(640, 480), (640, 480))

    def test_compute_target_size_rejects_invalid_sizes(self):
        with self.assertRaises(ScreenshotError):
            compute_target_size(0, 720)

    def test_screenshot_path_mirrors_segmented_audio_naming(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._project_session(folder)
            session.start_timer("[21-08][14-55-19]")
            entry = session.create_timestamp(12 * 60 + 34)
            self.assertEqual(
                session.screenshot_path(entry),
                os.path.join(folder, "Screenshots", "R01-001_00-12-34.jpg"),
            )

    def test_legacy_screenshot_path_uses_entry_id(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(12 * 60 + 34)
            self.assertEqual(
                session.screenshot_path(entry),
                os.path.join(folder, "Screenshots", "001_00-12-34.jpg"),
            )

    def test_screenshot_path_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(10)
            first_path = session.screenshot_path(entry)
            os.makedirs(os.path.dirname(first_path), exist_ok=True)
            open(first_path, "wb").close()
            self.assertEqual(
                os.path.basename(session.screenshot_path(entry)),
                "001_00-00-10_2.jpg",
            )

    def test_markdown_embeds_screenshot_below_entry_line(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._project_session(folder)
            shot_relative = os.path.join("Screenshots", "001_00-00-10.jpg")
            entry = session.create_timestamp(10)
            entry.screenshot_file = shot_relative
            session.create_timestamp(20)
            session.save()

            with open(os.path.join(folder, "My Project.md"), encoding="utf-8") as handle:
                markdown = handle.read()
            self.assertIn("- 00:00:10 — pending", markdown)
            self.assertIn("  - ![[001_00-00-10.jpg]]", markdown)
            # Entries without a screenshot render exactly as before.
            self.assertNotIn(os.path.join("Screenshots", "002_00-00-20.jpg"), markdown)

    def test_screenshot_file_round_trips_through_save_and_load(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(5)
            entry.screenshot_file = os.path.join("Screenshots", "001_00-00-05.jpg")
            session.save()

            restored = TimestampSession(folder)
            self.assertEqual(restored.entries[0].screenshot_file, entry.screenshot_file)

    def test_v3_session_without_screenshots_loads_none(self):
        with tempfile.TemporaryDirectory() as folder:
            legacy = {
                "version": 3,
                "project_name": "Legacy",
                "started_at": 1000.0,
                "timer_running": False,
                "next_recording_number": 1,
                "recordings": [],
                "entries": [
                    {
                        "id": 1,
                        "elapsed_seconds": 12.0,
                        "created_at": "2024-01-01T00:00:00+00:00",
                        "status": "completed",
                        "audio_file": "001_00-00-12.wav",
                        "duration_seconds": 3.0,
                    }
                ],
            }
            with open(os.path.join(folder, "session.json"), "w", encoding="utf-8") as handle:
                json.dump(legacy, handle)

            restored = TimestampSession(folder)
            self.assertEqual(len(restored.entries), 1)
            self.assertIsNone(restored.entries[0].screenshot_file)


class ScreenshotCaptureTests(unittest.TestCase):
    @unittest.skipUnless(
        SCREENSHOT_DEPS_AVAILABLE, "mss and Pillow are required for real capture"
    )
    def test_capture_writes_jpeg_at_or_below_720p_height(self):
        from timestamp_screenshot import capture_to_path

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "shot.jpg")
            capture_to_path(path)

            self.assertTrue(os.path.isfile(path))
            with Image.open(path) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertGreater(image.width, 0)
                self.assertLessEqual(image.height, 720)


class RecentProjectTests(unittest.TestCase):
    def test_new_project_is_prepended(self):
        recents = update_recent_projects([], "Alpha", "E:/out")
        self.assertEqual(
            recents, [{"name": "Alpha", "output_folder": os.path.abspath("E:/out")}]
        )

    def test_existing_project_moves_to_front_without_duplicating(self):
        stored = [
            {"name": "Beta", "output_folder": "E:/b"},
            {"name": "Alpha", "output_folder": "E:/a"},
        ]
        recents = update_recent_projects(stored, "Beta", "E:/B")
        self.assertEqual([entry["name"] for entry in recents], ["Beta", "Alpha"])
        self.assertEqual(len(recents), 2)

    def test_list_is_capped_at_five_with_oldest_dropped(self):
        recents = []
        for index in range(7):
            recents = update_recent_projects(recents, f"P{index}", f"E:/p{index}")
        self.assertEqual(len(recents), 5)
        self.assertEqual([entry["name"] for entry in recents], ["P6", "P5", "P4", "P3", "P2"])

    def test_same_name_in_different_folders_both_kept(self):
        recents = update_recent_projects([], "Take 1", "E:/one")
        recents = update_recent_projects(recents, "Take 1", "E:/two")
        self.assertEqual(len(recents), 2)

    def test_duplicate_detection_ignores_path_case_and_separators(self):
        recents = update_recent_projects([], "My Project", "e:\\out\\proj")
        recents = update_recent_projects(recents, "my project", "E:/OUT/proj/")
        self.assertEqual(len(recents), 1)
        self.assertEqual(recents[0]["name"], "my project")

    def test_malformed_entries_are_filtered(self):
        junk = [
            "nope",
            {"name": ""},
            {"name": "NoFolder"},
            {"name": "Ok", "output_folder": "E:/ok"},
            None,
        ]
        recents = sanitize_recent_projects(junk)
        self.assertEqual(len(recents), 1)
        self.assertEqual(recents[0]["name"], "Ok")

    def test_custom_limit_is_respected(self):
        recents = []
        for index in range(4):
            recents = update_recent_projects(recents, f"P{index}", f"E:/p{index}", limit=2)
        self.assertEqual([entry["name"] for entry in recents], ["P3", "P2"])

    def test_blank_update_keeps_existing_entries(self):
        stored = [{"name": "Alpha", "output_folder": "E:/a"}]
        self.assertEqual(update_recent_projects(stored, "", "E:/x"), sanitize_recent_projects(stored))
        self.assertEqual(update_recent_projects(stored, "Alpha", "   "), sanitize_recent_projects(stored))


class RemoveRecentProjectTests(unittest.TestCase):
    def test_matching_entry_is_removed_case_and_separator_blind(self):
        stored = [
            {"name": "Alpha", "output_folder": os.path.abspath("E:/a")},
            {"name": "Beta", "output_folder": os.path.abspath("E:/b")},
        ]
        remaining = remove_recent_project(stored, "ALPHA", "e:/a/")
        self.assertEqual([entry["name"] for entry in remaining], ["Beta"])

    def test_unknown_entry_leaves_list_unchanged(self):
        stored = [{"name": "Alpha", "output_folder": os.path.abspath("E:/a")}]
        self.assertEqual(
            remove_recent_project(stored, "Zeta", "E:/z"),
            sanitize_recent_projects(stored),
        )

    def test_blank_arguments_are_a_noop(self):
        stored = [{"name": "Alpha", "output_folder": os.path.abspath("E:/a")}]
        self.assertEqual(remove_recent_project(stored, "", "E:/a"), sanitize_recent_projects(stored))
        self.assertEqual(remove_recent_project(stored, "Alpha", "  "), sanitize_recent_projects(stored))


class ReadProjectStatsTests(unittest.TestCase):
    def _write_session(self, folder: str, payload) -> None:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, SESSION_FILENAME)
        with open(path, "w", encoding="utf-8") as handle:
            if isinstance(payload, str):
                handle.write(payload)
            else:
                json.dump(payload, handle)

    def test_counts_entries_and_recordings(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_session(
                tmp,
                {
                    "entries": [{"id": 1}, {"id": 2}, {"id": 3}],
                    "recordings": [{"number": 1}, {"number": 2}],
                },
            )
            self.assertEqual(
                read_project_stats(tmp), {"timestamps": 3, "recordings": 2}
            )

    def test_missing_folder_yields_zeros(self):
        missing = os.path.join(tempfile.gettempdir(), "definitely-not-here-12345")
        self.assertEqual(read_project_stats(missing), {"timestamps": 0, "recordings": 0})
        self.assertEqual(read_project_stats(""), {"timestamps": 0, "recordings": 0})

    def test_corrupt_or_malformed_data_yields_zeros(self):
        for payload in ("{not json", [], 42, {}, {"entries": "three"}, {"recordings": {}}):
            with tempfile.TemporaryDirectory() as tmp:
                self._write_session(tmp, payload)
                self.assertEqual(
                    read_project_stats(tmp), {"timestamps": 0, "recordings": 0}
                )


class InputCleaningTests(unittest.TestCase):
    def test_parse_time_input_accepts_supported_formats(self):
        self.assertEqual(parse_time_input("90"), 90.0)
        self.assertEqual(parse_time_input("42:10"), 2530.0)
        self.assertEqual(parse_time_input("01:02:03"), 3723.0)
        self.assertEqual(parse_time_input("  7:05 "), 425.0)

    def test_parse_time_input_rejects_garbage(self):
        self.assertIsNone(parse_time_input("abc"))
        self.assertIsNone(parse_time_input("1:2:3:4"))
        self.assertIsNone(parse_time_input("-5"))
        self.assertIsNone(parse_time_input(""))
        self.assertIsNone(parse_time_input("1.5:10"))

    def test_clean_label_flattens_lines_and_caps_length(self):
        self.assertEqual(clean_label("  hello\nworld  "), "hello world")
        self.assertEqual(clean_label('say "hi"'), "say 'hi'")
        self.assertEqual(len(clean_label("x" * 500)), 200)
        self.assertIsNone(clean_label(None))
        self.assertIsNone(clean_label("   "))

    def test_clean_tags_strips_hashes_and_duplicates(self):
        self.assertEqual(clean_tags([" #kill ", "#kill", "", "bug"]), ["kill", "bug"])
        self.assertEqual(clean_tags(None), [])


class TranscriptTests(unittest.TestCase):
    def test_clean_transcript_normalizes_whitespace(self):
        from timestamp_audio import clean_transcript

        self.assertEqual(clean_transcript("  hello\nworld  "), "hello\nworld")
        self.assertEqual(clean_transcript("a\n\n\nb\n\n\nc"), "a\n\nb\n\nc")
        self.assertIsNone(clean_transcript(None))
        self.assertIsNone(clean_transcript("   \n   "))
        self.assertIsNone(clean_transcript(""))
        # Long transcripts are truncated
        long = "x" * 20000
        self.assertTrue(len(clean_transcript(long)) <= 10002)  # 10000 + " …"
        self.assertIn("…", clean_transcript(long))

    def test_transcript_round_trip_through_save_and_load(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(10)
            session.update_transcript(entry.id, "Hello world\nSecond line")
            restored = TimestampSession(folder)
            self.assertEqual(restored.entries[0].transcript, "Hello world\nSecond line")

    def test_v4_session_loads_transcript_as_none(self):
        with tempfile.TemporaryDirectory() as folder:
            legacy = {
                "version": 4,
                "project_name": "Legacy",
                "started_at": 1000.0,
                "timer_running": False,
                "next_recording_number": 1,
                "recordings": [],
                "entries": [
                    {
                        "id": 1,
                        "elapsed_seconds": 12.0,
                        "created_at": "2024-01-01T00:00:00+00:00",
                        "status": "completed",
                        "audio_file": "001_00-00-12.wav",
                        "duration_seconds": 3.0,
                    }
                ],
            }
            with open(os.path.join(folder, "session.json"), "w", encoding="utf-8") as handle:
                json.dump(legacy, handle)
            restored = TimestampSession(folder)
            self.assertIsNone(restored.entries[0].transcript)
            # First save should migrate to v6
            restored.save()
            with open(os.path.join(folder, "session.json"), encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertEqual(data["version"], 6)
            self.assertIsNone(data["entries"][0]["transcript"])

    def test_markdown_embeds_transcript_block(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(os.path.join(folder, "My Project"), "My Project", load_existing=False)
            entry = session.create_timestamp(10)
            entry.screenshot_file = os.path.join("Screenshots", "001_00-00-10.jpg")
            session.update_transcript(entry.id, "First line\nSecond line")
            with open(os.path.join(folder, "My Project", "My Project.md"), encoding="utf-8") as handle:
                md = handle.read()
            self.assertNotIn("Transcript:", md)
            self.assertIn("    ```", md)
            self.assertIn("    First line", md)
            self.assertIn("    Second line", md)
            # Screenshot still present above transcript fence (Obsidian embed form)
            self.assertIn("![[001_00-00-10.jpg]]", md)
            self.assertLess(md.index("![[001_00-00-10.jpg]]"), md.index("    ```"))

    def test_markdown_no_transcript_no_block(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(os.path.join(folder, "My Project"), "My Project", load_existing=False)
            session.create_timestamp(10)
            with open(os.path.join(folder, "My Project", "My Project.md"), encoding="utf-8") as handle:
                md = handle.read()
            self.assertNotIn("Transcript:", md)
            self.assertNotIn("    ```", md)

    def test_markdown_escapes_fenced_block(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(os.path.join(folder, "My Project"), "My Project", load_existing=False)
            entry = session.create_timestamp(10)
            session.update_transcript(entry.id, "text with ``` inside")
            with open(os.path.join(folder, "My Project", "My Project.md"), encoding="utf-8") as handle:
                md = handle.read()
            self.assertIn("    ````", md)

    def test_update_transcript_rejects_recording(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(5)
            session.mark_recording(entry)
            with self.assertRaisesRegex(ValueError, "recording"):
                session.update_transcript(entry.id, "nope")


class TagDefinitionTests(unittest.TestCase):
    def test_non_list_input_falls_back_to_defaults_copy(self):
        defaults = [{"name": "kill", "color": "#FF5252"}]
        for raw in (None, "nope", {"name": "kill"}, 42):
            definitions = sanitize_tag_definitions(raw, defaults)
            self.assertEqual(definitions, defaults)
            self.assertIsNot(definitions, defaults)

    def test_valid_definitions_pass_through(self):
        raw = [
            {"name": "kill", "color": "#FF5252"},
            {"name": "bug", "color": "#ffab00"},
        ]
        self.assertEqual(sanitize_tag_definitions(raw, []), raw)

    def test_names_are_trimmed_and_hash_stripped(self):
        raw = [{"name": "  # kill  ", "color": "#FF5252"}]
        definitions = sanitize_tag_definitions(raw, [])
        self.assertEqual(definitions[0]["name"], "kill")

    def test_invalid_or_missing_colors_fall_back(self):
        raw = [
            {"name": "a", "color": "red"},
            {"name": "b"},
            {"name": "c", "color": "#12345"},
            {"name": "d", "color": "#12345G"},
        ]
        definitions = sanitize_tag_definitions(raw, [])
        self.assertEqual(
            [definition["color"] for definition in definitions],
            [DEFAULT_TAG_COLOR] * 4,
        )

    def test_custom_fallback_color_is_used(self):
        definitions = sanitize_tag_definitions(
            [{"name": "a", "color": "nope"}], [], fallback_color="#123456"
        )
        self.assertEqual(definitions[0]["color"], "#123456")

    def test_duplicate_names_case_insensitive_keep_first(self):
        raw = [
            {"name": "Kill", "color": "#FF5252"},
            {"name": "kill", "color": "#00E676"},
            {"name": "KILL", "color": "#448AFF"},
        ]
        definitions = sanitize_tag_definitions(raw, [])
        self.assertEqual(
            definitions, [{"name": "Kill", "color": "#FF5252"}]
        )

    def test_malformed_items_are_skipped_and_empty_result_uses_defaults(self):
        defaults = [{"name": "idea", "color": "#448AFF"}]
        self.assertEqual(
            sanitize_tag_definitions(["junk", 17, {"color": "#FFFFFF"}], defaults),
            defaults,
        )

    def test_normalize_tag_name_matches_clean_tags_rules(self):
        self.assertEqual(normalize_tag_name("  #Boss  "), "Boss")
        self.assertEqual(normalize_tag_name(None), "")
        self.assertLessEqual(TAG_NAME_MAX_LENGTH, 24)


class TagPropagationTests(unittest.TestCase):
    def _session_with_tags(self, folder):
        session = TimestampSession(folder, "Tag Project", load_existing=False)
        first = session.create_timestamp(10, label="first", tags=["Kill", "idea"])
        second = session.create_timestamp(20, label="second", tags=["kill"])
        third = session.create_timestamp(30, label="third", tags=["bug"])
        return session, first, second, third

    def test_rename_tag_updates_entries_case_insensitively_and_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            session, first, second, _ = self._session_with_tags(folder)
            changed = session.rename_tag("KILL", "boss")

            self.assertEqual(changed, 2)
            self.assertEqual(first.tags, ["boss", "idea"])
            self.assertEqual(second.tags, ["boss"])

            reloaded = TimestampSession(folder, load_existing=True)
            self.assertEqual(reloaded.entries[0].tags, ["boss", "idea"])
            self.assertEqual(reloaded.entries[1].tags, ["boss"])

            markdown_path = os.path.join(folder, "Tag Project.md")
            with open(markdown_path, "r", encoding="utf-8") as handle:
                markdown = handle.read()
            self.assertIn("#boss", markdown)
            self.assertNotIn("#Kill", markdown)
            self.assertNotIn("#kill", markdown)

    def test_rename_tag_collapses_case_variant_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(5, tags=["Bug", "bug"])
            changed = session.rename_tag("bug", "BOSS")

            self.assertEqual(changed, 1)
            self.assertEqual(entry.tags, ["BOSS"])

    def test_rename_tag_unknown_name_changes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            session, first, second, third = self._session_with_tags(folder)
            before = [entry.tags for entry in session.entries]
            self.assertEqual(session.rename_tag("missing", "other"), 0)
            self.assertEqual(
                [entry.tags for entry in session.entries], before
            )

    def test_rename_tag_bypasses_recording_lock_and_keeps_label(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(5, label="keep me", tags=["kill"])
            session.mark_recording(entry)

            with self.assertRaisesRegex(ValueError, "recording"):
                session.update_entry(entry.id, "blocked", [])

            self.assertEqual(session.rename_tag("kill", "boss"), 1)
            self.assertEqual(entry.tags, ["boss"])
            self.assertEqual(entry.label, "keep me")
            self.assertEqual(entry.status, "recording")

    def test_rename_tag_requires_both_names(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            with self.assertRaises(ValueError):
                session.rename_tag("", "boss")
            with self.assertRaises(ValueError):
                session.rename_tag("kill", "   ")

    def test_remove_tag_strips_from_entries_and_rewrites_markdown(self):
        with tempfile.TemporaryDirectory() as folder:
            session, first, second, _ = self._session_with_tags(folder)
            changed = session.remove_tag("KILL")

            self.assertEqual(changed, 2)
            self.assertEqual(first.tags, ["idea"])
            self.assertEqual(second.tags, [])

            reloaded = TimestampSession(folder, load_existing=True)
            self.assertEqual(reloaded.entries[0].tags, ["idea"])
            self.assertEqual(reloaded.entries[1].tags, [])

            markdown_path = os.path.join(folder, "Tag Project.md")
            with open(markdown_path, "r", encoding="utf-8") as handle:
                markdown = handle.read()
            self.assertNotIn("#kill", markdown.lower())
            self.assertIn("#idea", markdown)

    def test_remove_tag_on_recording_row_is_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(5, label="locked label", tags=["kill"])
            session.mark_recording(entry)

            self.assertEqual(session.remove_tag("kill"), 1)
            self.assertEqual(entry.tags, [])
            self.assertEqual(entry.label, "locked label")

    def test_remove_tag_unknown_name_changes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            session, _, _, _ = self._session_with_tags(folder)
            self.assertEqual(session.remove_tag("missing"), 0)

    def test_count_entries_with_tag_is_case_insensitive(self):
        with tempfile.TemporaryDirectory() as folder:
            session, _, _, _ = self._session_with_tags(folder)
            self.assertEqual(session.count_entries_with_tag("kill"), 2)
            self.assertEqual(session.count_entries_with_tag("KILL"), 2)
            self.assertEqual(session.count_entries_with_tag("bug"), 1)
            self.assertEqual(session.count_entries_with_tag("missing"), 0)

    def test_tag_propagation_never_touches_audio_or_screenshot_files(self):
        with tempfile.TemporaryDirectory() as folder:
            session = TimestampSession(folder, load_existing=False)
            entry = session.create_timestamp(15, tags=["kill"])
            audio_path = os.path.join(folder, "001_00-00-15.wav")
            screenshot_path = os.path.join(folder, "Screenshots", "001_00-00-15.jpg")
            os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
            open(audio_path, "wb").close()
            open(screenshot_path, "wb").close()
            entry.audio_file = "001_00-00-15.wav"
            entry.screenshot_file = os.path.join("Screenshots", "001_00-00-15.jpg")
            session.save()

            session.rename_tag("kill", "boss")
            session.remove_tag("boss")

            self.assertTrue(os.path.isfile(audio_path))
            self.assertTrue(os.path.isfile(screenshot_path))
            reloaded = TimestampSession(folder, load_existing=True)
            self.assertEqual(reloaded.entries[0].audio_file, "001_00-00-15.wav")
            self.assertEqual(
                reloaded.entries[0].screenshot_file,
                os.path.join("Screenshots", "001_00-00-15.jpg"),
            )


class ReplayEntryTests(unittest.TestCase):
    REPLAY_NAME = "replay- [21-08][17-44-10]"

    def _project_session(self, folder):
        return TimestampSession(folder, "My Project", load_existing=False)

    def test_replay_entry_attaches_to_live_segment_with_next_index(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._project_session(folder)
            session.start_timer("[21-08][17-40-00]")
            first = session.create_timestamp(5)
            replay = session.create_replay_entry("E:/Vids/replay- [21-08][17-44-10].mp4")

            self.assertEqual(replay.kind, "replay")
            self.assertEqual(replay.recording_number, first.recording_number)
            self.assertEqual(replay.recording_index, first.recording_index + 1)
            self.assertTrue(replay.replay_file.endswith("replay- [21-08][17-44-10].mp4"))
            self.assertIsNone(replay.label)
            self.assertEqual(replay.tags, [])

    def test_replay_entry_while_locked_attaches_to_latest_segment(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._project_session(folder)
            session.start_timer("[21-08][10-00-00]")
            session.stop_timer()
            replay = session.create_replay_entry("E:/Vids/replay.mp4")
            self.assertEqual(replay.recording_number, 1)
            self.assertEqual(replay.recording_index, 1)

    def test_replay_entry_before_any_recording_is_ungrouped(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._project_session(folder)
            replay = session.create_replay_entry("E:/Vids/replay.mp4")
            self.assertIsNone(replay.recording_number)
            self.assertIsNone(replay.recording_index)

    def test_duplicate_replay_save_returns_existing_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._project_session(folder)
            first = session.create_replay_entry("E:/Vids/replay.mp4")
            second = session.create_replay_entry("E:/Vids/replay.mp4")
            self.assertIs(first, second)
            self.assertEqual(len(session.entries), 1)

    def test_empty_replay_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._project_session(folder)
            with self.assertRaisesRegex(ValueError, "path"):
                session.create_replay_entry("   ")

    def test_replay_entry_round_trips_through_save_and_load(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._project_session(folder)
            session.start_timer("[21-08][10-00-00]")
            replay = session.create_replay_entry("E:/Vids/replay.mp4")
            audio_path = os.path.join(folder, "R01-001_00-00-00.wav")
            session.mark_completed(replay, audio_path, 3.0)

            restored = TimestampSession(folder)
            loaded = restored.entries[0]
            self.assertEqual(loaded.kind, "replay")
            self.assertEqual(loaded.status, "completed")
            self.assertTrue(loaded.replay_file.endswith("replay.mp4"))
            self.assertEqual(loaded.audio_file, "R01-001_00-00-00.wav")

    def test_v5_payload_without_kind_loads_as_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            legacy = {
                "version": 5,
                "project_name": "Legacy",
                "started_at": 1000.0,
                "timer_running": False,
                "next_recording_number": 1,
                "recordings": [],
                "entries": [
                    {
                        "id": 1,
                        "elapsed_seconds": 12.0,
                        "created_at": "2024-01-01T00:00:00+00:00",
                        "status": "completed",
                    }
                ],
            }
            with open(os.path.join(folder, "session.json"), "w", encoding="utf-8") as handle:
                json.dump(legacy, handle)

            restored = TimestampSession(folder)
            self.assertEqual(restored.entries[0].kind, "timestamp")
            self.assertIsNone(restored.entries[0].replay_file)

    def test_unknown_kind_value_falls_back_to_timestamp(self):
        from timestamp_audio import TimestampEntry

        entry = TimestampEntry.from_dict(
            {"id": 1, "elapsed_seconds": 1.0, "created_at": "", "kind": "banana"}
        )
        self.assertEqual(entry.kind, "timestamp")

    def test_markdown_replay_line_has_name_state_and_footage_link(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._project_session(folder)
            session.start_timer("[21-08][17-40-00]")
            session.create_timestamp(5)  # plain timestamps render unchanged
            replay = session.create_replay_entry(
                "E:/Vids/replay- [21-08][17-44-10].mp4"
            )

            with open(os.path.join(folder, "My Project.md"), encoding="utf-8") as handle:
                markdown = handle.read()
            self.assertIn(
                "002 00:00:00 — 🎬 Replay: replay- [21-08][17-44-10], pending",
                markdown,
            )
            uri = replay_file_uri(replay.replay_file)
            self.assertIsNotNone(uri)
            self.assertIn(
                f"  - Footage: [replay- [21-08][17-44-10].mp4]({uri})",
                markdown,
            )
            # The plain timestamp line keeps its original shape.
            self.assertIn("001 00:00:05 — pending", markdown)
            self.assertNotIn("🎬 Replay", markdown.split("001 00:00:05")[0])

    def test_completed_replay_markdown_links_wav_in_lead(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._project_session(folder)
            session.start_timer("[21-08][10-00-00]")
            replay = session.create_replay_entry("E:/Vids/replay.mp4")
            audio_path = os.path.join(folder, "R01-001_00-00-00.wav")
            session.mark_completed(replay, audio_path, 3.0)
            session.update_entry(replay.id, "Nice play", ["kill"])

            with open(os.path.join(folder, "My Project.md"), encoding="utf-8") as handle:
                markdown = handle.read()
            self.assertIn("[00:00:00](R01-001_00-00-00.wav)", markdown)
            self.assertIn('🎬 Replay: replay, completed, 3.0s — "Nice play" #kill', markdown)
            self.assertIn("  - Footage: [replay.mp4](", markdown)

    def test_replay_audio_path_follows_segment_naming(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._project_session(folder)
            session.start_timer("[21-08][10-00-00]")
            session.create_timestamp(5)
            replay = session.create_replay_entry("E:/Vids/replay.mp4")
            self.assertTrue(session.audio_path(replay).endswith("R01-002_00-00-00.wav"))

    def test_replay_update_and_remove_reject_active_recording(self):
        with tempfile.TemporaryDirectory() as folder:
            session = self._project_session(folder)
            replay = session.create_replay_entry("E:/Vids/replay.mp4")
            session.mark_recording(replay)
            with self.assertRaisesRegex(ValueError, "recording"):
                session.update_entry(replay.id, "nope")
            with self.assertRaisesRegex(ValueError, "recording"):
                session.update_transcript(replay.id, "nope")
            with self.assertRaisesRegex(ValueError, "recording"):
                session.remove_entry(replay.id)
            self.assertEqual(len(session.entries), 1)

    @unittest.skipUnless(sys.platform == "win32", "drive-letter URI form")
    def test_replay_file_uri_windows_form(self):
        self.assertEqual(
            replay_file_uri("E:/Vids/my replay.mp4"),
            "file:///E:/Vids/my%20replay.mp4",
        )

    def test_replay_file_uri_generic_properties(self):
        uri = replay_file_uri("E:/Vids/my replay.mp4")
        if sys.platform == "win32":
            self.assertTrue(uri.startswith("file:///"))
        self.assertTrue(uri.endswith("my%20replay.mp4"))

    def test_replay_file_uri_handles_missing_input(self):
        self.assertIsNone(replay_file_uri(None))
        self.assertIsNone(replay_file_uri(""))

    def test_replay_display_name_strips_extension(self):
        self.assertEqual(
            replay_display_name("E:/Vids/replay- [21-08][17-44-10].mp4"),
            "replay- [21-08][17-44-10]",
        )
        self.assertEqual(replay_display_name(None), "Unknown replay")
        self.assertEqual(replay_display_name(""), "Unknown replay")


if __name__ == "__main__":
    unittest.main()
