#!/usr/bin/env python3
"""
Transcribe completed WAVs in a Timestamp Recorder project and map transcripts
into session.json / Markdown.

Sequential wrapper around the whisper-transcriber skill:
  C:/Users/Nilva/.agents/skills/whisper-transcriber/scripts/transcribe.py

Usage:
  python scripts/transcribe_timestamps.py [project_dir] [options]
  python scripts/transcribe_timestamps.py                # defaults to dist/Timestamp_Audio/TEST2
  python scripts/transcribe_timestamps.py dist/Timestamp_Audio/TEST2 --model large-v3 --compute float16 --force

Each completed entry with an audio_file is transcribed once (skipped if
transcript already exists unless --force). Output .txt stays next to the WAV.
session.json (v5) stores the transcript and regenerates <Project>.md with a
dedicated block:

  - 001 [00:00:06](R01-001_00-00-06.wav) — completed, 9.3s
    - ![Screenshot](Screenshots\\R01-001_00-00-06.jpg)
    - Transcript:
      ```
      hello world
      ```
"""

from __future__ import annotations

import argparse
import os
import sys
import subprocess
from pathlib import Path

# Ensure repo root is on sys.path for timestamp_audio import
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from timestamp_audio import TimestampSession, clean_transcript  # noqa: E402

DEFAULT_PROJECT_DIR = REPO_ROOT / "dist" / "Timestamp_Audio" / "TEST2"
TRANSCRIBE_SCRIPT = Path("C:/Users/Nilva/.agents/skills/whisper-transcriber/scripts/transcribe.py")
TRANSCRIPT_MAX_PREVIEW = 120


def find_project_dir(arg_dir: str | None) -> Path:
    if arg_dir:
        p = Path(arg_dir).expanduser().resolve()
        return p
    # Default: prefer TEST2 if it exists, otherwise current working dir
    if DEFAULT_PROJECT_DIR.is_dir():
        return DEFAULT_PROJECT_DIR.resolve()
    cwd = Path.cwd().resolve()
    # If cwd already looks like a project (has session.json), use it
    if (cwd / "session.json").exists():
        return cwd
    return DEFAULT_PROJECT_DIR.resolve()


def build_transcribe_cmd(
    script: Path,
    wav_path: Path,
    model: str | None,
    compute: str | None,
    prompt: str | None,
    terms: str | None,
    no_vad: bool,
    outdir: str | None,
) -> list[str]:
    cmd = [sys.executable, str(script), str(wav_path)]
    if model:
        cmd += ["--model", model]
    if compute:
        cmd += ["--compute", compute]
    if prompt:
        cmd += ["--prompt", prompt]
    if terms:
        cmd += ["--terms", terms]
    if no_vad:
        cmd += ["--no-vad"]
    if outdir:
        cmd += ["--outdir", outdir]
    # WAVs are already 44.1kHz mono; transcribe.py will detect is_audio and skip extraction.
    # Stream index is irrelevant for WAV but left at default 3 to avoid side-effects.
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe project WAVs into session.json/Markdown")
    parser.add_argument("project_dir", nargs="?", default=None, help="Project folder containing session.json (default: dist/Timestamp_Audio/TEST2)")
    parser.add_argument("--model", default=None, help="Whisper model (default: transcribe.py default large-v3)")
    parser.add_argument("--compute", default=None, help="CTranslate2 compute_type (default: transcribe.py float16, fallback int8_float16)")
    parser.add_argument("--prompt", default=None, help="Extra prompt sentence (appended to style anchor); not a keyword list")
    parser.add_argument("--terms", default=None, help="Hotwords glossary, comma-separated proper nouns")
    parser.add_argument("--outdir", default=None, help="Directory for .vtt output (optional). .txt always stays next to WAV.")
    parser.add_argument("--no-vad", action="store_true", help="Disable Silero VAD pre-filtering")
    parser.add_argument("--force", action="store_true", help="Retranscribe even if transcript already exists")
    parser.add_argument("--dry-run", action="store_true", help="List what would be transcribed without running whisper")
    args = parser.parse_args()

    if not TRANSCRIBE_SCRIPT.exists():
        print(f"ERROR: transcribe script not found: {TRANSCRIBE_SCRIPT}", file=sys.stderr)
        return 1

    # Check ffmpeg availability early
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5, check=False)
    except FileNotFoundError:
        print("ERROR: ffmpeg not found on PATH. Install ffmpeg first.", file=sys.stderr)
        return 1

    project_dir = find_project_dir(args.project_dir)
    session_json = project_dir / "session.json"
    if not session_json.exists():
        print(f"ERROR: No session.json at {project_dir}", file=sys.stderr)
        print(f"       Tried: {session_json}", file=sys.stderr)
        return 1

    print(f"Project: {project_dir}")
    print(f"Session: {session_json}")
    print(f"Transcriber: {TRANSCRIBE_SCRIPT}")

    session = TimestampSession(str(project_dir))
    if not session.entries:
        print("No entries in session.json.")
        return 0
    if not session.project_name:
        print("WARNING: project_name is empty in session.json; markdown will not be written until set.", file=sys.stderr)

    # Select entries to transcribe
    candidates = []
    skipped_done = 0
    skipped_no_audio = 0
    skipped_recording = 0
    for entry in sorted(session.entries, key=lambda e: (e.recording_number or 0, e.recording_index or 0, e.id)):
        if entry.status == "recording":
            skipped_recording += 1
            continue
        if not entry.audio_file:
            skipped_no_audio += 1
            continue
        wav_path = project_dir / entry.audio_file
        if not wav_path.exists():
            print(f"WARNING: audio_file missing on disk, skipping entry {entry.id}: {wav_path}", file=sys.stderr)
            skipped_no_audio += 1
            continue
        if entry.transcript and not args.force:
            skipped_done += 1
            continue
        candidates.append(entry)

    total = len(session.entries)
    print(f"Entries: {total} total — {len(candidates)} to transcribe, {skipped_done} already transcribed (skip), "
          f"{skipped_no_audio} without audio, {skipped_recording} recording-locked")

    if not candidates:
        print("Nothing to transcribe. Use --force to re-transcribe existing entries.")
        # Still report current transcripts
        for e in session.entries:
            if e.transcript:
                preview = e.transcript.splitlines()[0][:TRANSCRIPT_MAX_PREVIEW]
                print(f"  id={e.id} R{e.recording_number:02d}-{e.recording_index:03d} : \"{preview}\"")
        return 0

    if args.dry_run:
        print("\nDry run — would transcribe:")
        for e in candidates:
            wav = project_dir / e.audio_file
            print(f"  id={e.id} [{e.recording_number:02d}-{e.recording_index:03d}] {wav}  elapsed={e.elapsed_seconds:.1f}s")
        return 0

    success = 0
    failed = 0
    for idx, entry in enumerate(candidates, 1):
        wav_path = (project_dir / entry.audio_file).resolve()
        txt_path = wav_path.with_suffix(".txt")
        print("\n" + "=" * 70)
        print(f"[{idx}/{len(candidates)}] Entry id={entry.id}  R{entry.recording_number:02d}-{entry.recording_index:03d}  "
              f"{entry.audio_file}  elapsed={entry.elapsed_seconds:.1f}s")
        print(f"  WAV: {wav_path}")
        print(f"  TXT: {txt_path}")
        print("=" * 70)

        cmd = build_transcribe_cmd(
            TRANSCRIBE_SCRIPT, wav_path,
            model=args.model, compute=args.compute,
            prompt=args.prompt, terms=args.terms,
            no_vad=args.no_vad, outdir=args.outdir,
        )
        print(f"  CMD: {' '.join(cmd)}")
        # Run sequentially — never parallel, GPU contention would OOM
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"  FAILED: transcribe.py exited {result.returncode} for {wav_path}", file=sys.stderr)
            failed += 1
            continue

        if not txt_path.exists():
            print(f"  WARNING: Expected .txt not found after transcription: {txt_path}", file=sys.stderr)
            failed += 1
            continue

        try:
            raw_text = txt_path.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"  ERROR reading .txt: {exc}", file=sys.stderr)
            failed += 1
            continue

        cleaned = clean_transcript(raw_text)
        if not cleaned:
            print(f"  WARNING: Transcript empty after cleaning (silence/VAD) — storing None", file=sys.stderr)
            # Store None explicitly (clear)
            try:
                session.update_transcript(entry.id, None)
            except Exception as exc:
                print(f"  ERROR updating session: {exc}", file=sys.stderr)
                failed += 1
                continue
            success += 1
            continue

        # Reload entry check — ensure not recording-locked mid-run
        current = session.get(entry.id)
        if current and current.status == "recording":
            print(f"  SKIP: Entry {entry.id} is recording-locked, not updating transcript", file=sys.stderr)
            failed += 1
            continue

        try:
            session.update_transcript(entry.id, cleaned)
            # Reload to verify persisted
            session_after = TimestampSession(str(project_dir))
            stored = session_after.get(entry.id)
            preview = cleaned.splitlines()[0][:TRANSCRIPT_MAX_PREVIEW] if cleaned else ""
            print(f"  DONE: Stored {len(cleaned)} chars for id={entry.id}: \"{preview}{'...' if len(cleaned) > TRANSCRIPT_MAX_PREVIEW else ''}\"")
            # Keep in-memory session in sync with disk
            session = session_after
            success += 1
        except Exception as exc:
            print(f"  ERROR updating transcript: {exc}", file=sys.stderr)
            failed += 1

    print("\n" + "=" * 70)
    print(f"Finished: {success} transcribed, {failed} failed, {skipped_done} skipped (already done)")
    markdown_path = project_dir / f"{session.project_name}.md" if session.project_name else project_dir / "TEST2.md"
    if markdown_path.exists():
        print(f"Markdown: {markdown_path}")
        # Show tail of markdown for verification
        try:
            lines = markdown_path.read_text(encoding="utf-8").splitlines()
            print(f"Markdown lines: {len(lines)}")
            # Print transcript blocks preview
            in_block = False
            for line in lines:
                if "Transcript:" in line or "```" in line:
                    print(line)
                elif in_block:
                    print(line)
        except Exception:
            pass
    else:
        print(f"Markdown not found: {markdown_path}", file=sys.stderr)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
