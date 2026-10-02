"""Dependency-light tests for the OBS WebSocket manager.

timestamp_obs imports obsws_python lazily inside its connect thread, so these
tests exercise OBSManager state machines with fake client objects shaped like
the installed library versions.
"""

import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from timestamp_obs import (
    AITUM_VERTICAL_RECORD_OUTPUT,
    AITUM_VERTICAL_VENDOR,
    OBSManager,
    collect_backtrack_candidates,
    pick_backtrack_video_file,
    parse_backtrack_save_time,
    pick_recent_video_file,
)


def vendor_event(event_type, event_data=None, vendor=AITUM_VERTICAL_VENDOR):
    """Payload shape obsws-python >= 1.x hands to on_vendor_event."""
    return SimpleNamespace(
        vendor_name=vendor,
        event_type=event_type,
        event_data=event_data if event_data is not None else {},
    )


class FakeWebSocket:
    def __init__(self, connected=True):
        self.connected = connected


class FakeBaseClient:
    def __init__(self, ws):
        self.ws = ws


class ModernEventClient:
    """Shape of obsws-python >= 1.x: composition via ``base_client``."""

    def __init__(self, connected=True):
        self.base_client = FakeBaseClient(FakeWebSocket(connected))


class LegacyEventClient:
    """Shape of old obsws-python releases: ``ws`` directly on the client."""

    def __init__(self, connected=True):
        self.ws = FakeWebSocket(connected)


class UnknownEventClient:
    """No recognizable WebSocket attribute anywhere."""

    pass


class EventSocketAliveTests(unittest.TestCase):
    def setUp(self):
        self.manager = OBSManager()

    def _with_event_client(self, client):
        self.manager._event_client = client

    def test_modern_shape_connected_socket_is_alive(self):
        self._with_event_client(ModernEventClient(connected=True))
        self.assertTrue(self.manager._event_socket_alive())

    def test_modern_shape_closed_socket_is_dead(self):
        self._with_event_client(ModernEventClient(connected=False))
        self.assertFalse(self.manager._event_socket_alive())

    def test_legacy_shape_still_resolves(self):
        self._with_event_client(LegacyEventClient(connected=True))
        self.assertTrue(self.manager._event_socket_alive())
        self._with_event_client(LegacyEventClient(connected=False))
        self.assertFalse(self.manager._event_socket_alive())

    def test_unknown_library_shape_fails_open_as_alive(self):
        # A future library layout must never make the watchdog tear down a
        # healthy connection every cycle (the v2.6.1 reconnect-churn bug).
        self._with_event_client(UnknownEventClient())
        self.assertTrue(self.manager._event_socket_alive())

    def test_websocket_without_connected_flag_fails_open(self):
        client = SimpleNamespace(base_client=FakeBaseClient(object()))
        self._with_event_client(client)
        self.assertTrue(self.manager._event_socket_alive())

    def test_no_event_client_is_dead(self):
        self.assertFalse(self.manager._event_socket_alive())


class RecordStateTransitionTests(unittest.TestCase):
    """The started/stopped callbacks must fire on transitions only."""

    def setUp(self):
        self.manager = OBSManager()
        self.events = []
        self.manager.register_callbacks(
            on_recording_started=lambda path=None: self.events.append(("started", path)),
            on_recording_stopped=lambda path=None: self.events.append(("stopped", path)),
        )

    @staticmethod
    def record_event(state, path="E:/rec/clip.mp4"):
        return SimpleNamespace(output_state=state, output_path=path)

    def test_started_when_inactive_fires_once_with_path(self):
        self.manager.on_record_state_changed(
            self.record_event("OBS_WEBSOCKET_OUTPUT_STARTED")
        )
        self.assertEqual(self.events, [("started", "E:/rec/clip.mp4")])
        self.assertTrue(self.manager.is_recording)

    def test_started_while_already_active_does_not_refire(self):
        self.manager.on_record_state_changed(
            self.record_event("OBS_WEBSOCKET_OUTPUT_STARTED")
        )
        self.manager.on_record_state_changed(
            self.record_event("OBS_WEBSOCKET_OUTPUT_STARTED")
        )
        self.assertEqual(len(self.events), 1)

    def test_stopped_only_fires_when_active(self):
        self.manager.on_record_state_changed(
            self.record_event("OBS_WEBSOCKET_OUTPUT_STOPPED")
        )
        self.assertEqual(self.events, [])

        self.manager.on_record_state_changed(
            self.record_event("OBS_WEBSOCKET_OUTPUT_STARTED")
        )
        self.manager.on_record_state_changed(
            self.record_event("OBS_WEBSOCKET_OUTPUT_STOPPED")
        )
        self.assertEqual(
            self.events,
            [("started", "E:/rec/clip.mp4"), ("stopped", "E:/rec/clip.mp4")],
        )
        self.assertFalse(self.manager.is_recording)

    def test_events_without_path_forward_none(self):
        self.manager.on_record_state_changed(
            self.record_event("OBS_WEBSOCKET_OUTPUT_STARTED", path=None)
        )
        self.assertEqual(self.events, [("started", None)])


class FakeReqClient:
    """Minimal ReqClient fake for path/status resolution tests.

    ``output_settings`` maps output names to response payloads;
    ``raise_error`` makes every call raise (plugin absent / protocol error).
    """

    def __init__(self, output_settings=None, outputs=None, raise_error=False):
        self.output_settings = output_settings or {}
        self.outputs = outputs or []
        self.raise_error = raise_error
        self.calls = []

    def get_output_settings(self, name):
        self.calls.append(("get_output_settings", name))
        if self.raise_error:
            raise RuntimeError("protocol error")
        return self.output_settings.get(name, {})

    def get_output_list(self):
        self.calls.append(("get_output_list", None))
        if self.raise_error:
            raise RuntimeError("protocol error")
        return SimpleNamespace(outputs=self.outputs)

    def call_vendor_request(self, vendor, request_type, request_data=None):
        self.calls.append(("call_vendor_request", vendor, request_type))
        if self.raise_error:
            raise RuntimeError("vendor not found")
        return SimpleNamespace(response_data={"success": True, "recording": False})


    @staticmethod
    def output_settings_response(path):
        return SimpleNamespace(
            output_active=True, output_settings={"path": path, "directory": os.path.dirname(path)}
        )


def write_file(path, mtime=None):
    with open(path, "wb") as f:
        f.write(b"x")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class VendorEventTests(unittest.TestCase):
    """Aitum Vertical vendor events drive the combined recording callbacks."""

    def setUp(self):
        self.manager = OBSManager()
        self.events = []
        self.manager.register_callbacks(
            on_recording_started=lambda path=None: self.events.append(("started", path)),
            on_recording_stopped=lambda path=None: self.events.append(("stopped", path)),
            on_replay_saved=lambda path=None: self.events.append(("replay", path)),
        )

    def test_recording_started_and_stopped_fire_callbacks(self):
        self.manager.on_vendor_event(vendor_event("recording_started"))
        self.manager.on_vendor_event(vendor_event("recording_stopped"))
        self.assertEqual(
            self.events, [("started", None), ("stopped", None)]
        )
        self.assertFalse(self.manager.is_recording)

    def test_recording_started_resolves_output_path(self):
        self.manager._req_client = FakeReqClient(
            output_settings={
                AITUM_VERTICAL_RECORD_OUTPUT: FakeReqClient.output_settings_response(
                    "E:/rec/2026-06-15 14-02-33-vertical.mp4"
                )
            }
        )
        self.manager.on_vendor_event(vendor_event("recording_started"))
        self.assertEqual(self.events, [("started", "E:/rec/2026-06-15 14-02-33-vertical.mp4")])

    def test_recording_stopped_backfills_path(self):
        # No query succeeded at start; the stop event still names the segment.
        self.manager.on_vendor_event(vendor_event("recording_started"))
        self.manager._req_client = FakeReqClient(
            output_settings={
                AITUM_VERTICAL_RECORD_OUTPUT: FakeReqClient.output_settings_response(
                    "E:/rec/clip-vertical.mp4"
                )
            }
        )
        self.manager.on_vendor_event(vendor_event("recording_stopped"))
        self.assertEqual(
            self.events, [("started", None), ("stopped", "E:/rec/clip-vertical.mp4")]
        )

    def test_other_vendors_ignored(self):
        self.manager.on_vendor_event(vendor_event("recording_started", vendor="other-plugin"))
        self.assertEqual(self.events, [])
        self.assertFalse(self.manager.is_recording)

    def test_unrelated_event_types_ignored(self):
        for event_type in (
            "recording_starting",
            "recording_stopping",
            "streaming_started",
            "virtual_camera_started",
            "switch_scene",
        ):
            self.manager.on_vendor_event(vendor_event(event_type))
        self.assertEqual(self.events, [])
        self.assertFalse(self.manager.is_recording)

    def test_malformed_payloads_never_raise(self):
        for payload in (None, SimpleNamespace(), {}, {"vendorName": 3}, "junk"):
            try:
                self.manager.on_vendor_event(payload)
            except Exception as exc:  # pragma: no cover - failure path
                self.fail(f"on_vendor_event raised on {payload!r}: {exc}")
        self.assertEqual(self.events, [])

    def test_dict_payload_shape_supported(self):
        self.manager.on_vendor_event(
            {
                "vendorName": AITUM_VERTICAL_VENDOR,
                "eventType": "recording_started",
                "eventData": {},
            }
        )
        self.assertEqual(self.events, [("started", None)])

    def test_path_resolution_failure_still_fires(self):
        self.manager._req_client = FakeReqClient(raise_error=True)
        self.manager.on_vendor_event(vendor_event("recording_started"))
        self.assertEqual(self.events, [("started", None)])
        self.assertTrue(self.manager.is_recording)

    def test_backtrack_saved_fires_replay_callback_with_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "Replay Buffer"))
            clip_time = datetime.now() - timedelta(seconds=2)
            new_clip = write_file(
                os.path.join(
                    tmp, "Replay Buffer", clip_time.strftime("Backtrack [%d-%m][%H-%M-%S].mp4")
                ),
                time.time() - 1,
            )
            # The recording output's own file sits in the parent folder with a
            # fresh mtime while recording — it must never be picked.
            write_file(os.path.join(tmp, "[02-10][10-37-54]-vertical.mp4"), time.time() - 1)
            write_file(os.path.join(tmp, "old-backtrack-vertical.mp4"), time.time() - 3600)
            self.manager._req_client = FakeReqClient(
                outputs=[{"outputName": "Vertical Backtrack", "outputKind": "replay_buffer"}],
                output_settings={
                    "Vertical Backtrack": SimpleNamespace(
                        output_settings={"directory": tmp}
                    )
                },
            )
            self.manager.on_vendor_event(vendor_event("backtrack_saved"))
        self.assertEqual(self.events, [("replay", new_clip)])

    def test_backtrack_saved_with_only_recording_file_fires_none(self):
        # Without a qualifying Backtrack clip (clip not yet written / renamed),
        # an actively-written vertical recording is excluded instead of being
        # mis-resolved as the saved replay.
        with tempfile.TemporaryDirectory() as tmp:
            write_file(os.path.join(tmp, "[02-10][10-46-20]-vertical.mp4"), time.time())
            self.manager._req_client = FakeReqClient(
                outputs=[{"outputName": "Vertical Backtrack", "outputKind": "replay_buffer"}],
                output_settings={
                    "Vertical Backtrack": SimpleNamespace(
                        output_settings={"directory": tmp}
                    )
                },
            )
            self.manager.on_vendor_event(vendor_event("backtrack_saved"))
        self.assertEqual(self.events, [("replay", None)])

    def test_backtrack_without_resolvable_path_fires_none(self):
        self.manager._req_client = FakeReqClient(raise_error=True)
        self.manager.on_vendor_event(vendor_event("backtrack_saved"))
        self.assertEqual(self.events, [("replay", None)])


class CombinedRecordingTests(unittest.TestCase):
    """Main and vertical recordings merge into one started/stopped session."""

    def setUp(self):
        self.manager = OBSManager()
        self.events = []
        self.manager.register_callbacks(
            on_recording_started=lambda path=None: self.events.append(("started", path)),
            on_recording_stopped=lambda path=None: self.events.append(("stopped", path)),
        )

    def test_vertical_start_during_main_recording_does_not_refire(self):
        self.manager.on_record_state_changed(self.record_started("main.mp4"))
        self.manager.on_vendor_event(vendor_event("recording_started"))
        self.assertEqual(self.events, [("started", "main.mp4")])
        self.assertTrue(self.manager.is_recording)

    def test_vertical_stop_keeps_timer_while_main_still_records(self):
        self.manager.on_record_state_changed(self.record_started("main.mp4"))
        self.manager.on_vendor_event(vendor_event("recording_started"))
        self.manager.on_vendor_event(vendor_event("recording_stopped"))
        self.assertEqual(self.events, [("started", "main.mp4")])
        self.assertTrue(self.manager.is_recording)

    def test_main_stop_ends_combined_session(self):
        self.manager.on_record_state_changed(self.record_started("main.mp4"))
        self.manager.on_vendor_event(vendor_event("recording_started"))
        self.manager.on_vendor_event(vendor_event("recording_stopped"))
        self.manager.on_record_state_changed(self.record_stopped("main.mp4"))
        self.assertEqual(
            self.events,
            [("started", "main.mp4"), ("stopped", "main.mp4")],
        )
        self.assertFalse(self.manager.is_recording)

    def test_reverse_order_main_within_vertical(self):
        self.manager.on_vendor_event(vendor_event("recording_started"))
        self.manager.on_record_state_changed(self.record_started("main.mp4"))
        self.assertEqual(self.events, [("started", None)])
        self.manager.on_record_state_changed(self.record_stopped("main.mp4"))
        self.assertEqual(self.events, [("started", None)])
        self.assertTrue(self.manager.is_recording)
        self.manager.on_vendor_event(vendor_event("recording_stopped"))
        self.assertEqual(
            self.events, [("started", None), ("stopped", None)]
        )
        self.assertFalse(self.manager.is_recording)

    def test_duplicate_vertical_events_are_noops(self):
        self.manager.on_vendor_event(vendor_event("recording_started"))
        self.manager.on_vendor_event(vendor_event("recording_started"))
        self.assertEqual(self.events, [("started", None)])
        self.manager.on_vendor_event(vendor_event("recording_stopped"))
        self.manager.on_vendor_event(vendor_event("recording_stopped"))
        self.assertEqual(self.events, [("started", None), ("stopped", None)])

    def test_is_recording_reflects_vertical_only_session(self):
        self.assertFalse(self.manager.is_recording)
        self.manager.on_vendor_event(vendor_event("recording_started"))
        self.assertTrue(self.manager.is_recording)

    def test_teardown_resets_vertical_state(self):
        self.manager.on_vendor_event(vendor_event("recording_started"))
        self.assertTrue(self.manager.is_recording)
        self.manager._teardown_clients()
        self.assertFalse(self.manager.is_recording)

    # Convenience accessors for the class-level helpers defined below.
    @staticmethod
    def record_started(path):
        return SimpleNamespace(output_state="OBS_WEBSOCKET_OUTPUT_STARTED", output_path=path)

    @staticmethod
    def record_stopped(path):
        return SimpleNamespace(output_state="OBS_WEBSOCKET_OUTPUT_STOPPED", output_path=path)


class VerticalStateSyncTests(unittest.TestCase):
    """Connect-time vendor status query restores vertical recording state."""

    def setUp(self):
        self.manager = OBSManager()
        self.events = []
        self.manager.register_callbacks(
            on_recording_started=lambda path=None: self.events.append(("started", path)),
            on_recording_stopped=lambda path=None: self.events.append(("stopped", path)),
        )

    def _run_connect_body(self, req, event):  # mirrors _connect_thread's tail
        # Call the query helper directly; the thread body needs a live
        # obsws_python import, so exercise its decision logic in isolation.
        vertical = self.manager._query_vertical_recording(req)
        self.manager._recording_active = False
        self.manager._vertical_recording_active = bool(vertical) if vertical else False
        if self.manager.is_recording:
            self.manager._combined_recording_active = True
            self.manager._fire(self.manager._on_recording_started)

    def test_vendor_status_reports_recording(self):
        req = SimpleNamespace(
            call_vendor_request=lambda vendor, rt, rd=None: SimpleNamespace(
                response_data={"success": True, "recording": True}
            )
        )
        self._run_connect_body(req, None)
        self.assertTrue(self.manager._vertical_recording_active)
        self.assertTrue(self.manager.is_recording)
        self.assertEqual(self.events, [("started", None)])

    def test_vendor_status_not_recording(self):
        req = SimpleNamespace(
            call_vendor_request=lambda vendor, rt, rd=None: SimpleNamespace(
                response_data={"success": True, "recording": False}
            )
        )
        self._run_connect_body(req, None)
        self.assertFalse(self.manager._vertical_recording_active)
        self.assertEqual(self.events, [])

    def test_plain_response_shape_supported(self):
        req = SimpleNamespace(
            call_vendor_request=lambda vendor, rt, rd=None: SimpleNamespace(success=True, recording=True)
        )
        vertical = self.manager._query_vertical_recording(req)
        self.assertTrue(vertical)

    def test_vendor_absent_degrades_to_false(self):
        def boom(vendor, rt, rd=None):
            raise RuntimeError("vendor not found")

        req = SimpleNamespace(call_vendor_request=boom)
        vertical = self.manager._query_vertical_recording(req)
        self.assertIsNone(vertical)
        self.assertFalse(self.manager._vertical_recording_active)
        self.assertEqual(self.events, [])

    def test_missing_call_vendor_request_method_degrades(self):
        vertical = self.manager._query_vertical_recording(SimpleNamespace())
        self.assertIsNone(vertical)

    def test_unrecognized_response_shape_degrades(self):
        req = SimpleNamespace(
            call_vendor_request=lambda vendor, rt, rd=None: SimpleNamespace(nonsense=True)
        )
        vertical = self.manager._query_vertical_recording(req)
        self.assertIsNone(vertical)


class PickRecentVideoFileTests(unittest.TestCase):
    """The pure newest-recent-video-file selector used for Backtrack saves."""

    def setUp(self):
        self.now = time.time()

    def test_newest_recent_wins(self):
        entries = [
            (self.now - 100, "a.mp4"),
            (self.now - 2, "b.mp4"),
            (self.now - 5, "c.mkv"),
        ]
        self.assertEqual(pick_recent_video_file(entries, self.now), "b.mp4")

    def test_window_filters_old_files(self):
        entries = [(self.now - 60, "old.mp4"), (self.now - 3600, "ancient.mp4")]
        self.assertIsNone(pick_recent_video_file(entries, self.now))

    def test_non_video_extensions_ignored(self):
        entries = [(self.now - 2, "notes.txt"), (self.now - 1, "pic.jpg")]
        self.assertIsNone(pick_recent_video_file(entries, self.now))

    def test_extension_is_case_insensitive(self):
        entries = [(self.now - 3, "CLIP.MP4")]
        self.assertEqual(pick_recent_video_file(entries, self.now), "CLIP.MP4")

    def test_slightly_future_mtime_accepted(self):
        entries = [(self.now + 2, "skew.mp4")]
        self.assertEqual(pick_recent_video_file(entries, self.now), "skew.mp4")

    def test_absurd_future_mtime_rejected(self):
        entries = [(self.now + 3600, "future.mp4")]
        self.assertIsNone(pick_recent_video_file(entries, self.now))

    def test_empty_and_none_mtime_entries(self):
        self.assertIsNone(pick_recent_video_file([], self.now))
        self.assertIsNone(pick_recent_video_file([(None, "a.mp4")], self.now))

    def test_all_supported_video_extensions_count(self):
        for i, ext in enumerate((".mp4", ".mkv", ".mov", ".ts", ".avi", ".flv", ".webm")):
            entries = [(self.now - i - 1, f"file{ext}")]
            self.assertEqual(pick_recent_video_file(entries, self.now), entries[0][1])


class ParseBacktrackSaveTimeTests(unittest.TestCase):
    """The [DD-MM][HH-MM-SS] save-time parser for Backtrack file names."""

    def setUp(self):
        self.now = datetime(2026, 10, 2, 10, 40, 0)

    def test_valid_name_parses_to_current_year(self):
        parsed = parse_backtrack_save_time("Backtrack [02-10][10-38-03].mp4", self.now)
        self.assertEqual(parsed, datetime(2026, 10, 2, 10, 38, 3))

    def test_prefix_and_extension_not_required(self):
        parsed = parse_backtrack_save_time("clip [02-10][10-38-03].mkv", self.now)
        self.assertEqual(parsed, datetime(2026, 10, 2, 10, 38, 3))

    def test_name_without_bracket_time_returns_none(self):
        self.assertIsNone(parse_backtrack_save_time("recording 2026-10-02.mp4", self.now))
        self.assertIsNone(parse_backtrack_save_time("old-backtrack-vertical.mp4", self.now))

    def test_invalid_date_values_return_none(self):
        self.assertIsNone(parse_backtrack_save_time("Backtrack [13-13][10-38-03].mp4", self.now))
        self.assertIsNone(parse_backtrack_save_time("Backtrack [02-10][10-38-61].mp4", self.now))

    def test_rollover_picks_closest_adjacent_year(self):
        new_years = datetime(2026, 1, 1, 0, 0, 5)
        parsed = parse_backtrack_save_time("Backtrack [31-12][23-59-58].mp4", new_years)
        self.assertEqual(parsed, datetime(2025, 12, 31, 23, 59, 58))


class PickBacktrackVideoFileTests(unittest.TestCase):
    """The combined name-time selector for vertical Backtrack saves."""

    def setUp(self):
        self.now = datetime(2026, 10, 2, 10, 40, 0)
        self.name_time = "Backtrack [02-10][10-39-58].mp4"
        self.embedded = datetime(2026, 10, 2, 10, 39, 58)

    def test_pattern_file_beats_newer_mtime_non_pattern_file(self):
        mtime = self.now.timestamp()
        entries = [
            (mtime - 2.0, "fresh-renamed.mp4"),  # newest mtime, unpatterned name
            (mtime - 10.0, self.name_time),      # embedded time 2 s old
        ]
        self.assertEqual(pick_backtrack_video_file(entries, self.now), self.name_time)

    def test_newest_embedded_time_wins(self):
        mtime = self.now.timestamp()
        entries = [
            (mtime - 12.0, "Backtrack [02-10][10-39-50].mp4"),
            (mtime - 2.0, self.name_time),
        ]
        self.assertEqual(pick_backtrack_video_file(entries, self.now), self.name_time)

    def test_slightly_future_embedded_time_accepted(self):
        name = "Backtrack [02-10][10-40-02].mp4"  # 2 s in the future
        entries = [(0.0, name)]
        self.assertEqual(pick_backtrack_video_file(entries, self.now), name)

    def test_absurd_future_embedded_time_rejected(self):
        name = "Backtrack [02-10][12-40-02].mp4"  # ~2 h ahead, not clock skew
        entries = [(self.now.timestamp(), name)]
        self.assertIsNone(pick_backtrack_video_file(entries, self.now))

    def test_no_pattern_matches_falls_back_to_mtime(self):
        entries = [(self.now.timestamp() - 3.0, "b.mp4"), (self.now.timestamp() - 5.0, "c.mkv")]
        self.assertEqual(pick_backtrack_video_file(entries, self.now), "b.mp4")
        self.assertEqual(
            pick_backtrack_video_file(entries, self.now),
            pick_recent_video_file(entries, self.now.timestamp()),
        )

    def test_non_video_and_missing_pattern_entries_ignored(self):
        entries = [(self.now.timestamp() - 1.0, "Backtrack [02-10][10-39-58].txt")]
        self.assertIsNone(pick_backtrack_video_file(entries, self.now))
        self.assertIsNone(pick_backtrack_video_file([], self.now))

    def test_pattern_outside_window_falls_back_to_mtime(self):
        name = "Backtrack [02-10][10-30-00].mp4"  # ~10 min old name-time
        entries = [(self.now.timestamp() - 3.0, "renamed.mp4"), (1.0, name)]
        self.assertEqual(pick_backtrack_video_file(entries, self.now), "renamed.mp4")

    def test_bracket_time_without_backtrack_prefix_is_mtime_pool(self):
        # A custom-template clip with a bracket time but no Backtrack prefix
        # never gets the pattern pool's preferential treatment.
        mtime = self.now.timestamp()
        entries = [
            (mtime - 1.0, "clip [02-10][10-39-59].mp4"),   # newer bracket time
            (mtime - 5.0, "Backtrack [02-10][10-39-55].mp4"),
        ]
        self.assertEqual(
            pick_backtrack_video_file(entries, self.now),
            "Backtrack [02-10][10-39-55].mp4",
        )

    def test_vertical_recording_file_never_qualifies(self):
        # The recording file (name suffix "-vertical") embeds its own bracket
        # time and keeps a fresh mtime while recording — it must be excluded
        # from both the pattern and the mtime pools.
        rec = "[02-10][10-39-30]-vertical.mp4"
        entries = [
            (self.now.timestamp() - 1.0, rec),
            (self.now.timestamp() - 10.0, self.name_time),
        ]
        self.assertEqual(pick_backtrack_video_file(entries, self.now), self.name_time)
        self.assertIsNone(pick_backtrack_video_file([(0.0, rec)], self.now))
        self.assertIsNone(
            pick_backtrack_video_file(
                [(self.now.timestamp() - 1.0, rec)], self.now
            )
        )

    def test_exclude_paths_drops_recording_file_from_all_pools(self):
        rec = "custom-recording-name.mp4"
        entries = [
            (self.now.timestamp() - 1.0, rec),
            (self.now.timestamp() - 10.0, self.name_time),
        ]
        self.assertEqual(
            pick_backtrack_video_file(entries, self.now, exclude_paths=(rec,)),
            self.name_time,
        )
        self.assertIsNone(
            pick_backtrack_video_file([(0.0, rec)], self.now, exclude_paths=(rec,))
        )


class CollectBacktrackCandidatesTests(unittest.TestCase):
    """The depth-limited recursive scan for Backtrack save folders."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def write(self, relpath, content=b"x"):
        path = os.path.join(self.root, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content)
        return path

    def test_finds_clips_in_subfolders_and_skips_non_video(self):
        clip = self.write(os.path.join("Replay Buffer", "Backtrack [02-10][10-38-03].mp4"))
        rec = self.write("[02-10][10-37-54]-vertical.mp4")
        self.write("notes.txt")
        paths = {os.path.normcase(path) for _, path in collect_backtrack_candidates(self.root)}
        self.assertEqual(paths, {os.path.normcase(clip), os.path.normcase(rec)})

    def test_depth_limit_bounds_the_walk(self):
        self.write(os.path.join("s1", "s2", "s3", "deep.mp4"))
        self.assertEqual(collect_backtrack_candidates(self.root, max_depth=3), [])
        self.write(os.path.join("s1", "ok.mp4"))
        found = collect_backtrack_candidates(self.root, max_depth=3)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0][1].endswith("ok.mp4"))

    def test_extension_case_insensitive_and_empty_dir(self):
        self.write("Clip.MP4")
        found = collect_backtrack_candidates(self.root)
        self.assertEqual(len(found), 1)
        self.assertEqual(collect_backtrack_candidates(os.path.join(self.root, "s1")), [])


if __name__ == "__main__":
    unittest.main()
