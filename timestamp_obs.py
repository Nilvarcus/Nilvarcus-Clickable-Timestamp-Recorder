"""
timestamp_obs.py — OBS Studio Integration for the Nilvarcus Timestamp App.

Handles:
  - WebSocket connection management (obs-websocket v5 via obsws-python)
  - Auto-sync: start/stop stopwatch when OBS recording starts/stops
  - Scene markers: log scene transitions to the active timestamp file
  - Replay buffer: trigger OBS save (log entry handled by GUI via save_short)

Requires: pip install obsws-python
OBS Setup: Tools → OBS WebSocket Settings → Enable (port 4455)
"""

import threading


class OBSManager:
    """Self-contained OBS WebSocket manager.

    All heavy logic (file writes, GUI updates) is delegated to the GUI layer
    via registered callbacks, keeping this module free of tkinter dependencies.
    """

    def __init__(self, timestamp_manager):
        self.timestamp_manager = timestamp_manager

        self._req_client = None
        self._event_client = None
        self._connected = False
        self._lock = threading.Lock()

        # GUI callbacks — set via register_callbacks()
        self._on_status_change = None      # (status_str: str) → None
        self._on_scene_change = None       # (scene_name: str) → None
        self._on_replay_saved = None       # () → None
        self._on_recording_started = None  # () → None
        self._on_recording_stopped = None  # () → None

    # ── Public API ──────────────────────────────────────────────────────────

    def register_callbacks(
        self,
        on_status_change=None,
        on_scene_change=None,
        on_replay_saved=None,
        on_recording_started=None,
        on_recording_stopped=None,
    ):
        """Register GUI callbacks. All are optional."""
        self._on_status_change = on_status_change
        self._on_scene_change = on_scene_change
        self._on_replay_saved = on_replay_saved
        self._on_recording_started = on_recording_started
        self._on_recording_stopped = on_recording_stopped

    @property
    def is_connected(self):
        return self._connected

    def connect(self, host="localhost", port=4455, password=""):
        """Start a connection attempt in a background thread (non-blocking)."""
        threading.Thread(
            target=self._connect_thread,
            args=(host, port, password),
            daemon=True,
        ).start()

    def disconnect(self):
        """Cleanly disconnect from OBS WebSocket."""
        try:
            if self._event_client:
                self._event_client.disconnect()
                self._event_client = None
            if self._req_client:
                self._req_client.disconnect()
                self._req_client = None
        except Exception as e:
            print(f"[OBS] Disconnect error: {e}")
        finally:
            with self._lock:
                self._connected = False
            self._fire(self._on_status_change, "disconnected")

    def test_connection(self, host, port, password):
        """
        Test a connection synchronously.
        Returns (True, 'OBS X.Y.Z') on success or (False, 'error message').
        """
        try:
            import obsws_python as obs
            client = obs.ReqClient(host=host, port=int(port), password=password, timeout=3)
            version = client.get_version()
            client.disconnect()
            return True, f"OBS {version.obs_version}"
        except Exception as e:
            return False, str(e)

    def save_replay_buffer(self):
        """
        Tell OBS to save the replay buffer.
        The log entry (SHORT marker) is handled by the GUI via save_short().
        Returns True on success, False if not connected or on error.
        """
        if not self._connected or not self._req_client:
            print("[OBS] Not connected — cannot save replay buffer.")
            return False
        try:
            self._req_client.save_replay_buffer()
            self._fire(self._on_replay_saved)
            return True
        except Exception as e:
            print(f"[OBS] Replay buffer error: {e}")
            return False

    def start_obs_recording(self):
        """Command OBS to start recording."""
        if not self._connected or not self._req_client:
            return False
        try:
            self._req_client.start_record()
            return True
        except Exception as e:
            print(f"[OBS] Start recording error: {e}")
            return False

    def stop_obs_recording(self):
        """Command OBS to stop recording."""
        if not self._connected or not self._req_client:
            return False
        try:
            self._req_client.stop_record()
            return True
        except Exception as e:
            print(f"[OBS] Stop recording error: {e}")
            return False

    # ── Internal: connection ─────────────────────────────────────────────────

    def _connect_thread(self, host, port, password):
        self._fire(self._on_status_change, "connecting")
        try:
            import obsws_python as obs

            # Request client — used to send commands to OBS
            req = obs.ReqClient(host=host, port=int(port), password=password, timeout=5)

            # Event client — listens for OBS events
            ev = obs.EventClient(host=host, port=int(port), password=password)
            ev.callback.register([
                self.on_record_state_changed,
                self.on_current_program_scene_changed,
            ])

            with self._lock:
                self._req_client = req
                self._event_client = ev
                self._connected = True

            self._fire(self._on_status_change, "connected")
            print("[OBS] Connected successfully.")

        except Exception as e:
            print(f"[OBS] Connection failed: {e}")
            with self._lock:
                self._connected = False
            self._fire(self._on_status_change, f"error:{e}")

    # ── Internal: OBS event handlers ─────────────────────────────────────────

    def on_record_state_changed(self, data):
        """
        Fires callbacks when OBS recording starts or stops.
        All actual start/stop logic runs on the GUI main thread via the callback.
        No direct calls to TimestampManager here — that avoids threading issues.
        """
        state = data.output_state
        print(f"[OBS] Record state: {state}")

        if state == "OBS_WEBSOCKET_OUTPUT_STARTED":
            self._fire(self._on_recording_started)

        elif state in ("OBS_WEBSOCKET_OUTPUT_STOPPED", "OBS_WEBSOCKET_OUTPUT_STOPPING"):
            self._fire(self._on_recording_stopped)

    def on_current_program_scene_changed(self, data):
        """
        Writes a scene marker to the log file, then notifies the GUI to refresh.
        File I/O is safe from this thread; GUI refresh goes via callback.
        """
        scene_name = data.scene_name
        tm = self.timestamp_manager

        # Only log if a recording session is active
        if tm.current_file_path and tm.stopwatch_running:
            line = f"\n📺  **Scene →** {scene_name}"
            try:
                with open(tm.current_file_path, "a", encoding="utf-8") as f:
                    f.write(line)
                self._fire(self._on_scene_change, scene_name)
            except Exception as e:
                print(f"[OBS] Scene marker write error: {e}")

    # ── Internal: helpers ────────────────────────────────────────────────────

    def _fire(self, callback, *args):
        """Safely invoke a callback (ignores None)."""
        if callback:
            try:
                callback(*args)
            except Exception as e:
                print(f"[OBS] Callback error: {e}")
