# Clickable Timestamp Recorder

![Clickable Timestamp Recorder](Clickable-Timestamp-Recorder-App-Image.png)

A Windows-first desktop app that connects to OBS, starts its timer with OBS recording (main output or Aitum Vertical), and attaches microphone recordings to clickable timestamps.

## Features

- OBS WebSocket auto-connect with recording sync + ⚙ in-app connection editor (host/port/password)
- ▶/■ Built-in timer (works without OBS) + ⏸ Pause for manual sessions (same-segment resume)
- Clickable timestamp workflow: pending → recording → playback
- Per-recording segment groups with restarting numbering
- 📼 button per segment to open its main OBS recording
- Microphone WAV recording and playback, with re-recordable takes per timestamp
- Live mic level meter with silence detection (auto-stop & retry on completely silent takes — pauses during speech never delete recorded audio)
- Per-timestamp 720p screenshots
- Labels, tag library, and tag propagation
- Replay-buffer logging (passive)
- Aitum Vertical support: vertical recordings and Backtrack saves drive the timer like main ones
- Recent projects for one-click switching (🗑 delete, ✎ rename) + 🔍 toolbar filter + contextual empty states
- Editable timestamp times (HH:MM:SS in the ✎ dialog) + Recycle-Bin deletion + `session.backup.json` backup
- Markdown project log with media links
- Configurable global hotkey (keyboard & mouse)
- Manual timestamps from typed time

## Project output

After choosing an output folder and project name:

```text
<output folder>/
└── My Project/
    ├── My Project.md
    ├── session.json
    ├── R01-001_00-12-34.wav
    ├── R01-002_00-18-05.wav
    └── Screenshots/
        ├── R01-001_00-12-34.jpg
        └── R01-002_00-18-05.jpg
```

Completed recordings are linked from the Markdown file, which groups timestamps under one section per OBS recording. Each timestamp that captured a screenshot embeds it directly below its entry:

```markdown
### [21-08][14-55-19]

- Footage: [[21-08][14-55-19].mp4](file:///E:/Videos/%5B21-08%5D%5B14-55-19%5D.mp4)

- 001 [00:12:34](R01-001_00-12-34_2.wav) — completed, 3.8s — "Good take" #kill #bug
  - ![[R01-001_00-12-34.jpg]]
  - Take 1: [R01-001_00-12-34.wav](R01-001_00-12-34.wav) (4.2s)
- 002 00:18:05 — pending
  - ![[R01-002_00-18-05.jpg]]
```

Labels are optional short notes, and tags come from the preset list in `keybinds.json`.

## Requirements

- Python 3.x on Windows
- OBS Studio with WebSocket enabled
- A microphone

Install dependencies:

```bash
python -m pip install customtkinter pynput obsws-python sounddevice mss pillow send2trash
```

Enable OBS WebSocket from **Tools → WebSocket Server Settings**. The default connection is `localhost:4455`; click ⚙ in the header to edit host, port, password, and auto-connect (saved to `keybinds.json`), or hand-edit the file.

Connecting is automatic: while OBS is closed the header shows amber `● Waiting for OBS…` and the app retries every few seconds; once OBS starts it connects on its own, and if OBS exits mid-session it reconnects when OBS comes back. Clicking **Disconnect** pauses auto-connect until you click **Connect OBS** again. A wrong password now shows a red `● OBS auth failed` header and a status-bar hint instead of hiding behind the amber dot.

## Run from source

```bash
python timestamp_gui.py
```

## Build the executable

```bash
pyinstaller --clean --noconfirm timestamp_gui.spec
```

The executable is created at `dist/timestamp_gui_lite.exe`. The spec bundles the PortAudio runtime required by microphone recording and the mss/Pillow runtime used by timestamp screenshots.

## Workflow

1. Choose an output folder.
2. Enter a project name and click **Set project**.
3. Select a microphone. If **System default** does not work, choose the explicit input device shown with its index/backend.
4. Change the timestamp hotkey if desired — click it, then press a key or Mouse 4/5 (Esc cancels).
5. Start recording in OBS — the app connects and follows automatically (the main recording and an Aitum Vertical recording both work; overlapping ones share one timer session) — or click **▶ Start timer** to run a session without OBS.
6. Press the timestamp hotkey or click **New timestamp**, or click **＋ Manual** to create one from a typed time. Each new timestamp also saves a small JPEG snapshot of your main monitor into the project's `Screenshots` folder.
7. Click a pending timestamp to start microphone recording; a 🎤 level bar beside the status bar shows the input level, and if the mic picks up nothing at all the silent take is discarded and the row becomes recordable again.
8. Click the active timestamp to stop and save its WAV file.
9. Click a saved timestamp to play or stop it.
10. Use **✎** to add a label, tags, and correct the time (HH:MM:SS) for any timestamp — its **Audio takes** section also lets you re-record a new take, audition old ones, switch the active take (**★ Use**), or delete individual takes. **✕** deletes the whole entry (all takes plus the screenshot are moved to the Recycle Bin; locked files keep the entry so you can retry).
11. Save a replay buffer in OBS (default hotkey) while connected: it is logged automatically as a 🎬 REPLAY entry at the current time. Click it to record a microphone note exactly like a timestamp, use **🎬** on the row to watch the replay video, and **✎**/**✕** to tag or remove it.
12. Use the **📼** button next to a recording's section header to watch that segment's full OBS video; segments recorded without OBS stay greyed out.
13. Filter with 🔍 above the list (label, tag, ref, or time substring) and edit times directly in the ✎ dialog; recent projects can be renamed via ✎ in the popup (beside 🗑). While the manual ▶ timer runs, ⏸ Pause freezes the clock and timestamp times for the same segment until ▶ Resume (disabled while OBS records). Each `session.json` save also keeps a one-generation `session.backup.json`.

Creating new timestamps requires a running timer — either an active OBS recording or the in-app **▶ Start timer** button. When no timer runs, existing timestamps stay fully usable: pending notes can still record audio and saved notes play back; only creating new entries waits for the next timer start.

Replay saves are logged even while the timer is stopped (they attach to the most recent segment), but a project must be selected — without one the save is only announced in the status bar. Aitum Vertical's **Backtrack** replay saves are logged the same way (main OBS replay saves and vertical ones both count; a vertical Backtrack entry only appears when its fresh clip can be located).

Tip: click the **project name field** to reopen one of your five most recent projects instantly — each entry shows how many timestamps and recordings the project holds (read live from its log, not the folder path), and a 🗑 button deletes a project by moving its folder to the Recycle Bin after a confirmation while ✎ renames it (folder, markdown, and `session.json` updated). Typing a new name works as usual. If the app connects while OBS is already recording, it detects the active OBS recording and starts the timer after a project is selected; that segment's header falls back to `Recording N` until the recording stops and the real file name becomes known.

## Out of scope

This MVP intentionally does not include Gemini, DaVinci Resolve export, HUD overlays, video recording, in-app replay-buffer controls, transcription, or a database/server. (The app only *logs* OBS replay saves; it never starts/stops OBS outputs.) Per-timestamp screenshots were added by explicit product decision.

## License

MIT License; see [LICENSE](LICENSE).
