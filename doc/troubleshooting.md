# Troubleshooting

## OBS does not connect

- Confirm OBS is running. The header shows amber `● Waiting for OBS…` while the app retries, and it connects on its own once OBS starts — no button press needed.
- Enable the OBS WebSocket server.
- Check host, port, and password in `keybinds.json`. A wrong password keeps the app in `● Waiting for OBS…` (retry details are printed to the console); with auto-connect off, a manual attempt shows the error instead.
- Confirm the app status changes from connecting to connected.
- If you clicked **Disconnect**, auto-connect stays paused on purpose until you click **Connect OBS** again.
- Restart the app after changing OBS WebSocket settings.

## Timer does not start

- Set a project name and click **Set project** first.
- Confirm OBS is connected.
- Start the main OBS recording, not only the replay buffer.
- If OBS was already recording before connection, select/set the project after connecting so the app can synchronize to the existing recording.

## Timestamp button is disabled

Creating timestamps needs a running timer: an active OBS recording **or** the in-app **▶ Start timer** button. The button stays disabled until a project is selected and one of those is active.

## Recent-projects popup does not appear or is empty

- The popup only lists projects set through **Set project** (or loaded from the popup) since the feature was added; it fills up as you work.
- Click directly on the project-name field; typing any character closes it so new names can be entered normally.
- Entries live in `keybinds.json` under `recent_projects`; malformed entries are ignored, and the list keeps at most five projects.
- Selecting a project whose folder was deleted recreates the folder — same as setting the project fresh.

## Timer questions

- **▶ Start timer** and OBS recording share the same session timer: whichever comes first opens segment 1, and each later start opens the next numbered segment.
- Stopping the timer (in-app or via OBS stop) locks new-timestamp creation until a timer starts again.
- Existing timestamps stay clickable while locked: pending rows record notes, completed rows play back.

## Global timestamp hotkey does not work

- Use the **Timestamp hotkey** setting to capture a new key.
- Press `Esc` to cancel capture.
- Check that another application is not consuming the key.
- Run the app with the permissions required by the target application, especially when controlling an elevated/full-screen app.
- The GUI button remains available even if the global listener is unavailable.

## Microphone is missing

- Click **Refresh** beside the microphone selector.
- Select the explicit physical input instead of **System default**.
- Confirm Windows microphone privacy permissions allow desktop apps to record.
- Close applications that exclusively hold the microphone.
- If the packaged app reports a missing audio backend, rebuild with:

```bash
pyinstaller --clean --noconfirm timestamp_gui.spec
```

The spec bundles the PortAudio runtime required by `sounddevice`.

## Recording fails

- Read the red status message in the footer; it contains the PortAudio error.
- Try the same input through Windows Sound settings.
- Try another backend variant for the device, such as MME or WASAPI.
- Confirm the output folder is writable.
- Only one timestamp microphone recording can be active at a time.

## Screenshots fail

- The amber footer message contains the underlying error; timestamps are still created without their snapshot.
- Run from source requires `mss` and `pillow` (`python -m pip install mss pillow`).
- If the packaged app reports a missing screenshot backend, rebuild with:

```bash
pyinstaller --clean --noconfirm timestamp_gui.spec
```

The spec bundles the mss/Pillow runtime used for captures.
- Confirm the project folder is writable; captures are written to its `Screenshots` subfolder.
- Remote-desktop sessions or locked workstations can block screen capture; unlock the session and create another timestamp.

## Project files are missing

A project folder contains `<Project Name>.md`, `session.json`, WAV files, and a `Screenshots` folder with per-timestamp JPEGs. Keep the folder together when moving it so Markdown relative links and embedded images remain valid. If `session.json` is malformed, restore it from a backup; it is the machine-readable source of truth.

## OBS stops while a note is recording

The app automatically finalizes the active microphone note, saves its WAV file, stops the timer, and locks timestamp creation.

## Diagnostic commands

```bash
python --version
python -m pip show customtkinter pynput obsws-python sounddevice
python -m unittest -v
python -m py_compile timestamp_gui.py timestamp_audio.py timestamp_obs.py test_timestamp_audio.py
```
