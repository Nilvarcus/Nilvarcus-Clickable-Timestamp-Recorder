# Developer guide

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install customtkinter pynput obsws-python sounddevice mss pillow pyinstaller
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
- Update `session.json` and regenerate the Markdown log whenever persisted timestamp state changes.
- Add tests for filename, persistence, or state changes before changing the data model.
- Update the relevant `doc/` page and README when behavior changes.
- Do not reintroduce removed integrations without an explicit product decision.

## Generated content

`build/`, `__pycache__/`, and packaged runtime output are generated. The latest executable may be retained for distribution, but generated build work should be removed before publishing source.
