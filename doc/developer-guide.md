# Developer guide

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate   # Git Bash
.venv\Scripts\activate          # Command Prompt
python -m pip install customtkinter pynput obsws-python sounddevice mss pillow send2trash pyinstaller
```

Run from the project root:

```bash
python timestamp_gui.py
```

## Tests and checks

```bash
python -m unittest discover -s tests -v
python -m py_compile timestamp_gui.py timestamp_audio.py timestamp_obs.py timestamp_screenshot.py
```

The tests are dependency-light and cover timestamp formatting, safe filenames, persistence, Markdown links, interrupted sessions, device-query errors, tag-library helpers (`sanitize_tag_definitions`, `rename_tag`, `remove_tag`), screenshot path/Markdown rules, and capture scaling. Real microphone, OBS, GUI, and screen-capture smoke tests still require Windows hardware/software; the real-capture test skips automatically when mss/Pillow are missing.

For GUI-level changes, also run the manual smoke script, which drives the real app (tag manager add/rename/delete flows, edit-dialog chip refresh, watchdog waiting/suppression states) and restores `keybinds.json` afterwards:

```bash
python scripts/smoke_gui_features.py
```

## Packaging

```bash
pyinstaller --clean --noconfirm timestamp_gui.spec
```

The spec creates `dist/timestamp_gui_lite.exe` and bundles CustomTkinter, OBS WebSocket dependencies, pynput Windows modules, sounddevice, `_sounddevice_data` PortAudio DLLs, and the mss/Pillow screenshot runtime.

## Extension rules

- Keep GUI-independent session/audio behavior in `timestamp_audio.py`.
- Keep OBS protocol behavior in `timestamp_obs.py`; do not import Tkinter there.
- Route OBS and pynput callbacks into Tk with `root.after(0, ...)`.
- Update `session.json` and regenerate the Markdown log whenever persisted timestamp state changes. `session.json` writes are always synchronous; the Markdown regeneration may be debounced by the GUI (`TimestampSession.markdown_autoflush = False` + `flush_markdown()`, default `True` keeps everything synchronous for tests and direct consumers).
- Keep the timestamp list refresh cheap: rows are cached and carry change signatures — never reconfigure a row whose entry did not change, and never call `update_idletasks()` on refresh paths. The one sanctioned exception is the forced project-open scroll (`_scroll_timestamp_list_to_bottom(force=True)`): opening a project rebuilds hundreds of rows whose layout settles in waves, and the one synchronous layout pass there re-derives the scrollregion (`bbox("all")`) before the scroll so the viewport cannot land past the content end.
- Timestamp-list rows, headers, footers, and chips are deliberately plain tk widgets (the dark theme is locked, so hard-coded colors are safe); do not "restore" CTk rows and remember tk `width`/`height` are font-dependent units, not pixels — size icon buttons via `padx`/`pady`.
- Add tests for filename, persistence, or state changes before changing the data model.
- Update the relevant `doc/` page and README when behavior changes.
- Do not reintroduce removed integrations without an explicit product decision.

## Generated content

`build/`, `__pycache__/`, and packaged runtime output are generated. The latest executable may be retained for distribution, but generated build work should be removed before publishing source.
