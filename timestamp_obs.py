"""Minimal OBS WebSocket integration for the Clickable Timestamp Recorder."""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime

# Aitum Vertical (obs-vertical-canvas) registers this obs-websocket vendor and
# emits custom events (recording_started/stopped, backtrack_saved, ...) for its
# own independent recording output, which never fires the standard
# RecordStateChanged event. Names verified against the plugin source (1.6.x).
AITUM_VERTICAL_VENDOR = "aitum-vertical-canvas"
# Stable output name the plugin gives its recording output; used to resolve
# the vertical recording's file path via GetOutputSettings.
AITUM_VERTICAL_RECORD_OUTPUT = "vertical_canvas_record"
# The main OBS replay buffer output is internally named "replay_buffer"; the
# Aitum Vertical one ("Vertical Backtrack", localized) has kind replay_buffer
# with any other name.
MAIN_REPLAY_BUFFER_NAME = "replay_buffer"
VIDEO_FILE_EXTENSIONS = (".mp4", ".mkv", ".mov", ".ts", ".avi", ".flv", ".webm")
# Backtrack saves give no path; a qualifying clip's embedded [DD-MM][HH-MM-SS]
# save time (or its mtime as fallback) must be this close to the event.
BACKTRACK_FILE_WINDOW_SECONDS = 15.0


def _event_field(obj, *names):
    """Read the first available field from a dict or attr-based payload.

    obsws-python converts event payloads to dataclasses with snake_case
    attributes, but older releases (or future ones) may hand us plain dicts
    with camelCase keys. Any unrecognized shape must return None, never raise.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj[name]
        return None
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def pick_recent_video_file(entries, now, window=BACKTRACK_FILE_WINDOW_SECONDS):
    """Return the path of the newest recently-modified video file.

    ``entries`` is an iterable of (mtime, path) pairs for files in the Backtrack
    save folder. Only video extensions count and only files modified within
    ``window`` seconds of ``now`` qualify (a slightly future mtime from clock
    skew is accepted). Returns None when nothing qualifies.
    """
    best_path = None
    best_mtime = None
    for mtime, path in entries:
        extension = os.path.splitext(str(path))[1].lower()
        if extension not in VIDEO_FILE_EXTENSIONS:
            continue
        if mtime is None:
            continue
        age = now - mtime
        if age < -window or age > window:
            continue
        if best_mtime is None or mtime > best_mtime:
            best_mtime = mtime
            best_path = str(path)
    return best_path


# The Aitum Vertical Backtrack output names files like
# ``Backtrack [02-10][10-38-03].mp4`` — day-month, local save time. Its
# recording output names files ``[02-10][10-38-03]-vertical.mp4`` in the
# parent folder, which embeds the same bracket time and so must never
# qualify as a Backtrack clip.
BACKTRACK_TIME_PATTERN = re.compile(
    r"\[(\d{2})-(\d{2})\]\[(\d{2})-(\d{2})-(\d{2})\]"
)
# Real Backtrack clips start their stem with this prefix (1.6.x default
# template); matching requires both the prefix and the bracket time.
BACKTRACK_NAME_PREFIX = "backtrack"
# Suffix the recording output appends to its file names; such files are
# never Backtrack clips.
VERTICAL_RECORD_NAME_SUFFIX = "-vertical"


def parse_backtrack_save_time(filename, now=None):
    """Extract the embedded save time from a Backtrack file name.

    Looks for ``[DD-MM][HH-MM-SS]`` anywhere in ``filename`` (the literal
    ``Backtrack`` prefix and the extension are not required). The year is
    not stored in the name, so each adjacent-year candidate (``now.year``,
    ``±1``) is built and the one closest to ``now`` wins — covering a New
    Year's Eve rollover. Returns a naive local ``datetime`` or None when
    the name does not match or holds invalid date values.
    """
    match = BACKTRACK_TIME_PATTERN.search(str(filename))
    if not match:
        return None
    day, month, hour, minute, second = (int(part) for part in match.groups())
    if now is None:
        now = datetime.now()
    best = None
    for year in (now.year - 1, now.year, now.year + 1):
        try:
            candidate = datetime(year, month, day, hour, minute, second)
        except ValueError:
            continue
        if best is None or abs(candidate - now) < abs(best - now):
            best = candidate
    return best


def collect_backtrack_candidates(directory, max_depth=3):
    """Collect ``(mtime, path)`` pairs for video files under ``directory``.

    Walks limited depth: the plugin saves clips into a subfolder of the
    resolved output directory (e.g. ``Replay Buffer\\``), while the vertical
    recording files sit in the top level. ``max_depth=1`` is the resolved
    directory alone; unreadable entries are skipped.
    """
    candidates = []

    def recurse(base, depth):
        try:
            with os.scandir(base) as scan:
                for entry in scan:
                    try:
                        if entry.is_dir():
                            if depth < max_depth:
                                recurse(entry.path, depth + 1)
                            continue
                        if not entry.is_file():
                            continue
                        extension = os.path.splitext(entry.path)[1].lower()
                        if extension not in VIDEO_FILE_EXTENSIONS:
                            continue
                        candidates.append((entry.stat().st_mtime, entry.path))
                    except OSError:
                        continue
        except OSError:
            return

    recurse(directory, 1)
    return candidates


def pick_backtrack_video_file(
    entries, now, window=BACKTRACK_FILE_WINDOW_SECONDS, exclude_paths=()
):
    """Return the video file of a just-saved Aitum Vertical Backtrack.

    ``entries`` has the ``(mtime, path)`` shape of :func:`collect_backtrack_candidates`.
    Files whose stem starts with ``Backtrack`` and embeds a
    ``[DD-MM][HH-MM-SS]`` save time are judged solely by that time (newest
    within ``window`` of ``now`` wins) and always beat the rest; only
    remaining files go through the mtime-based ``pick_recent_video_file``
    fallback. Files in ``exclude_paths`` and vertical recording files
    (``-vertical`` name suffix) are never candidates — the recording file is
    continuously rewritten while recording, so its mtime and its embedded
    bracket time would otherwise win. A bracket time without the Backtrack
    prefix belongs to the mtime pool. Returns the chosen path or None when
    nothing qualifies. ``now`` may be an epoch float or a naive ``datetime``.
    """
    now_dt = now if isinstance(now, datetime) else datetime.fromtimestamp(now)
    excluded = {os.path.normcase(str(exclude)) for exclude in exclude_paths if exclude}
    pattern_entries = []
    other_entries = []
    for entry in entries:
        path = str(entry[1])
        if os.path.normcase(path) in excluded:
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        lowered_stem = stem.lower()
        if lowered_stem.endswith(VERTICAL_RECORD_NAME_SUFFIX):
            continue
        extension = os.path.splitext(path)[1].lower()
        if extension not in VIDEO_FILE_EXTENSIONS:
            continue
        embedded = parse_backtrack_save_time(stem, now_dt)
        if embedded is not None and lowered_stem.startswith(BACKTRACK_NAME_PREFIX):
            pattern_entries.append((embedded, path))
        else:
            other_entries.append((entry[0], path))
    best_path = None
    best_embedded = None
    for embedded, path in pattern_entries:
        age = (now_dt - embedded).total_seconds()
        if age < -window or age > window:
            continue
        if best_embedded is None or embedded > best_embedded:
            best_embedded = embedded
            best_path = path
    if best_path is not None:
        return best_path
    fallback_now = now.timestamp() if isinstance(now, datetime) else now
    return pick_recent_video_file(other_entries, fallback_now, window)


class OBSManager:
    """Connect to OBS and report recording output state.

    Two independent recording outputs are tracked and merged: OBS's main
    recording (standard RecordStateChanged events) and the Aitum Vertical
    plugin's canvas recording (custom vendor events). The started/stopped
    callbacks fire on transitions of the *combined* state, so overlapping
    recordings produce exactly one started → stopped pair while either is
    active.

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
        self._vertical_recording_active = False
        # Merged view used for started/stopped transitions; reset by teardown
        # so a reconnect mid-recording re-fires started.
        self._combined_recording_active = False
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
        """True while the main recording OR the Aitum Vertical recording is active."""
        return self._recording_active or self._vertical_recording_active

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
            self._vertical_recording_active = False
            self._combined_recording_active = False
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
                [
                    self.on_record_state_changed,
                    self.on_replay_buffer_saved,
                    self.on_vendor_event,
                ]
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

            # Aitum Vertical's canvas recording must be polled because it
            # never fires the standard RecordStateChanged event. Failure means
            # the plugin is absent or the installed obsws-python lacks vendor
            # support; either way the vertical state just stays False.
            vertical = self._query_vertical_recording(req)
            self._vertical_recording_active = bool(vertical) if vertical else False

            try:
                replay_status = req.get_replay_buffer_status()
                self._replay_buffer_active = bool(replay_status.output_active)
            except Exception as exc:
                print(f"[OBS] Could not query replay buffer state: {exc}")
                self._replay_buffer_active = None

            self._fire(self._on_status_change, "connected")
            if self._recording_active or self._vertical_recording_active:
                self._combined_recording_active = True
                self._fire(self._on_recording_started)
            print("[OBS] Connected successfully.")
        except Exception as exc:
            print(f"[OBS] Connection failed: {exc}")
            with self._lock:
                self._connected = False
                self._recording_active = False
                self._vertical_recording_active = False
                self._combined_recording_active = False
                self._replay_buffer_active = None
            if self._is_auth_failure(exc):
                # Wrong password / auth failures must surface even from the
                # watchdog — "Waiting for OBS…" hides the real problem.
                self._fire(self._on_status_change, f"auth_error:{exc}")
            elif source == "manual":
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
                self._recompute_recording_activity(output_path)
        elif state == "OBS_WEBSOCKET_OUTPUT_STOPPED":
            # Wait for STOPPED rather than STOPPING so the final file path is
            # available for renaming the segment that was just recorded.
            if self._recording_active:
                self._recording_active = False
                self._recompute_recording_activity(output_path)

    def on_vendor_event(self, data) -> None:
        """Handle obs-websocket vendor events (plugin-defined events).

        obsws-python dispatches the generic ``VendorEvent`` to a callback with
        this name; the payload nests ``vendorName``/``eventType``/``eventData``.
        Only events from the Aitum Vertical vendor are interpreted — other
        plugins (or any unrecognizable payload shape) are silently ignored.

        Used for Aitum Vertical's independent recording output, which never
        fires the standard RecordStateChanged event: ``recording_started``/
        ``recording_stopped`` drive the combined timer state, and
        ``backtrack_saved`` (its replay buffer) logs a replay entry.
        """
        vendor_name = _event_field(data, "vendorName", "vendor_name")
        if vendor_name != AITUM_VERTICAL_VENDOR:
            return
        event_type = _event_field(data, "eventType", "event_type") or ""
        print(f"[OBS] Aitum Vertical event: {event_type}")

        # The started/stopping-family events are ignored on purpose: the main
        # path likewise waits for the definitive STARTED/STOPPED states.
        if event_type == "recording_started":
            with self._lock:
                already = self._vertical_recording_active
                self._vertical_recording_active = True
            if not already:
                self._recompute_recording_activity(self._vertical_record_path())
        elif event_type == "recording_stopped":
            # Resolve the file path before clearing the state: the output's
            # settings keep the last recording path, so the stop event can
            # backfill the segment name even if start resolution failed.
            path = self._vertical_record_path()
            with self._lock:
                already = not self._vertical_recording_active
                self._vertical_recording_active = False
            if not already:
                self._recompute_recording_activity(path)
        elif event_type == "backtrack_saved":
            self._fire(self._on_replay_saved, self._vertical_backtrack_path())

    def _recompute_recording_activity(self, path=None) -> None:
        """Fire started/stopped callbacks on transitions of the combined state.

        Main and Aitum Vertical recordings are independent outputs; the timer
        sees one merged session that runs while either is active, so starting
        one while the other already records does not restart the timer.
        """
        combined = self._recording_active or self._vertical_recording_active
        if combined and not self._combined_recording_active:
            self._combined_recording_active = True
            self._fire(self._on_recording_started, path)
        elif not combined and self._combined_recording_active:
            self._combined_recording_active = False
            self._fire(self._on_recording_stopped, path)

    def _query_vertical_recording(self, req):
        """Query Aitum Vertical's recording state via its vendor status request.

        Returns True/False, or None when the plugin is not installed (the
        request fails), the installed obsws-python lacks call_vendor_request,
        or the response shape is unrecognized.
        """
        if not hasattr(req, "call_vendor_request"):
            return None
        try:
            response = req.call_vendor_request(AITUM_VERTICAL_VENDOR, "status")
        except Exception as exc:
            print(f"[OBS] Aitum Vertical status unavailable: {exc}")
            return None
        # The vendor's own response data is nested under responseData in
        # current obs-websocket releases; older shapes may expose it directly.
        reply = _event_field(response, "responseData", "response_data") or response
        recording = _event_field(reply, "recording")
        return recording if isinstance(recording, bool) else None

    def _vertical_record_path(self) -> str | None:
        """Best-effort resolve the vertical recording's current file path.

        Reads the plugin's recording output settings (output name
        ``vertical_canvas_record``), whose ``path`` field holds the file being
        recorded. Returns None on any failure — the segment then simply has
        no name.
        """
        try:
            with self._lock:
                req = self._req_client
            if req is None or not hasattr(req, "get_output_settings"):
                return None
            settings = _event_field(
                req.get_output_settings(AITUM_VERTICAL_RECORD_OUTPUT),
                "outputSettings",
                "output_settings",
            )
            path = _event_field(settings, "path")
            return path if isinstance(path, str) and path else None
        except Exception as exc:
            print(f"[OBS] Could not resolve vertical recording path: {exc}")
            return None

    def _vertical_backtrack_path(self) -> str | None:
        """Best-effort resolve the file of a just-saved Aitum Vertical Backtrack.

        The backtrack_saved vendor event carries no path, so the Backtrack
        save folder is read from the plugin's replay-buffer output settings
        (found as a replay_buffer-kind output not named like OBS's own) and
        scanned — including subfolders, where the plugin drops clips like
        ``Backtrack [DD-MM][HH-MM-SS].mp4`` — for a clip whose embedded save
        time is within ~15 s of the event. The vertical recording's own file
        (queried from the recording output settings) is excluded, falling
        back to the newest recently-modified mtime rule otherwise.
        """
        try:
            with self._lock:
                req = self._req_client
            if req is None or not hasattr(req, "get_output_settings"):
                return None
            directory = self._vertical_backtrack_directory(req)
            if not directory or not os.path.isdir(directory):
                return None
            recording_path = self._vertical_record_path()
            entries = collect_backtrack_candidates(directory)
            return pick_backtrack_video_file(
                entries,
                time.time(),
                BACKTRACK_FILE_WINDOW_SECONDS,
                exclude_paths=(recording_path,),
            )
        except Exception as exc:
            print(f"[OBS] Could not resolve vertical backtrack path: {exc}")
            return None

    @staticmethod
    def _vertical_backtrack_directory(req) -> str | None:
        """Find the Aitum Vertical Backtrack save folder from output settings."""
        if not hasattr(req, "get_output_list"):
            return None
        response = req.get_output_list()
        outputs = _event_field(response, "outputs") or []
        for output in outputs:
            kind = _event_field(output, "outputKind", "output_kind")
            name = _event_field(output, "outputName", "output_name")
            if kind != "replay_buffer" or not name or name == MAIN_REPLAY_BUFFER_NAME:
                continue
            settings = _event_field(
                req.get_output_settings(name),
                "outputSettings",
                "output_settings",
            )
            directory = _event_field(settings, "directory")
            if isinstance(directory, str) and directory:
                return directory
        return None

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
    def _is_auth_failure(exc: Exception) -> bool:
        text = str(exc).lower()
        markers = ("auth", "password", "401", "unauthorized", "authentication")
        return any(marker in text for marker in markers)

    @staticmethod
    def _fire(callback, *args) -> None:
        if callback:
            try:
                callback(*args)
            except Exception as exc:
                print(f"[OBS] Callback error: {exc}")
