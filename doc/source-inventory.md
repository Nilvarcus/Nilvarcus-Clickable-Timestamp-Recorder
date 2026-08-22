# Source inventory

## Active source

| File | Responsibility |
|---|---|
| `timestamp_gui.py` | Main GUI, project controls, OBS timer synchronization, replay-save logging, configurable hotkey, tag-library manager, timestamp list, and playback actions. |
| `timestamp_audio.py` | Timestamp/replay-entry model, recording segments, tag-library helpers and propagation, project persistence, Markdown generation, microphone recording, and playback. |
| `timestamp_obs.py` | OBS WebSocket connection with auto-reconnect watchdog, recording status, recording file path events, replay-buffer save events, and callbacks. |
| `timestamp_screenshot.py` | Main-monitor JPEG capture (mss + Pillow) for per-timestamp context shots; pure size helper and lazily imported dependencies. |
| `test_timestamp_audio.py` | Unit tests for core timestamp/audio-session behavior plus screenshot path/Markdown/capture tests. |
| `test_timestamp_obs.py` | Dependency-light unit tests for the OBS manager: socket-liveness probe across obsws-python client layouts (fail-open on unknown shapes) and recording start/stop transition guards. |

## Configuration and build

| File | Responsibility |
|---|---|
| `keybinds.json` | Local runtime settings; ignored by Git. |
| `timestamp_gui.spec` | PyInstaller build specification. |
| `.gitignore` | Ignores local configuration, project output, build/cache files, and IDE files. |

## Documentation and legal files

| File | Responsibility |
|---|---|
| `README.md` | User-facing setup and workflow. |
| `AGENTS.md` | Maintainer and coding-agent guide. |
| `Changelog.md` | Current MVP change summary. |
| `doc/` | Detailed user, architecture, configuration, output, integration, developer, troubleshooting, and inventory documentation. |
| `LICENSE` | MIT license. |

## Generated/local content

- `Timestamp_Audio/`: preserved local project output in this checkout; future output is ignored by Git.
- `dist/timestamp_gui_lite.exe`: preserved latest packaged executable.
- `build/`: disposable PyInstaller work directory; remove before source distribution.
- `__pycache__/`: disposable Python bytecode cache.
- `dist/Timestamp_Audio/`, `dist/Timestamp_TXT/`, and `dist/keybinds.json`: disposable runtime data from running a packaged copy.
