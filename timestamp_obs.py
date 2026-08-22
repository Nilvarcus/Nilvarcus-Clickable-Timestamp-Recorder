"""Minimal OBS WebSocket integration for the Clickable Timestamp Recorder."""

from __future__ import annotations

import threading


class OBSManager:
    """Connect to OBS and report the main recording output state.

    An optional watchdog thread (``enable_auto_reconnect``) keeps the
    connection alive across OBS restarts: while enabled it retries the
    connection every few seconds until OBS's WebSocket server answers, and
    detects a connection that died because OBS exited, so the app reconnects
    automatically the next time OBS starts.
    """

    def __init__(self):
        self._req_client = None
        self._event_client = None
        self._connected = False
        self._recording_active = False
        self._replay_buffer_active: bool | None = None
        self._lock = threading.Lock()

        self._on_status_change = None
        self._on_recording_started = None
        self._on_recording_stopped = None
        self._on_replay_saved = None

        # Auto-reconnect watchdog state.
        self._auto_reconnect_enabled = False
        self._reconnect_interval = 5.0
        self._connection_params = ("localhost", 4455, "")
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: threading.Thread | None = None
        self._attempt_in_progress = False
        # Set when the user explicitly disconnects; the watchdog must not
        # fight that decision until the next explicit connect.
        self._user_suppressed = False
        # Bumped by every teardown so a connection attempt that was in flight
        # during a disconnect can detect that it went stale and stay silent.
        self._epoch = 0

    def register_callbacks(
        self,
        on_status_change=None,
        on_recording_started=None,
        on_recording_stopped=None,
        on_replay_saved=None,
    ) -> None:
        """Register optional callbacks; callbacks run from the OBS thread."""
        self._on_status_change = on_status_change
        self._on_recording_started = on_recording_started
        self._on_recording_stopped = on_recording_stopped
        self._on_replay_saved = on_replay_saved

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_recording(self) -> bool:
        return self._recording_active

    @property
    def replay_buffer_active(self) -> bool | None:
        """OBS replay-buffer state learned at connect time.

        True/False reflect the queried output state; None means the query
        failed (or the client is disconnected), so callers cannot trust it.
        """
        return self._replay_buffer_active

    # ── Connection lifecycle ────────────────────────────────────────────────

    def connect(self, host="localhost", port=4455, password="") -> None:
        """Start a non-blocking OBS connection attempt.

        An explicit connect clears the auto-reconnect suppression left by an
        earlier explicit disconnect and refreshes the connection parameters
        the watchdog uses for its own retries.
        """
        self._user_suppressed = False
        self._connection_params = (str(host), int(port), str(password))
        self._start_attempt(source="manual")

    def disconnect(self) -> None:
        """Disconnect both OBS WebSocket clients and pause auto-reconnect."""
        # The user asked to be offline: the watchdog must not reconnect until
        # they explicitly connect again.
        self._user_suppressed = True
        self._teardown_clients()
        self._fire(self._on_status_change, "disconnected")

    def shutdown(self) -> None:
        """Stop the watchdog thread; used when the application closes."""
        self._watchdog_stop.set()

    def enable_auto_reconnect(
        self, interval: float = 5.0, host="localhost", port=4455, password=""
    ) -> None:
        """Watch OBS availability and connect automatically whenever it appears.

        Starts a daemon thread that attempts a connection immediately and
        then roughly every ``interval`` seconds while disconnected. Once
        connected it passively checks the socket so an OBS exit is noticed
        and retried. Explicit ``disconnect()`` pauses the watchdog until the
        next explicit ``connect()``.
        """
        self._reconnect_interval = max(1.0, float(interval))
        self._connection_params = (str(host), int(port), str(password))
        self._auto_reconnect_enabled = True
        if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
            self._watchdog_stop.clear()
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop, name="obs-watchdog", daemon=True
            )
            self._watchdog_thread.start()

    def _watchdog_loop(self) -> None:
        while not self._watchdog_stop.is_set():
            self._watchdog_pass()
            if self._watchdog_stop.wait(self._reconnect_interval):
                break

    def _watchdog_pass(self) -> None:
        """One watchdog cycle: reconnect when down, verify liveness when up."""
        if not self._auto_reconnect_enabled or self._user_suppressed:
            return
        if self._connected:
            if self._event_socket_alive():
                return
            print("[OBS] Connection lost (OBS closed?) — waiting for OBS")
            self._teardown_clients()
            self._fire(self._on_status_change, "waiting")
        if self._attempt_in_progress:
            return
        self._fire(self._on_status_change, "waiting")
        self._start_attempt(source="watchdog")

    def _event_socket_alive(self) -> bool:
        """Passive liveness probe of the event client's underlying socket.

        No WebSocket requests are issued (they could race concurrent calls);
        the WebSocket object's ``connected`` flag simply reports whether the
        connection is still up (websocket-client flips it to False when a
        recv fails after the remote side closes, so an OBS exit is caught).

        The client layout differs across obsws-python releases: >=1.x nests
        it at ``event.base_client.ws`` while legacy builds exposed ``ws``
        directly on the client. Anything unrecognizable fails OPEN — assume
        alive — because failing closed here made the watchdog tear down and
        reconnect a healthy connection every cycle, restarting the recording
        segment each time (the v2.6.1 churn bug).
        """
        with self._lock:
            event = self._event_client
        if event is None:
            return False
        try:
            ws = getattr(getattr(event, "base_client", None), "ws", None)
            if ws is None:
                ws = getattr(event, "ws", None)
            if ws is None:
                print(
                    "[OBS] Event client has an unrecognized layout; "
                    "assuming the connection is alive"
                )
                return True
            return bool(getattr(ws, "connected", True))
        except Exception:
            return True

    def _teardown_clients(self) -> None:
        """Drop both WebSocket clients and reset state without touching the
        auto-reconnect suppression flag."""
        with self._lock:
            self._epoch += 1
            event = self._event_client
            req = self._req_client
            self._event_client = None
            self._req_client = None
        for client in (event, req):
            if client is None:
                continue
            try:
                client.disconnect()
            except Exception as exc:
                print(f"[OBS] Disconnect error: {exc}")
        with self._lock:
            self._connected = False
            self._recording_active = False
            self._replay_buffer_active = None

    def _start_attempt(self, source: str) -> None:
        """Spawn one connection attempt unless another one is still running."""
        if self._attempt_in_progress:
            return
        self._attempt_in_progress = True
        host, port, password = self._connection_params
        with self._lock:
            self._epoch += 1
            epoch = self._epoch
        threading.Thread(
            target=self._connect_thread,
            args=(host, port, password, epoch, source),
            daemon=True,
        ).start()

    def _connect_thread(self, host, port, password, epoch: int, source: str) -> None:
        try:
            self._fire(
                self._on_status_change,
                "connecting" if source == "manual" else "waiting",
            )
            import obsws_python as obs

            req = obs.ReqClient(
                host=host, port=int(port), password=password, timeout=5
            )
            # Publish the request client before event handlers can fire so
            # the replay-path fallback query always has a client to use. A
            # disconnect that happened mid-attempt invalidates this one.
            with self._lock:
                if epoch != self._epoch:
                    req.disconnect()
                    return
                self._req_client = req
            event = obs.EventClient(
                host=host, port=int(port), password=password
            )
            event.callback.register(
                [self.on_record_state_changed, self.on_replay_buffer_saved]
            )

            with self._lock:
                if epoch != self._epoch:
                    # Stale attempt: a disconnect/teardown happened while we
                    # were connecting. Quietly drop the fresh clients.
                    try:
                        event.disconnect()
                    except Exception:
                        pass
                    try:
                        req.disconnect()
                    except Exception:
                        pass
                    return
                self._event_client = event
                self._connected = True

            try:
                record_status = req.get_record_status()
                self._recording_active = bool(record_status.output_active)
            except Exception as exc:
                print(f"[OBS] Could not query recording state: {exc}")
                self._recording_active = False

            try:
                replay_status = req.get_replay_buffer_status()
                self._replay_buffer_active = bool(replay_status.output_active)
            except Exception as exc:
                print(f"[OBS] Could not query replay buffer state: {exc}")
                self._replay_buffer_active = None

            self._fire(self._on_status_change, "connected")
            if self._recording_active:
                self._fire(self._on_recording_started)
            print("[OBS] Connected successfully.")
        except Exception as exc:
            print(f"[OBS] Connection failed: {exc}")
            with self._lock:
                self._connected = False
                self._recording_active = False
                self._replay_buffer_active = None
            if source == "manual":
                # A user-initiated attempt deserves the visible error.
                self._fire(self._on_status_change, f"error:{exc}")
            else:
                # Watchdog retries stay quiet in the GUI; the amber
                # "waiting" indicator covers them.
                self._fire(self._on_status_change, "waiting")
        finally:
            self._attempt_in_progress = False

    # ── OBS events ──────────────────────────────────────────────────────────

    def on_record_state_changed(self, data) -> None:
        """Forward main OBS recording transitions to the registered callbacks.

        The WebSocket protocol populates ``output_path`` only for the STARTED
        and STOPPED states; it is the file path of the recording, which the
        GUI uses as the timestamp segment name. Callbacks receive the path or
        None when the event did not carry one.
        """
        state = getattr(data, "output_state", "")
        output_path = getattr(data, "output_path", None)
        print(f"[OBS] Record state: {state}")

        if state == "OBS_WEBSOCKET_OUTPUT_STARTED":
            if not self._recording_active:
                self._recording_active = True
                self._fire(self._on_recording_started, output_path)
        elif state == "OBS_WEBSOCKET_OUTPUT_STOPPED":
            # Wait for STOPPED rather than STOPPING so the final file path is
            # available for renaming the segment that was just recorded.
            if self._recording_active:
                self._recording_active = False
                self._fire(self._on_recording_stopped, output_path)

    def on_replay_buffer_saved(self, data) -> None:
        """Forward OBS replay-buffer saves to the registered callback.

        The v5 ReplayBufferSaved event carries savedReplayPath; if a host
        omits it, fall back once to GetLastReplayBufferReplay. The callback
        always fires: with the path when it could be resolved, else None so
        the GUI can explain why no entry was created.
        """
        path = getattr(data, "saved_replay_path", None)
        if not path:
            try:
                with self._lock:
                    req = self._req_client
                if req is not None:
                    path = req.get_last_replay_buffer_replay().saved_replay_path
            except Exception as exc:
                print(f"[OBS] Could not resolve saved replay path: {exc}")
        if path:
            print(f"[OBS] Replay buffer saved: {path}")
            self._fire(self._on_replay_saved, str(path))
        else:
            print("[OBS] ReplayBufferSaved arrived without a resolvable path")
            self._fire(self._on_replay_saved, None)

    @staticmethod
    def _fire(callback, *args) -> None:
        if callback:
            try:
                callback(*args)
            except Exception as exc:
                print(f"[OBS] Callback error: {exc}")
