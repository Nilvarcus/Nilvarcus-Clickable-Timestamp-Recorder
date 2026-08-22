"""Temporary end-to-end check: real OBSManager + real TimestampSession.

Connects like the app does, then logs every replay-buffer save OBS sends into
a temp project exactly the way timestamp_gui does. Press the OBS Save-Replay
hotkey while it listens.
"""

import os
import sys
import tempfile
import time

from timestamp_audio import TimestampSession
from timestamp_obs import OBSManager

LISTEN_SECONDS = 75


def main():
    manager = OBSManager()

    def on_status(status):
        print(f"[status] {status}", flush=True)

    def on_replay_saved(replay_path):
        print(f"[replay event] path={replay_path!r}", flush=True)
        if not replay_path:
            print("  -> would show: file path unavailable", flush=True)
            return
        entry = session.create_replay_entry(replay_path)
        name = os.path.splitext(os.path.basename(entry.replay_file))[0]
        print(
            f"  -> LOGGED {name} as R-segment "
            f"{entry.recording_number}-index {entry.recording_index}",
            flush=True,
        )

    def on_rec_started(path=None):
        session.start_timer()
        print(f"[recording started] timer started ({path})", flush=True)

    def on_rec_stopped(path=None):
        session.stop_timer()
        print(f"[recording stopped] timer stopped ({path})", flush=True)

    manager.register_callbacks(
        on_status_change=on_status,
        on_recording_started=on_rec_started,
        on_recording_stopped=on_rec_stopped,
        on_replay_saved=on_replay_saved,
    )

    project = tempfile.mkdtemp(prefix="replay_e2e_")
    session = TimestampSession(os.path.join(project, "E2E"), "E2E", load_existing=False)
    print(f"project folder: {session.output_dir}", flush=True)

    manager.connect()
    deadline = time.time() + LISTEN_SECONDS
    try:
        while time.time() < deadline and not manager.is_connected:
            time.sleep(0.2)
        if not manager.is_connected:
            print("FAILED to connect to OBS", flush=True)
            return 1
        print(f"connected; replay buffer active = {manager.replay_buffer_active}")
        print(f">>> press OBS Save-Replay hotkey now ({LISTEN_SECONDS}s window) <<<")
        while time.time() < deadline:
            time.sleep(0.5)
    finally:
        manager.disconnect()

    print("--- final entries ---")
    for entry in session.entries:
        kind = "REPLAY" if entry.kind == "replay" else "stamp"
        print(f"  [{kind}] seg={entry.recording_number} idx={entry.recording_index} file={entry.replay_file}")

    md_files = [f for f in os.listdir(session.output_dir) if f.endswith(".md")]
    if md_files:
        markdown = open(os.path.join(session.output_dir, md_files[0]), encoding="utf-8").read()
        replay_lines = [ln for ln in markdown.splitlines() if "Replay:" in ln or "Footage:" in ln]
        print("--- markdown lines ---")
        for line in replay_lines:
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
