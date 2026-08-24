# Configuration reference

## `keybinds.json`

The app reads and writes `keybinds.json` beside the source script or packaged executable. It is local configuration and is ignored by Git.

Example:

```json
{
  "keybinds": {
    "mark_time": "f15"
  },
  "output_folder": "C:/Recordings",
  "project_name": "My Project",
  "recent_projects": [
    {"name": "My Project", "output_folder": "C:/Recordings/My Project"},
    {"name": "Old Take", "output_folder": "D:/Archive/Old Take"}
  ],
  "obs_settings": {
    "host": "localhost",
    "port": 4455,
    "password": "",
    "auto_connect": true
  },
  "mic_settings": {
    "silence_threshold": 250.0,
    "silence_timeout": 5.0
  },
  "audio_device": 1,
  "tags": [
    {"name": "kill", "color": "#FF5252"},
    {"name": "bug", "color": "#FFAB00"},
    {"name": "idea", "color": "#448AFF"}
  ]
}
```

## Settings

- `keybinds.mark_time`: normalized pynput keyboard name; default `f15`.
- `output_folder`: parent folder for project folders. If missing, the app uses `Timestamp_Audio` beside the application.
- `project_name`: last entered project name, used to prefill the project field.
- `recent_projects`: up to five recently opened projects as `{"name", "output_folder"}` pairs (each `output_folder` is the project folder itself), most recent first.
  - Clicking the project-name field opens a popup offering them for one-click loading; each row shows the project's timestamp/recording counts from its `session.json`.
  - The popup's 🗑 button recycles a project's folder to the Recycle Bin (after confirmation) and removes its entry.
  - The ✎ button renames a project (folder, markdown, and `session.json` updated; blocked for the open project).
  - Every change persists this key immediately; the list is refreshed whenever a project is set. Missing keys and malformed entries are ignored.
- `obs_settings.host`: OBS WebSocket hostname; default `localhost`.
- `obs_settings.port`: OBS WebSocket port; default `4455`.
- `obs_settings.password`: OBS WebSocket password, if configured.
- `obs_settings.auto_connect`: keep the OBS connection automatic (default when the key is missing: `true`). Set `false` for manual-only connections.
  - While enabled, a background watchdog attempts a connection immediately and retries roughly every 5 seconds while OBS is unreachable, showing `● Waiting for OBS…` instead of error spam.
  - Once connected, it passively checks the socket so an OBS exit is noticed and retried.
  - Wrong-password failures surface even from watchdog retries — a red `● OBS auth failed` header plus a status-bar hint ("OBS refused the password — click ⚙ to edit") — de-duplicated so repeated pulses stay quiet.
  - Clicking **Disconnect** also pauses auto-connect until **Connect OBS** is clicked again.
  - Edited in-app via the header ⚙ button (host, port, password, auto-connect), which saves and reconnects immediately.
- `audio_device`: selected PortAudio input index, or `null` for system default.
- `mic_settings.silence_threshold`: int16 RMS level a microphone signal must reach to count as voice (default `250.0`).
  - Raise it if background noise keeps silent recordings alive; lower it if quiet speech gets cut off.
  - Invalid values fall back to the default.
- `mic_settings.silence_timeout`: seconds a recording can run with **no input above the threshold at any point** before it is auto-stopped and discarded with a "No microphone input detected" status (default `5.0`, minimum `0`).
  - Any detected input permanently disarms the auto-stop for that take — pauses during speech never delete recorded audio.
  - Use a very large value to effectively disable the auto-stop; invalid values fall back to the default.
- `tags`: the tag library offered in the timestamp edit dialog. Each entry has a `name` and a hex `color` (`#RRGGBB`).
  - Invalid colors fall back to gray; duplicate names (case-insensitive) are ignored.
  - When the key is missing or empty, the defaults `kill`, `bug`, and `idea` are used.
  - Manage tags in-app through the tag library dialog (header **🏷 Tags** button or **＋ New tag** inside the edit dialog); every change there rewrites this key immediately.
  - Hand-editing still works and loads on the next launch.

The GUI saves tag definitions immediately when changed in the tag library and saves the remaining settings on close or project/folder changes.

## Hotkey capture

Click the **Timestamp hotkey** button, press a keyboard key or an auxiliary mouse button (Mouse 4, Mouse 5, or middle click), and the new key is applied immediately. Left/right clicks are excluded on purpose. Press `Esc` to cancel. The GUI button and global listener both update without restarting the app.

Hotkey triggering tolerates missed release events: a key re-fires if pressed again after a short window even when Windows never delivered the release, so a hotkey cannot silently stop working mid-session.

## Project naming

Project names are displayed as entered but sanitized for Windows folder and Markdown filenames. Characters invalid in Windows paths are replaced with underscores. A project is stored under:

```text
<output_folder>/<safe project name>/
```

Do not place secrets in project names or commit `keybinds.json`.
