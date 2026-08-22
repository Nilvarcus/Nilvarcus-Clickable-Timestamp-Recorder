# Architecture

## Runtime shape

The app is a small single-process CustomTkinter application with background OBS and pynput callbacks.

```text
main()
  └─ TimestampApp (timestamp_gui.py)
       ├─ TimestampSession (timestamp_audio.py)
       ├─ AudioRecorder / PlaybackController (timestamp_audio.py)
       ├─ OBSManager (timestamp_obs.py)
       └─ Screenshot worker threads (timestamp_screenshot.py, one per new timestamp)
```

## Module responsibilities

### `timestamp_gui.py`

Owns the window, project/output controls (including the recent-projects popup bound to the project-name field), device selector, manual ▶/■ timer toggle, timestamp list, hotkey capture, the tag-library manager dialog (header **🏷 Tags** button plus a **＋ New tag** shortcut inside the timestamp edit dialog whose chips refresh live), OBS callbacks, and user-facing state messages. All callbacks from background listeners are routed to Tk using `root.after(0, ...)`.

### `timestamp_audio.py`

Contains GUI-independent functionality:

- `TimestampEntry`: persisted timestamp state (`kind` distinguishes regular timestamps from automatic replay-buffer entries; replay entries carry `replay_file`).
- `TimestampSession`: project timer, IDs, JSON persistence, Markdown generation, and safe filenames; `rename_tag()`/`remove_tag()` propagate tag-library edits across entries (case-insensitive, bypassing the recording-row edit lock) and persist like any other change.
- `sanitize_recent_projects` / `update_recent_projects`: pure helpers behind the five-slot recent-projects list stored in `keybinds.json`.
- `sanitize_tag_definitions` / `normalize_tag_name` / `is_valid_hex_color`: pure helpers validating the `tags` key and normalizing tag names everywhere (config load and the tag manager share them).
- `AudioRecorder`: one active `sounddevice.RawInputStream` and WAV writer.
- `PlaybackController`: Windows-native WAV playback and stop behavior.

### `timestamp_obs.py`

Wraps `obsws-python` request and event clients. It exposes connection state and `is_recording`, queries recording state on connect, listens for recording transitions and `ReplayBufferSaved` events (resolving the path via the event payload or a `GetLastReplayBufferReplay` fallback), and emits callbacks without importing Tkinter.

An optional watchdog (`enable_auto_reconnect`) runs as a daemon thread: while enabled and not user-suppressed it attempts a connection immediately and then roughly every 5 seconds, firing a `"waiting"` status instead of error spam on failed retries. While connected it passively inspects the event client's underlying socket (defensive attribute access; no extra WebSocket requests, avoiding concurrent-request races) so an OBS exit is detected, torn down internally, and retried. Connection attempts carry an epoch counter so an attempt in flight during a disconnect goes stale silently. Explicit `disconnect()` sets a suppression flag paused until the next explicit `connect()`; internal teardown never touches it.

### `timestamp_screenshot.py`

Captures the context snapshot saved next to each new timestamp. `capture_to_path` lazily imports mss and Pillow (so tests run without them), grabs OS primary monitor 1 in physical pixels, converts BGRA→RGB, scales to at most 720 px height without upscaling, and writes JPEG quality 100. `compute_target_size` is a pure function so the scaling rule is unit-testable. Failures raise `ScreenshotError`; callers keep the timestamp valid.

### `timestamp_gui.spec`

Builds the windowed PyInstaller executable and bundles the `_sounddevice_data` PortAudio DLL required by microphone capture plus the mss/Pillow runtime used by screenshot capture (`mss` is listed as a hidden import).

## Event flow

### OBS start

```text
OBS output-start event or initial record-status query
  → OBSManager._recording_active = True
  → on_recording_started callback (carries the recording file path from
    RecordStateChanged.outputPath when OBS reported it)
  → root.after(0, TimestampApp._start_obs_timer)
  → TimestampSession.start_timer(recording_name)   # opens a numbered segment
  → timestamp button enabled, segment header appears in the list
```

The segment name is derived from the OBS recording file name (basename without extension). When the app connects while OBS is already recording, no file path is available yet; the segment starts unnamed and is renamed via `TimestampSession.name_recording` when the stop event reveals the final path.

### Manual timer (OBS-free sessions)

```text
▶ Start timer click
  → TimestampSession.start_timer()          # same call as OBS start; opens "Recording N"
  → clock/REC indicator run; New timestamp enabled
...normal timestamp creation...
■ Stop timer click (or an OBS stop event)
  → TimestampSession.stop_timer()           # segment closed, creation locked
```

Both entry points share the model methods, so segments, per-segment indices, locking, and Markdown sections behave identically regardless of who started the timer. If a manual timer is already running when OBS starts, `start_timer` no-ops and the live segment continues; the eventual OBS stop still backfills its file name before locking.

Row clicks are never gated on the timer: pending/error rows record, recording rows stop, completed rows play — only *creating* timestamps requires a running timer.

### Timestamp recording

```text
New timestamp button/hotkey
  → TimestampSession.create_timestamp()   # stamped with the current segment
  → pending row appears under the live segment header
  → click row
  → AudioRecorder.start()
  → recording row appears
  → click row again
  → AudioRecorder.stop()
  → WAV file + session.json + Markdown link
```

### Screenshot capture

```text
New timestamp created (button/hotkey/manual)
  → TimestampSession.screenshot_path()    # unique JPEG path reserved on the Tk thread
  → daemon thread: mss grab of monitor 1 → Pillow BGRA→RGB → fit to ≤720p height → JPEG
  → root.after(0): attach relative path as entry.screenshot_file
  → session.json save + Markdown regenerates with the embedded image line
```

Capture failure only shows an amber status message; the timestamp stays valid and its entry simply has no image line.

Each timestamp row carries a 📷 button (`_open_screenshot`) that opens `entry.screenshot_file` (stored relative to the project folder) with the OS default image viewer, mirroring a replay row's 🎬 button. The button renders disabled until the asynchronous capture attaches the JPEG, so in-flight, failed, and pre-screenshot entries stay greyed out; a click on a missing file reports an error in the status bar. Replay rows capture no screenshot and keep no 📷 button.

### OBS stop

```text
OBS output-stop event
  → on_recording_stopped callback
  → root.after(0, TimestampApp._stop_obs_timer)
  → active microphone note finalized
  → TimestampSession.stop_timer()
  → timestamp button disabled
```

### Replay-buffer save

```text
OBS ReplayBufferSaved event
  → OBSManager.on_replay_buffer_saved      # savedReplayPath, or
                                           # GetLastReplayBufferReplay fallback
  → on_replay_saved callback
  → root.after(0, TimestampApp._on_obs_replay_saved)
  → TimestampSession.create_replay_entry(path)
     # deduplicates by path; attaches to the live segment, else the most
     # recent one, else ungrouped — never gated on a running timer
  → violet 🎬 REPLAY row appears inline between timestamps
```

Without a selected project the save is announced but not logged. Replay rows reuse the timestamp click state machine (pending → record, recording → stop, completed → play); their extra 🎬 button opens the linked video with the OS default player. No screenshot is captured for replay entries.

## Persistence

Each project folder contains:

- `<Project Name>.md`: readable log with one section per recording segment, relative links to completed WAV files, and an embedded `![Screenshot]` line under each captured timestamp.
- `session.json`: project name, timer metadata, recording segments, timestamp states, file paths, durations, and relative screenshot filenames.
- `R##-###_HH-MM-SS.wav`: microphone recordings, named after their segment and per-segment index.
- `Screenshots/R##-###_HH-MM-SS.jpg`: per-timestamp main-monitor snapshots (720p-height JPEG).

A temporary JSON file is atomically replaced during saves. Active recording/timer states are normalized on the next process launch because hardware streams cannot be resumed safely.

## Threading rules

- OBS connection and event callbacks run outside the Tk main loop.
- The OBS auto-reconnect watchdog is another daemon thread; its status callbacks reach Tk through the same `root.after` marshaling as every other OBS callback.
- Global hotkeys run through daemon pynput keyboard and mouse listeners.
- Never mutate Tk widgets directly from those callbacks; schedule work with `root.after`.
- Screenshot capture runs on short-lived daemon threads; they never touch Tk or session state directly — results are attached through `root.after` like every other callback.
- Audio callback threads only append raw audio blocks; WAV finalization occurs when the user stops recording.
