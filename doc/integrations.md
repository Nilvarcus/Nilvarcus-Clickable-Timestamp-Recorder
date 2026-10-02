# Integrations

## OBS Studio

The app uses OBS WebSocket v5 through `obsws-python`.

### Setup

1. In OBS, open **Tools → WebSocket Server Settings**.
2. Enable the WebSocket server.
3. Confirm the port, normally `4455`, and configure a password if desired.
4. That is usually all: with auto-connect enabled (the default), the app connects on its own whenever OBS is reachable — the header shows amber `● Waiting for OBS…` while it retries — and follows OBS restarts automatically.
5. If the host, port, or password differ from the defaults, enter them via the header **⚙** button (it saves and reconnects immediately). Hand-editing `keybinds.json` works too; restart the app afterwards.
6. Manual control stays available: click **Connect OBS** or **Disconnect** in the app. Disconnecting pauses auto-connect until you click **Connect OBS** again.

### Behavior

- OBS recording start resets and starts the project timer.
- OBS recording stop finalizes an active microphone timestamp, stops the timer, and locks new timestamps.
- If the app connects while OBS is already recording, it queries OBS recording status and synchronizes once a project is selected.
- The timer follows the *combined* state of the main recording and the Aitum Vertical plugin's recording (see below): while either is active the timer runs, starting one while the other records does not restart it, and it stops only when both have stopped.
- OBS connection status is displayed in the app header; `● Waiting for OBS…` means the watchdog is retrying while OBS is closed, and a detected OBS exit returns the app to that state automatically.

The app does not command OBS to start or stop recording; OBS is the source of truth for this MVP.

### Aitum Vertical (vertical-canvas plugin)

The Aitum Vertical plugin records through its own output, which never fires the standard `RecordStateChanged` event. Starting with this, the app follows it through the plugin's obs-websocket **vendor API** (vendor name `aitum-vertical-canvas`, names verified against the plugin's source as of 1.6.x):

- Starting/stopping a vertical recording (dock button or hotkey) starts/stops the app's timer exactly like a main recording.
- Vertical segments get the real video file name in their header: after a vertical start the app reads the recording output's settings (`vertical_canvas_record`) over the WebSocket. If that query fails, the segment starts unnamed and the stop event backfills the name from the same source; if both fail it stays unnamed.
- Vertical **Backtrack** saves (`backtrack_saved` vendor event) are logged as 🎬 REPLAY entries like main replay-buffer saves. The event carries no file path, so the app reads the Backtrack save folder from the plugin's replay output settings and scans it (including subfolders like `Replay Buffer\`, where the plugin drops its clips) for a file named `Backtrack [DD-MM][HH-MM-SS]` whose embedded save time (day-month, local clock) is within ~15 s — the newest one wins. The vertical recording's own file (`[DD-MM][HH-MM-SS]-vertical.mp4` in the parent folder, whose name also embeds a bracket time and whose mtime stays fresh while recording) is explicitly excluded so it is never mistaken for a clip; files without a parseable clip name fall back to the newest video modified within ~15 s. When nothing can be resolved, the entry is not logged and the status bar explains why.
- If the app connects while a vertical recording is already running, it queries the plugin's vendor `status` request and starts the timer once a project is selected (same join-mid-recording behavior as the main output). Without the plugin installed the query simply fails once and is ignored — no behavior change.
- With the main recording and the vertical recording running at the same time, they merge into one timer session: the timer starts with the first one, keeps running across the overlap, and stops with the last one. Only the first start names the segment.
- Limitations: vendor/output names are pinned to the Aitum Vertical plugin (the successor Aitum Stream Suite, which refuses to coexist with it, is not covered); multiple vertical canvases are merged into a single recording state; vertical recording *pause* is not tracked (the manual ⏸ Pause button is disabled while OBS drives the session anyway).

### Replay buffer

When OBS saves its replay buffer (requires the replay buffer source enabled in OBS), the app receives the `ReplayBufferSaved` WebSocket event and automatically logs a replay entry into the selected project:

- The entry carries `savedReplayPath` from the event (with a `GetLastReplayBufferReplay` query as fallback for hosts that omit it).
- Entries appear inline between timestamps, numbered in the shared per-segment sequence, tagged with the replay file's name (for example `replay- [21-08][17-44-10]`).
- A save arriving while the timer is stopped attaches to the most recent segment; before any recording it stays ungrouped. Without a selected project nothing is logged.
- Saving the same replay file twice never creates a second entry.
- Replays are detected only while connected; saves made before connecting are not backfilled.

## Microphone audio

Microphone capture uses `sounddevice.RawInputStream` and writes mono signed 16-bit WAV files. The GUI lists input devices with their PortAudio index and Windows backend to distinguish physical and virtual devices.

If the system default does not work, select the explicit microphone input. The packaged executable includes the PortAudio runtime through `timestamp_gui.spec`.

## Local Markdown links

Each completed timestamp is written as a relative link in the project Markdown file. Keeping the Markdown file and WAV files in the same project folder preserves those links when the folder is moved together.
