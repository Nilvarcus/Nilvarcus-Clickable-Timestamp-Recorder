# User guide

## Install

```bash
python -m pip install customtkinter pynput obsws-python sounddevice mss pillow
```

OBS Studio and a microphone are required for normal use.

## Start

From the project root:

```bash
python timestamp_gui.py
```

Or launch the latest packaged build at `dist/timestamp_gui_lite.exe`.

## First project

1. Choose an output folder.
2. Enter a project name and click **Set project**.
3. Select a microphone. Use **Refresh** to rescan devices.
4. Click the current **Timestamp hotkey** and press a replacement key if desired.
5. Connect OBS. Connecting is automatic: while OBS is closed the header shows amber `● Waiting for OBS…` and the app retries every few seconds, so this step is just "have OBS running" — and if OBS restarts later, the app follows on its own.
6. Start the main OBS recording.

The timer starts automatically when OBS starts recording. The **New timestamp** button and configured hotkey become available. To work without OBS, click **▶ Start timer** instead — see [Timer without OBS](#timer-without-obs).

Clicking the **project name field** opens a popup listing your five most recent projects; pick one to reopen it instantly (its output folder and session load together). Typing a fresh name works as usual — any keypress dismisses the popup.

## Timer without OBS

OBS is optional:

1. Set a project as usual.
2. Click **▶ Start timer** (it turns into ■ Stop timer and turns red). The header clock runs and the `● REC N` indicator appears, exactly like an OBS-driven session.
3. Create timestamps with the hotkey or button; each one captures its screenshot and records microphone notes normally.
4. Click **■ Stop timer** to lock new-timestamp creation — identical to an OBS stop.
5. Starting again opens the next numbered segment (`Recording 2`, `Recording 3`, …).

If OBS starts while the manual timer already runs, the live segment simply continues; when that OBS recording stops, its file name still names the segment before locking.

## Capture a timestamp note

1. Press the timestamp hotkey or click **New timestamp**. Each new timestamp also saves a small (720p-height) JPEG snapshot of your main monitor into the project's `Screenshots` folder — no preview pops up; the file just appears in the folder, in the Markdown log, and behind the row's **📷** button, which opens it in your default image viewer.
2. Find the new pending timestamp in the list.
3. Click it to start microphone recording.
4. Speak your note.
5. Click the recording row again to stop and save the WAV file.
6. Click the completed row to play or stop the recording.

When the timer stops — from OBS or the in-app button — the app finalizes an active note and locks only *creating new* timestamps. Existing rows stay usable: pending notes can still record audio and saved notes play back anytime.

### Reading the timestamp list

Each timestamp is one compact row. A colored dot shows the state: grey pending, red recording (or error), green saved, blue while playing, violet for replay entries. The row itself is the button — clicking anywhere on it records, stops, or plays exactly as before. After the `R##-###` reference and time you'll see the entry's label in quotes and up to three tag chips (more tags collapse into a `+N` chip). The small icons at the end open the timestamp's screenshot (📷, timestamps only), open the replay video (🎬, replays only), edit label/tags (✎), and delete the entry (✕). The **📷** icon stays greyed out until the snapshot has finished saving (and on older entries created before screenshots existed); if the JPEG was moved or deleted, clicking it shows an error in the status bar instead.

The list follows you while you work: as long as you're already near the bottom, every new timestamp (hotkey, ＋ button, manual entry, or OBS replay save) scrolls itself into view. Scroll up to read older entries and it stops following — scroll back down and it picks up where it left off. Opening or switching a project always lands you on the latest segment.

## Replay-buffer saves

If OBS has its replay buffer enabled, every time you save a replay (OBS default hotkey) while the app is connected, a violet **🎬 REPLAY** entry appears automatically between your timestamps, named after the replay file (for example `replay- [21-08][17-44-10]`).

- Treat it like any timestamp: click to record a microphone note, click again to stop, then click to play.
- The **🎬** button on the row opens the replay video in your default video player; if the file was moved or deleted, the status bar shows an error.
- Use **✎** to add a label and tags, and **✕** to delete the entry (its microphone-note WAV is deleted with it, while the replay video is never touched).
- Saves are logged even when the timer is stopped — they attach to the most recent recording segment. A project must be selected, otherwise the save is announced in the status bar only.
- Saving the same replay twice never creates two entries.
- Replays are detected from the moment OBS connects; earlier saves are not backfilled.

The Markdown log shows each replay with its name and a `Footage:` link straight to the video file.

## Label, tag, delete, and manual timestamps

- **Label and tags**: click **✎** on any timestamp to open a dialog with an optional short label and tag chips (defaults: `kill`, `bug`, `idea`). Labels and tags appear in the timestamp list and in the Markdown log as `"label" #tag`. The actively recording row cannot be edited.
- **Tag library**: click **🏷 Tags** in the header — or **＋ New tag** inside the timestamp edit dialog — to open the tag manager. Add tags (name plus a palette or custom color), rename, recolor, or delete them; names are capped at 24 characters and must be unique. Renaming a tag updates it on every timestamp in the open project (list, Markdown log, and `session.json`); deleting a tag that timestamps use asks for confirmation first and then strips it from them. Changes are saved to `keybinds.json` immediately, and the edit dialog's chips refresh live while the manager is open.
- **Delete**: click **✕** and confirm. The entry is removed from the list, Markdown, and `session.json`, and its WAV file and screenshot are deleted from disk; if a file cannot be deleted (for example it is still open in another program) it stays put and the status bar says so. The actively recording row cannot be deleted.
- **Manual timestamps**: click **＋ Manual** while a project is open — even when OBS is stopped — and type a time position as SS, MM:SS, or HH:MM:SS. The entry is created at that position so you can attach a note afterwards for a moment you missed live.
- **Tag storage**: the library lives in `keybinds.json` under `tags` as `{"name": ..., "color": "#RRGGBB"}` entries and is shared across projects; see [configuration.md](configuration.md). Hand-editing works too, but the dialog is the normal way.

## Project files

The selected project folder contains:

```text
Project/
├── Project.md
├── session.json
├── 001_00-12-34.wav
└── Screenshots/
    └── 001_00-12-34.jpg
```

`Project.md` is human-readable and links directly to completed audio; each captured timestamp embeds its screenshot right below its entry. `session.json` restores the list and statuses when the project is selected again.

## Hotkey setting

Click the key button beside **Timestamp hotkey**, then press a keyboard key or an auxiliary mouse button (Mouse 4, Mouse 5, or middle click) and it becomes active immediately. Left/right clicks are deliberately excluded so everyday clicking can never fire a timestamp. Press `Esc` to cancel. The selection is saved in `keybinds.json` and displayed on the **New timestamp** button.

## Safe shutdown

Close the app normally. It saves project metadata, finalizes an active microphone note, stops playback, disconnects OBS, and stops listeners. An interrupted note is restored as pending on the next launch rather than pretending the hardware stream continued.
