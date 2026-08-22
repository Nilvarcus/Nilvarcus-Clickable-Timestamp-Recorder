"""Temporary diagnostic: connect to OBS like the app does and watch events.

Run while OBS is open. Prints protocol version, replay-buffer status, record
status, then every event received for LISTEN_SECONDS.
"""

import json
import sys
import time

from obsws_python import ReqClient, EventClient

LISTEN_SECONDS = 30


def load_obs_settings():
    try:
        with open("keybinds.json", encoding="utf-8") as handle:
            config = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        config = {}
    settings = config.get("obs_settings", {})
    return (
        settings.get("host", "localhost"),
        int(settings.get("port", 4455)),
        settings.get("password", ""),
    )


def main():
    host, port, password = load_obs_settings()
    print(f"Connecting to ws://{host}:{port} ...")
    req = ReqClient(host=host, port=port, password=password, timeout=5)

    version = req.get_version()
    print(f"OBS version      : {version.obs_version}")
    print(f"websocket version: {version.obs_web_socket_version}")

    try:
        status = req.get_replay_buffer_status()
        print(f"replay buffer active: {bool(status.output_active)}")
    except Exception as exc:
        print(f"get_replay_buffer_status FAILED: {exc}")
        print(">>> The replay buffer output may be disabled in OBS. <<<")

    try:
        record = req.get_record_status()
        print(f"record active    : {bool(record.output_active)}")
    except Exception as exc:
        print(f"get_record_status failed: {exc}")

    seen = []
    event = EventClient(host=host, port=port, password=password)

    def on_any_event(data):
        # Registered under many names? No: this fires for any event whose
        # snake_case name is 'on_...'. We register per-type below instead.
        pass

    # Register one named handler per expected event so the library's
    # name-based dispatch shows exactly which events arrive.
    def on_record_state_changed(data):
        print(f"EVENT RecordStateChanged state={getattr(data, 'output_state', None)}")

    def on_replay_buffer_saved(data):
        print(f"EVENT ReplayBufferSaved saved_replay_path={getattr(data, 'saved_replay_path', '<MISSING>')!r}")

    event.callback.register([on_record_state_changed, on_replay_buffer_saved])
    registered = event.callback.get()
    print(f"registered callbacks -> dispatch names: {registered}")
    print(f"Listening for {LISTEN_SECONDS}s ... press the OBS Save-Replay hotkey NOW.")

    deadline = time.time() + LISTEN_SECONDS
    try:
        while time.time() < deadline:
            time.sleep(0.5)
    finally:
        event.disconnect()
        req.disconnect()
        print(f"done; events seen: {len(seen)}")


if __name__ == "__main__":
    sys.exit(main())
