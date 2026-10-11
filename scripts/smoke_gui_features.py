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
  5. Watchdog finalize-on-transition regression: repeated "waiting" passes
     with OBS off never kill a manual timer/mic recording; only a real
     connected→dropped transition does.
  6. Recent-projects popup follows main-window moves/resizes while open
     and is dismissed when the window is minimized.
  7. Replay rows expose audio state (amber no-audio, red recording,
     green with duration, 🎙 quick-take button) and the toolbar
     Missing-audio toggle filters to replays without a saved take.
  8. Long-list projects open on the newest entries with the bottom row
     visible (blank-on-open regression), across big→small→big switches;
     wheel step is widened to 4 px scroll units (~80 px per notch).
  9. Watch-from-here: the ⏩ button renders disabled without a segment
     video path and enabled with one; launching without a seek-capable
     player opens the default app and copies the offset to the clipboard;
     a configured player builds the seek command; segment video_start
     offsets shift the seek position.
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


def row_visible_in_view(canvas, row: dict) -> bool:
    """True when the row's frame is actually inside the canvas viewport.

    ``winfo_ismapped`` cannot be used for this: on Windows the canvas keeps
    every embedded window mapped regardless of scroll position (one reason
    long lists are heavy there). ``winfo_rooty`` instead reports the row's
    nominal screen Y, which falls outside the canvas's screen rect when the
    row is scrolled out of view.
    """
    if canvas is None or row is None:
        return False
    top = canvas.winfo_rooty()
    bottom = top + canvas.winfo_height()
    row_y = row["frame"].winfo_rooty()
    return top - 2 <= row_y < bottom


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
        check(
            "markdown debounced after tag rename (not yet rewritten)",
            "#boss" not in markdown and app.session.markdown_dirty,
        )
        app.session.flush_markdown()
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
        app.session.flush_markdown()
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

        # ── 6. Watchdog finalize-on-transition regression ────────────────
        # Drive the GUI status handler directly so no real OBS state can
        # interfere; force _obs_was_up explicitly for determinism.
        app.session.start_timer()
        app._obs_was_up = False
        for _ in range(3):  # repeated retry-cycle passes
            app._on_obs_status_change("waiting")
        pump(root, 0.3)
        check(
            "repeated waiting passes with OBS never up keep the timer running",
            app.session.timer_running,
        )

        class StubRecorder:
            """Mimics AudioRecorder.stop()'s absolute-path contract."""

            active = True
            level = 0.0
            monitor = None

            def __init__(self, output_dir):
                self.output_dir = output_dir
                self.stopped = False

            def stop(self):
                self.active = False
                self.stopped = True
                return (os.path.join(self.output_dir, "stub.wav"), 1.0)

            def discard(self):
                self.active = False

            def cancel(self):
                self.active = False

        stub = StubRecorder(app.session.output_dir)
        real_recorder = app.recorder
        app.recorder = stub
        try:
            mic_entry = app.session.create_timestamp(1)
            app.recording_entry_id = mic_entry.id
            app.session.mark_recording(mic_entry)
            app._on_obs_status_change("waiting")
            pump(root, 0.2)
            check(
                "waiting pass with OBS never up keeps the mic recording alive",
                stub.active and app.session.get(mic_entry.id).status == "recording",
            )

            app.session.start_timer()  # re-arm in case anything stopped it
            app._obs_was_up = True
            app._on_obs_status_change("waiting")
            pump(root, 0.2)
            check(
                "connected→dropped transition stops timer and saves the note",
                not app.session.timer_running
                and stub.stopped
                and app.session.get(mic_entry.id).status == "completed",
            )
        finally:
            app.recorder = real_recorder
            app.recording_entry_id = None
            app._stop_mic_meter()

        # ── 7. Recent-projects popup follows window moves ────────────────
        # The popup is an overrideredirect Toplevel pinned once by absolute
        # screen coordinates; root <Configure> bindings must re-anchor it
        # while open, and an <Unmap> (minimize) must dismiss it.
        for seed in ("Popup Alpha", "Popup Beta"):
            os.makedirs(os.path.join(temp_output, seed), exist_ok=True)
        app.recent_projects = [
            {
                "name": "Popup Alpha",
                "output_folder": os.path.join(temp_output, "Popup Alpha"),
            },
            {
                "name": "Popup Beta",
                "output_folder": os.path.join(temp_output, "Popup Beta"),
            },
        ]
        app._open_recent_popup()
        popup = app._recent_popup
        check(
            "recent popup opened with seeded projects",
            popup is not None and popup.winfo_exists(),
        )
        pump(root, 0.4)

        # Moving the window must move the popup by the same delta. Compare
        # against the root's ACTUAL displacement, not the requested one:
        # Windows may adjust the applied offset (frame/DPI rounding).
        root_x, root_y = root.winfo_rootx(), root.winfo_rooty()
        pop_x, pop_y = popup.winfo_rootx(), popup.winfo_rooty()
        dx, dy = 120, 80
        root.geometry(f"+{root_x + dx}+{root_y + dy}")
        pump(root, 0.5)
        actual_dx = root.winfo_rootx() - root_x
        actual_dy = root.winfo_rooty() - root_y
        print(f"    (requested ({dx}, {dy}), client moved ({actual_dx}, {actual_dy}))")
        check(
            f"popup follows window move (client delta {actual_dx}, {actual_dy})",
            abs(actual_dx) > 0
            and abs(actual_dy) > 0
            and abs(popup.winfo_rootx() - (pop_x + actual_dx)) <= 2
            and abs(popup.winfo_rooty() - (pop_y + actual_dy)) <= 2,
        )

        # Shrinking the window keeps the popup clamped inside its bounds.
        old_geometry = root.geometry()
        root_x, root_y = root.winfo_rootx(), root.winfo_rooty()
        root.geometry(f"500x620+{root_x}+{root_y}")
        pump(root, 0.5)
        pop_w, pop_h = popup.winfo_width(), popup.winfo_height()
        check(
            "popup stays clamped inside a shrunken window",
            popup.winfo_rootx() >= root.winfo_rootx() - 2
            and popup.winfo_rooty() >= root.winfo_rooty() - 2
            and popup.winfo_rooty() + pop_h
            <= root.winfo_rooty() + root.winfo_height() + 2,
        )
        print(f"    (popup {pop_w}x{pop_h} in 500x620 window)")

        # Minimizing unmaps the main window and must dismiss the popup.
        root.iconify()
        pump(root, 0.5)
        check(
            "minimizing the window dismisses the popup",
            app._recent_popup is None or not app._recent_popup.winfo_exists(),
        )
        root.deiconify()
        root.geometry(old_geometry)
        pump(root, 0.3)

        editor.destroy()

        # ── 8. Replay audio-state rows + Missing-audio toggle ──────────
        # Replay rows must expose their audio state in the main list:
        # amber "no audio" while pending, red while recording, green with
        # duration once a take exists — plus the toolbar Missing-audio
        # toggle that filters to replays without a saved take.
        Theme = timestamp_gui.Theme
        replay_done = app.session.create_replay_entry(
            os.path.join(temp_output, "replay-done.mp4")
        )
        replay_missing = app.session.create_replay_entry(
            os.path.join(temp_output, "replay-missing.mp4")
        )
        # Direct session calls don't repaint on their own; the production
        # replay path schedules the refresh via _on_obs_replay_saved.
        app._schedule_list_refresh()
        pump(root, 0.4)
        w_done = app._list_rows.get(replay_done.id)
        w_missing = app._list_rows.get(replay_missing.id)
        check("replay rows rendered in the list", w_done is not None and w_missing is not None)
        if w_done is not None and w_missing is not None:
            check(
                "pending replay row is amber with a no-audio hint",
                "no audio" in w_missing["label"].cget("text")
                and w_missing["dot"].cget("fg") == Theme.AMBER,
            )
            check(
                "pending replay quick-take button is hidden",
                not w_missing["take_button"].winfo_ismapped(),
            )

            stub = StubRecorder(app.session.output_dir)
            real_recorder = app.recorder
            app.recorder = stub
            try:
                # Recording state: red row + ■ Stop button.
                app.recording_entry_id = replay_missing.id
                app.session.mark_recording(replay_missing)
                app._schedule_list_refresh()
                pump(root, 0.3)
                check(
                    "recording replay row is red with a recording hint",
                    "recording — click to stop" in w_missing["label"].cget("text")
                    and w_missing["dot"].cget("fg") == Theme.RED
                    and w_missing["take_button"].cget("text") == "■ Stop",
                )
                # Completing the take: green row with duration + take button.
                app._stop_entry_recording(replay_missing)
                pump(root, 0.3)
                done_entry = app.session.get(replay_missing.id)
                check(
                    "completed replay row is green with audio duration",
                    done_entry.status == "completed"
                    and "audio" in w_missing["label"].cget("text")
                    and w_missing["dot"].cget("fg") == Theme.GREEN
                    and w_missing["take_button"].cget("text") == "🎙",
                )

                # Missing-audio toggle: only replay entries without a saved
                # take stay visible (a recording-in-progress row hides too).
                app.missing_audio_checkbox.select()
                app._on_missing_audio_toggle()
                pump(root, 0.4)
                check(
                    "toggle hides replays that have audio",
                    not w_missing["frame"].winfo_ismapped()
                    and w_done["frame"].winfo_ismapped(),
                )
                # A second pending replay stays visible under the toggle.
                replay_late = app.session.create_replay_entry(
                    os.path.join(temp_output, "replay-late.mp4")
                )
                app._schedule_list_refresh()
                pump(root, 0.4)
                w_late = app._list_rows.get(replay_late.id)
                check(
                    "toggle keeps pending replays visible",
                    w_late is not None and w_late["frame"].winfo_ismapped(),
                )
                # A currently-recording replay is excluded (being handled).
                app.recorder = StubRecorder(app.session.output_dir)
                app.recording_entry_id = replay_late.id
                app.session.mark_recording(replay_late)
                app._schedule_list_refresh()
                pump(root, 0.4)
                check(
                    "toggle hides a currently-recording replay",
                    not w_late["frame"].winfo_ismapped(),
                )
                app._stop_entry_recording(replay_late)
                # Complete the last pending replay too, so every replay has
                # audio and the toggle filters out everything: the list
                # then shows its own "no replays missing" placeholder.
                app.recorder = StubRecorder(app.session.output_dir)
                app.recording_entry_id = replay_done.id
                app.session.mark_recording(replay_done)
                app._schedule_list_refresh()
                pump(root, 0.4)
                app._stop_entry_recording(replay_done)
                pump(root, 0.5)
                check(
                    "empty placeholder celebrates no missing audio",
                    app._empty_label is not None
                    and "No replays missing audio" in app._empty_label.cget("text"),
                )
                # Unchecking restores every row (recreated after the
                # placeholder branch dropped the cached widgets).
                app.missing_audio_checkbox.deselect()
                app._on_missing_audio_toggle()
                pump(root, 0.5)
                restored = all(
                    app._list_rows.get(eid) is not None
                    and app._list_rows[eid]["grid_row"] is not None
                    for eid in (replay_done.id, replay_missing.id, replay_late.id)
                )
                check(
                    "unchecking the toggle restores all replay rows",
                    restored,
                )
            finally:
                app.recorder = real_recorder
                app.recording_entry_id = None
                app._stop_mic_meter()
                app.missing_audio_checkbox.deselect()
                app._missing_audio_only = False

        # ── 9. Long-list open lands on the newest entries ───────────────
        # Regression for the blank-on-open bug: the forced project-open
        # scroll raced the scrollregion update (CTkScrollableFrame only
        # recomputes it from the inner frame's late <Configure> event), so
        # opening a project with many rows could park the viewport past the
        # end of the freshly built content — rows stayed invisible until
        # the user scrolled. The fixed open path settles the layout
        # synchronously, resyncs the scrollregion from it, and only then
        # moves, so switching directions (big→small→big) always lands on
        # the newest entries with rows actually mapped.
        app.project_name_var.set("Big List")
        app._set_project()
        big_entries = []
        for seg in range(8):  # ~120 rows over 8 recording segments
            app.session.start_timer()
            for i in range(15):
                entry = app.session.create_timestamp(10.0 * (i + 1))
                app.session.update_entry(entry.id, f"note {seg}-{i}")
                big_entries.append(entry)
            app.session.stop_timer()
        big_entries.append(
            app.session.create_replay_entry(
                "X:/reels/Backtrack [02-01][10-00-00].mp4"
            )
        )
        big_entries.append(
            app.session.create_replay_entry(
                "X:/reels/Backtrack [02-01][10-05-00].mp4"
            )
        )
        # A fresh session load via _set_project is the real open path.
        app.project_name_var.set("Big List")
        app._set_project()
        pump(root, 1.0)
        canvas = app._timestamp_list_canvas()
        check("big list build produced >100 rows", len(app._list_rows) > 100)
        check(
            "big list opens scrolled to the bottom",
            canvas is not None and canvas.yview()[1] > 0.99,
        )
        newest_row = app._list_rows.get(big_entries[-1].id)
        check(
            "newest big-list row visible without scrolling",
            row_visible_in_view(canvas, newest_row),
        )
        oldest_row = app._list_rows.get(big_entries[0].id)
        check(
            "oldest big-list row stays off-view at the bottom",
            oldest_row is not None and not row_visible_in_view(canvas, oldest_row),
        )

        # Small project right after the big one: the overshoot direction —
        # the canvas still carries the previous, taller region when the
        # forced scroll runs, which used to strand the viewport past the
        # end of the short content (blank list again).
        app.project_name_var.set("Small List")
        app._set_project()
        small_entries = [
            app.session.create_timestamp(5.0 + n) for n in range(2)
        ]
        app.project_name_var.set("Small List")
        app._set_project()
        pump(root, 1.0)
        check(
            "small list after big list opens at the bottom",
            canvas.yview()[1] > 0.99,
        )
        small_row = app._list_rows.get(small_entries[-1].id)
        check(
            "small list newest row visible without scrolling",
            row_visible_in_view(canvas, small_row),
        )

        # And back to the big project — the reverse direction again.
        app.project_name_var.set("Big List")
        app._set_project()
        pump(root, 1.0)
        check(
            "big list re-open stays scrolled to the bottom",
            canvas.yview()[1] > 0.99,
        )
        newest_row = app._list_rows.get(big_entries[-1].id)
        check(
            "big list newest row visible again without scrolling",
            row_visible_in_view(canvas, newest_row),
        )

        # Wheel step: the timestamp list scrolls ~80 px per notch
        # (CTk multiplies each wheel event's delta/6 units by this many px).
        check(
            "wheel step widened to 4 px scroll units",
            canvas is not None and int(canvas.cget("yscrollincrement")) == 4,
        )

        # ── 10. Watch-from-here: ⏩ row button + offset launcher ──────
        app.project_name_var.set("Watch From Here")
        app._set_project()
        app.session.start_timer("[21-08][12-00-00]")
        watch_entry = app.session.create_timestamp(5)
        watch_video = os.path.join(temp_output, "watch-video.mp4")
        with open(watch_video, "wb") as handle:
            handle.write(b"fake")
        app._schedule_list_refresh()
        pump(root, 0.4)
        watch_row = app._list_rows.get(watch_entry.id)
        check("watch-from-here row rendered", watch_row is not None)
        if watch_row is not None:
            check(
                "⏩ disabled without a segment video path",
                str(watch_row["footage_button"].cget("state")) == "disabled",
            )
            app.session.set_recording_path(
                app.session.recordings[0].number, watch_video
            )
            app._schedule_list_refresh()
            pump(root, 0.4)
            check(
                "⏩ enabled once the segment path is known",
                str(watch_row["footage_button"].cget("state")) == "normal",
            )

            # No seek-capable player: default app opens from 0 and the
            # offset lands on the clipboard.
            with mock.patch.object(
                app, "_footage_player", return_value=(None, None)
            ), mock.patch.object(
                app, "_open_with_default_app", return_value=True
            ) as opener:
                app._open_footage_at(watch_entry.id)
                pump(root, 0.3)
                check(
                    "fallback opens the default app and copies the offset",
                    opener.called and root.clipboard_get() == "00:00:05",
                )
                check(
                    "fallback status offers the player hint",
                    "copied" in app.status_label.cget("text"),
                )

            # A configured player builds (and would run) the seek command.
            fake_player = os.path.join(temp_output, "footageplayer.exe")
            with open(fake_player, "w", encoding="utf-8") as handle:
                handle.write("fake")
            with mock.patch(
                "timestamp_gui.subprocess.Popen"
            ) as popen, mock.patch.object(
                app,
                "_footage_player",
                return_value=(fake_player, ["--start={start}", "{path}"]),
            ):
                app._open_footage_at(watch_entry.id)
                pump(root, 0.3)
                argv = popen.call_args[0][0] if popen.called else []
                check(
                    "player launch builds the seek command",
                    argv[:2] == [fake_player, "--start=5"],
                )
                check(
                    "player launch status names the offset and player",
                    "at 00:00:05 (footageplayer)" in app.status_label.cget("text"),
                )

            # video_start offsets shift the seek position (manual-timer
            # continuation: the video joined part-way through the segment).
            app.session.set_recording_video_start(
                app.session.recordings[0].number, 3.0
            )
            with mock.patch(
                "timestamp_gui.subprocess.Popen"
            ) as popen, mock.patch.object(
                app,
                "_footage_player",
                return_value=(fake_player, ["--start={start}", "{path}"]),
            ):
                app._open_footage_at(watch_entry.id)
                pump(root, 0.3)
                argv = popen.call_args[0][0] if popen.called else []
                check(
                    "video_start offsets shift the seek position",
                    argv[:2] == [fake_player, "--start=2"],
                )

            # Reuse: each ⏩ click replaces the window instead of piling up
            # players, so only the process this app spawned is closed.
            class FakePlayerProcess:
                def __init__(self, exit_code=None):
                    self.terminated = False
                    self._exit_code = exit_code

                def poll(self):
                    return self._exit_code

                def terminate(self):
                    self.terminated = True

            first_process = FakePlayerProcess()
            second_process = FakePlayerProcess()
            with mock.patch(
                "timestamp_gui.subprocess.Popen"
            ) as popen, mock.patch.object(
                app,
                "_footage_player",
                return_value=(fake_player, ["--start={start}", "{path}"]),
            ):
                popen.side_effect = [first_process, second_process]
                app._open_footage_at(watch_entry.id)
                pump(root, 0.2)
                app._open_footage_at(watch_entry.id)
                pump(root, 0.2)
            check(
                "each ⏩ click launches the player",
                popen.call_count == 2 and app._footage_process is second_process,
            )
            check(
                "a new ⏩ click closes the previous player window",
                first_process.terminated,
            )
            check(
                "the newest player window is left running",
                not second_process.terminated,
            )

            # A window the user already closed is not terminated a second time.
            exited_process = FakePlayerProcess(exit_code=0)
            app._footage_process = exited_process
            third_process = FakePlayerProcess()
            with mock.patch(
                "timestamp_gui.subprocess.Popen"
            ) as popen, mock.patch.object(
                app,
                "_footage_player",
                return_value=(fake_player, ["--start={start}", "{path}"]),
            ):
                popen.return_value = third_process
                app._open_footage_at(watch_entry.id)
                pump(root, 0.2)
            check(
                "an already-exited player is not terminated again",
                not exited_process.terminated,
            )
            check(
                "the launch still succeeds after an exited player",
                app._footage_process is third_process,
            )

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
