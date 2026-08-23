# Troubleshooting

## OBS does not connect

- Confirm OBS is running. The header shows amber `● Waiting for OBS…` while the app retries, and it connects on its own once OBS starts — no button press needed.
- Enable the OBS WebSocket server.
- Check host, port, and password via the header ⚙ button (or in `keybinds.json`). A wrong password now shows a red `● OBS auth failed` header and a status-bar hint ("OBS refused the password — click ⚙ to edit") instead of hiding behind the amber dot; it is de-duplicated so repeated retries stay quiet.
- Confirm the app status changes from connecting to connected.
- If you clicked **Disconnect**, auto-connect stays paused on purpose until you click **Connect OBS** again.
- Changing settings via ⚙ reconnects immediately; otherwise restart the app after hand-editing `keybinds.json`.

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
- Entries whose folder no longer exists render a dim `(folder missing)` marker instead of counts; selecting one recreates the folder — same as setting the project fresh.
- Deleting a project from the popup needs the `send2trash` package (`python -m pip install send2trash`) when its folder exists; if recycling fails (e.g., a file inside is open in another program), an error dialog appears and the project stays listed.
- Selecting a project whose folder was deleted recreates the folder — same as setting the project fresh.

## Timer questions

- **▶ Start timer** and OBS recording share the same session timer: whichever comes first opens segment 1, and each later start opens the next numbered segment.
- Stopping the timer (in-app or via OBS stop) locks new-timestamp creation until a timer starts again.
- While the manual ▶ timer runs, ⏸ Pause freezes the clock and timestamp times for the same segment until ▶ Resume (disabled while OBS records; segment wall duration still includes the paused time and pause does not survive a restart).
- Existing timestamps stay clickable while locked/paused: pending rows record notes (blocked only while paused for new creation), completed rows play back.

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

## "No microphone input detected" stops my recording

That is the silence watchdog doing its job: the 🎤 level bar beside the status bar never moved because the selected input delivered (almost) no signal for the whole take, so the app discarded the completely silent capture instead of saving an empty file. Any detected input disarms the watchdog for the rest of that take — pausing mid-recording never deletes audio you already recorded; an aborted retake on an already-saved timestamp keeps that previous audio and stays green. Check, in order:

- The right device is selected in the microphone dropdown (a disconnected headset often lingers as a stale entry).
- The mic is not muted in Windows or on the device itself, and its input level is not set to 0.
- Windows microphone privacy permissions allow desktop apps.
- If you work in a very quiet room or speak softly, lower `mic_settings.silence_threshold` in `keybinds.json` (default `250.0`); raise it if background noise keeps truly dead recordings alive.
- To get more grace time before the auto-stop, raise `mic_settings.silence_timeout` (default `5.0` seconds); a very large value effectively disables the feature while keeping the level meter.

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

A project folder contains `<Project Name>.md`, `session.json`, `session.backup.json` (one-generation copy made before each save), WAV files, and a `Screenshots` folder with per-timestamp JPEGs. Keep the folder together when moving it so Markdown relative links and embedded images remain valid. If `session.json` is malformed, restore it from `session.backup.json`; `session.json` is the machine-readable source of truth.

## OBS stops while a note is recording

The app automatically finalizes the active microphone note, saves its WAV file, stops the timer, and locks timestamp creation. This only happens when a *live* connection actually drops (or OBS stops recording); while OBS is simply not running at all, ▶ timer sessions and recordings run indefinitely — that was a bug in earlier versions where every reconnect retry pass cut them off after a few seconds.

## Deleting a timestamp left files behind

- Deleting a timestamp now moves its WAV takes and screenshot to the Recycle Bin; if a file is locked the entry is kept and the status bar tells you which file blocked it so you can close the program holding it and retry. Paths outside the project folder are reported but do not block deletion.

## Diagnostic commands

```bash
python --version
python -m pip show customtkinter pynput obsws-python sounddevice
python -m unittest discover -s tests -v
python -m py_compile timestamp_gui.py timestamp_audio.py timestamp_obs.py timestamp_screenshot.py
```
