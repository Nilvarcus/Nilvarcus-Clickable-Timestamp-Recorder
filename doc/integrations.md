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
- OBS connection status is displayed in the app header; `● Waiting for OBS…` means the watchdog is retrying while OBS is closed, and a detected OBS exit returns the app to that state automatically.

The app does not command OBS to start or stop recording; OBS is the source of truth for this MVP.

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
