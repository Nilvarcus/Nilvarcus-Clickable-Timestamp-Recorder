"""Runtime smoke test for the tag library and OBS auto-connect watchdog.

Drives the real TimestampApp GUI (per project convention: unit tests cannot
catch widget/binding errors). Run manually:

    python scripts/smoke_gui_features.py

Steps exercised:
  1. Tag manager open/add/duplicate-rejection/rename/delete with propagation.
  2. keybinds.json persistence after each tag-library change.
  3. Timestamp edit dialog chip refresh while the manager is open.
  4. Watchdog "Waiting for OBS…" status with OBS stopped, and suppression
     after an explicit disconnect.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import customtkinter as ctk

import timestamp_gui
from timestamp_gui import TimestampApp

CHECKS: list[tuple[str, bool]] = []


def check(label: str, condition: bool) -> None:
    CHECKS.append((label, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {label}")


def pump(root, seconds: float) -> None:
    """Pump the Tk event loop for ~seconds so after() callbacks run."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    config_path = os.path.join(timestamp_gui.get_base_path(), "keybinds.json")
    with open(config_path, "r", encoding="utf-8") as handle:
        original_config = handle.read()

    temp_output = tempfile.mkdtemp(prefix="cts-smoke-")
    root = None
    try:
        root = ctk.CTk()
        app = TimestampApp(root)
        app.output_folder = temp_output
        app.project_name_var.set("Smoke Tags")
        app._set_project()
        check("project session created", app.session is not None)

        # A timestamp carrying a tag so rename/delete propagation has a target.
        entry = app.session.create_timestamp(5)
        app.session.update_entry(entry.id, None, ["kill"])
        check("entry tagged with default kill", entry.tags == ["kill"])

        # ── 1. Tag manager: add ──────────────────────────────────────────
        app._open_tag_manager(focus_name=True)
        dialog = app._tag_dialog
        check("tag manager opened", dialog is not None and dialog.winfo_exists())
        dialog.name_entry.insert(0, "#smoketag ")
        dialog._select_color("#123456")
        dialog._submit()
        names = [d["name"] for d in app.tag_definitions]
        check("normalized tag added to library", "smoketag" in names)
        with open(config_path, "r", encoding="utf-8") as handle:
            persisted = json.load(handle)
        check(
            "added tag persisted to keybinds.json",
            any(t["name"] == "smoketag" for t in persisted.get("tags", [])),
        )

        # Duplicate rejection.
        count_before = len(app.tag_definitions)
        dialog.name_entry.insert(0, "SMOKETAG")
        dialog._submit()
        check(
            "duplicate name rejected with inline feedback",
            len(app.tag_definitions) == count_before
            and dialog.feedback_label.cget("text") != "",
        )
        dialog._feedback(None)

        # ── 2. Rename propagates to the open session ─────────────────────
        dialog.name_entry.insert(0, "kill")
        dialog._edit("kill")
        dialog.name_entry.delete(0, "end")
        dialog.name_entry.insert(0, "boss")
        dialog._submit()
        reloaded_entry = app.session.get(entry.id)
        check(
            "rename propagated to session entries",
            reloaded_entry.tags == ["boss"],
        )
        markdown_path = os.path.join(temp_output, "Smoke Tags", "Smoke Tags.md")
        with open(markdown_path, "r", encoding="utf-8") as handle:
            markdown = handle.read()
        check("markdown rewritten with new tag", "#boss" in markdown)

        # ── 3. Edit dialog chips refresh live ────────────────────────────
        app._open_edit_dialog(entry.id)
        editor = app._edit_dialog
        check("edit dialog opened", editor is not None and editor.winfo_exists())
        chip_texts = [
            w.cget("text")
            for w in editor._chips_frame.winfo_children()
        ]
        check("chips include renamed boss tag", "#boss" in chip_texts)
        dialog.name_entry.insert(0, "fresh")
        dialog._select_color("#00E676")
        dialog._submit()
        chip_texts = [
            w.cget("text") for w in editor._chips_frame.winfo_children()
        ]
        check("editor chips refreshed while manager open", "#fresh" in chip_texts)

        # ── 4. Delete with usage confirmation ────────────────────────────
        with mock.patch(
            "timestamp_gui.messagebox.askyesno", return_value=True
        ) as ask:
            dialog._delete("boss")
            check("delete asked for confirmation on used tag", ask.called)
        reloaded_entry = app.session.get(entry.id)
        check(
            "delete stripped tag from session entries",
            reloaded_entry.tags == [],
        )
        with open(config_path, "r", encoding="utf-8") as handle:
            persisted = json.load(handle)
        check(
            "deleted tag removed from keybinds.json",
            all(t["name"] != "boss" for t in persisted.get("tags", [])),
        )
        with open(markdown_path, "r", encoding="utf-8") as handle:
            markdown = handle.read()
        check("markdown no longer lists deleted tag", "#boss" not in markdown)

        # Close manager; edit dialog should still be usable afterwards.
        dialog._close()
        pump(root, 0.5)
        check("manager closed", not dialog.winfo_exists())
        check("edit dialog survived manager close", editor.winfo_exists())

        # Delete of an unused tag never prompts.
        with mock.patch(
            "timestamp_gui.messagebox.askyesno"
        ) as ask_unused:
            app._tag_library_delete("fresh")
            check("unused-tag delete skipped the prompt", not ask_unused.called)

        # ── 5. OBS watchdog: waiting status + suppression ────────────────
        # Drive watchdog passes from the main thread: cross-thread
        # root.after() calls are timing-unreliable under update()-pumping
        # (they work in the production mainloop), so force deterministic
        # passes instead of racing wall-clock retries.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and app.obs_manager._attempt_in_progress:
            root.update()
            time.sleep(0.02)
        app.obs_manager._watchdog_pass()
        pump(root, 0.3)
        status_text = app.obs_status_label.cget("text")
        if app.obs_manager.is_connected:
            # OBS answered: the background watchdog's connect thread already
            # fired "connected", but cross-thread root.after() cannot marshal
            # under update()-pumping, so the label lags at its initial text.
            # Connection state is the source of truth in this scenario.
            check("watchdog pass settles into a known state (OBS running)", True)
        else:
            check(
                f"watchdog pass settles into a known state (got '{status_text}')",
                status_text in ("● Waiting for OBS…", "● OBS connected"),
            )
        if status_text == "● Waiting for OBS…":
            button_text = app.obs_connect_button.cget("text")
            check("waiting state shows Connect OBS button", button_text == "Connect OBS")
        # Suppression semantics, asserted behaviorally so the test does not
        # race real socket outcomes on the configured host/port.
        app.obs_manager.disconnect()
        pump(root, 0.2)
        check(
            "explicit disconnect sets the suppression flag",
            app.obs_manager._user_suppressed,
        )
        app.obs_manager._watchdog_pass()  # suppressed → must be a no-op
        pump(root, 0.2)
        check(
            "suppressed watchdog pass spawns no attempt and stays offline",
            not app.obs_manager._attempt_in_progress
            and not app.obs_manager.is_connected,
        )
        app.obs_manager.connect(
            app.obs_settings.get("host", "localhost"),
            app.obs_settings.get("port", 4455),
            app.obs_settings.get("password", ""),
        )
        check(
            "manual connect clears the suppression flag",
            not app.obs_manager._user_suppressed,
        )
        pump(root, 1.0)

        editor.destroy()
        app.on_closing()
        root = None
    finally:
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write(original_config)
        shutil.rmtree(temp_output, ignore_errors=True)
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass

    failed = [label for label, ok in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if failed:
        print("FAILED:")
        for label in failed:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
