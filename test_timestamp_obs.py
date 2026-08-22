"""Dependency-light tests for the OBS WebSocket manager.

timestamp_obs imports obsws_python lazily inside its connect thread, so these
tests exercise OBSManager state machines with fake client objects shaped like
the installed library versions.
"""

import unittest
from types import SimpleNamespace

from timestamp_obs import OBSManager


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


if __name__ == "__main__":
    unittest.main()
