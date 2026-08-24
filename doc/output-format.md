# Output format

## Project folder

```text
<output folder>/<safe project name>/
├── <Project Name>.md
├── session.json
├── R##-###_HH-MM-SS.wav
├── R##-###_HH-MM-SS.txt      # transcript (only via the optional transcribe script)
├── R##-###_HH-MM-SS.vtt      # optional VTT (same script, when --outdir used)
└── Screenshots/
    └── R##-###_HH-MM-SS.jpg
```
The project name is displayed in Markdown as entered. Invalid Windows path characters are sanitized for the folder and Markdown filename.

## Recording segments

Each OBS recording start opens a numbered segment inside the project. Segments are named after the recording file OBS reports over WebSocket (for example `[21-08][14-55-19]` from `[21-08][14-55-19].mp4`); when the name is not known yet, the segment falls back to `Recording N`. Timestamps carry their segment number and a per-segment index, so numbering restarts at 001 for every recording. Timestamps created before any recording (or migrated from older session files) are ungrouped and shown under "Earlier timestamps".

A segment header falls back to `Recording N` when the app joins an already-running recording; it is renamed to the real file name when that recording stops. Each segment also keeps the absolute OBS video path once OBS reports one (`RecordingInfo.path`, filled by the start event and confirmed by the stop event); timer-only segments opened from the in-app ▶/■ toggle never get one.

## Markdown log

A new project starts with:

```markdown
# My Project

## Timestamps

_No timestamps yet._
```

Timestamps are grouped under one `###` section per segment, in chronological order ("Earlier timestamps" first). A pending timestamp is represented as:

```markdown
### [21-08][14-55-19]

- 001 00:12:34 — pending
```

Labels and tags appear as a suffix. The label is quoted; tags are rendered as `#name`:

```markdown
### [21-08][14-55-19]

- 001 [00:12:34](R01-001_00-12-34.wav) — completed, 4.2s — "Good take" #kill #bug
  - ![[R01-001_00-12-34.jpg]]
- 002 [00:18:05](R01-002_00-18-05.wav) — pending — "Check this later" #idea
  - ![[R01-002_00-18-05.jpg]]
- 003 [00:21:40](R01-003_00-21-40.wav) — pending
```

When a segment's recording video path is known, its header gets a `Footage:` link directly below it (the same line shape replay entries use), so the main recording is clickable outside the app too:

```markdown
### [21-08][14-55-19]

- Footage: [[21-08][14-55-19].mp4](file:///E:/Videos/%5B21-08%5D%5B14-55-19%5D.mp4)

- 001 [00:12:34](R01-001_00-12-34.wav) — completed, 4.2s
```

Segments without a stored path render exactly as before — plain header, no Footage line.

Ungrouped entries keep the legacy line form without a number prefix:

```markdown
### Earlier timestamps

- 00:30:10 — pending
```

Replay entries (automatic OBS replay-buffer saves) sit inline between timestamps in the same segment numbering and carry the replay file's name, its state, and an absolute `file:///` link to the video:

```markdown
### [21-08][17-40-00]

- 001 00:02:10 — pending — "Setup"
- 002 00:04:15 — 🎬 Replay: replay- [21-08][17-44-10], pending
  - Footage: [replay- [21-08][17-44-10].mp4](file:///E:/Videos/replay-%20%5B21-08%5D%5B17-44-10%5D.mp4)
- 003 [00:06:20](R01-003_00-06-20.wav) — completed, 6.1s — "Nice play" #kill
```

After a mic note is recorded on a replay entry, the elapsed time links to the WAV like any other entry. The `Footage` line always points at the OBS-saved replay video where it lives on disk (percent-encoded so spaces and brackets stay valid Markdown).

Errors include an indented error detail below the timestamp (below the screenshot line when one exists). The app itself never transcribes; transcripts are written only by the optional offline script `scripts/transcribe_timestamps.py`. When present, they appear as an indented fenced block below the screenshot/error lines (no label, to save tokens):

```markdown
- 001 [00:12:34](R01-001_00-12-34.wav) — completed, 4.2s
  - ![[R01-001_00-12-34.jpg]]
    ```
    Hello, hello. Test, test, test.
    ```
```

The Markdown is regenerated from `session.json` on every session state save.

Timestamps recorded more than once keep every take; each non-active take is listed as an indented link below the screenshot/error lines (the active take is already the headline link):

```markdown
- 001 [00:12:34](R01-001_00-12-34_2.wav) — completed, 3.8s
  - ![[R01-001_00-12-34.jpg]]
  - Take 1: [R01-001_00-12-34.wav](R01-001_00-12-34.wav) (4.2s)
```

Deleting a timestamp removes its entry from the log and `session.json` and also deletes every audio take plus the screenshot JPEG from disk; any `.txt`/`.vtt` transcript files left by the optional transcribe script stay on disk. Individual takes can also be deleted from the edit dialog without touching the rest of the entry.

## Session JSON

`session.json` stores:

- format version (`6`);
- project name;
- timer start metadata and running state;
- the next recording-segment number; and
- recording segments, each with its number, display name (OBS file name when known), start time, end time once stopped, and the absolute OBS video path when reported (`path`).

Each timestamp entry stores:

- ID and elapsed seconds;
- creation time;
- pending/recording/completed/error status;
- entry kind (`"timestamp"`, or `"replay"` for automatic replay-buffer entries);
- optional relative audio filename;
- recording duration;
- optional `takes` list holding every retake as `{file, duration_seconds, created_at}` entries (oldest first); `audio_file`/`duration_seconds` above always point at the *active* take;
- error message when applicable;
- optional label (single-line text);
- optional tag names (list of strings);
- optional relative screenshot filename (`screenshot_file`, set once capture finishes);
- optional transcript text (`transcript`, normalized multi-line, capped at 10k chars); and
- its segment number and per-segment index (absent/empty for ungrouped entries).

Replay-kind entries additionally store `replay_file`, the absolute path of the OBS-saved replay video they were created from; regular timestamps leave it unset.

It is the machine-readable source of truth for restoring the timestamp list. Version 1 files without labels or tags load unchanged with empty annotations; version 1–3 files have no screenshot fields, so their entries load with `screenshot_file` unset and the next save writes version 4; version 4 files load with `transcript` as `null` and the next save writes version 5; version 5 files have no kind fields, so their entries load as regular timestamps and the next save writes version 6. The `takes` list is optional within version 6: files written before re-recording existed load unchanged, entries that already carry audio synthesize their single take, and the next save persists the explicit list.

## WAV files

Audio uses mono signed 16-bit PCM WAV at 44,100 Hz. New filenames contain the segment number, per-segment index, and timestamp position — `R{nn}-{iii}_{HH-MM-SS}.wav` (for example `R02-001_00-12-34.wav`). Entries without a segment keep the legacy `{id}_HH-MM-SS.wav` scheme. Existing files are never overwritten; a numeric suffix is added if necessary. Re-recording a timestamp (an additional take) follows the same rule, producing `…_2.wav`, `…_3.wav`, … so earlier takes stay on disk until they are deleted individually or the whole entry is removed.

## Screenshot files

Every new timestamp captures one JPEG snapshot of the main monitor for context, stored under `Screenshots/` inside the project folder. Filenames mirror the WAV scheme with a `.jpg` extension (`R02-001_00-12-34.jpg`, legacy `{id}_HH-MM-SS.jpg`) and are made unique with a numeric suffix instead of overwriting. Captures are scaled to at most 720 px height while preserving aspect ratio (smaller monitors keep their native size) and saved as JPEG quality 100 via mss + Pillow. Capture runs on a background thread after the timestamp exists; a failed capture leaves the entry without a screenshot and never blocks creation. Deleting a timestamp recycles its JPEG to the Recycle Bin together with every audio take; if a file is locked the entry is kept and the status bar names the blocking file.

In the Markdown log each screenshot is embedded as an Obsidian wikilink using only the filename (`![[R02-001_00-12-34.jpg]]`), so Obsidian resolves it vault-wide without any folder path; other Markdown viewers can find the file under the project's `Screenshots/` folder.
