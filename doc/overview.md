# Project overview

## Purpose

Clickable Timestamp Recorder helps creators attach short microphone notes to exact moments in an OBS recording. Timestamps are created while OBS is recording, and each timestamp becomes a clickable UI item that starts/stops its associated audio note.

## Current capabilities

- CustomTkinter desktop interface.
- OBS WebSocket v5 connection and status display, with an automatic-reconnect watchdog that connects whenever OBS becomes reachable and reconnects after OBS restarts.
- Tag library: create, rename, recolor, and delete custom tags in-app; changes propagate to the open project.
- Timer driven by OBS recording start/stop events.
- Project name selection before recording.
- Project-specific folder with Markdown and JSON metadata.
- Configurable output folder and microphone device.
- WAV microphone capture through `sounddevice`.
- Clickable timestamp states: pending, recording, completed, and error.
- WAV playback from completed timestamps.
- Configurable global timestamp hotkey through `pynput`.
- Detection of OBS recordings that were already active when connecting.
- Built-in ▶/■ timer: full timestamp sessions without OBS open.
- Recent-projects popup: the five most recently opened projects load with one click from the project-name field.
- Per-timestamp context screenshots: a 720p-height main-monitor JPEG saved into `Screenshots/` and embedded in the Markdown log.

## Deliberate limitations

- Windows is the primary supported platform.
- OBS is required for timer-driven timestamps.
- Only one microphone recording can be active at a time.
- Audio is mono 16-bit PCM WAV.
- A process restart cannot resume an active microphone stream or OBS timer; interrupted entries return to pending state.
- Project Markdown links are local relative links and are intended to remain beside their WAV files.
- There is no cloud sync, database, multi-user mode, video capture, transcription, or editing system.
