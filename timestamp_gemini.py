"""
timestamp_gemini.py — Google Gemini AI Integration for the Nilvarcus Timestamp App.

Handles:
  - API key management (stored in keybinds.json, set via Settings → AI tab)
  - Screenshot analysis using Gemini via google-genai SDK
  - Batch analysis of all screenshots in the current log file
  - Threaded execution with GUI callbacks for progress updates

Requires: pip install google-genai
Get your API key: https://aistudio.google.com/apikey
"""

import threading
import os
import sys

# ── Module-level google-genai import ──────────────────────────────────────
# Import on the main thread (at module load time) to avoid PEP 420 namespace
# package resolution failures when accessed later from background threads.
# Using the standard 'from google import genai' syntax which properly handles
# namespace package path resolution, unlike manual importlib.find_spec().

_genai = None
_genai_types = None
_GENAI_AVAILABLE = False
_GENAI_IMPORT_ERROR = ""

try:
    from google import genai as _genai
    from google.genai import types as _genai_types
    _GENAI_AVAILABLE = True
except ImportError as e:
    _GENAI_IMPORT_ERROR = f"{type(e).__name__}: {e}"
    # Collect diagnostics to help the user identify the right Python environment
    _python_exe = sys.executable
    _python_ver = sys.version.split()[0]
    _pip_cmd = f'"{_python_exe}" -m pip install google-genai'
    print(f"[Gemini] Import failed: {_GENAI_IMPORT_ERROR}", file=sys.stderr)
    print(f"[Gemini] Python:  {_python_ver}  ({_python_exe})", file=sys.stderr)
    print(f"[Gemini] Fix:     {_pip_cmd}", file=sys.stderr)


class GeminiAnalyzer:
    """Self-contained Gemini AI analyzer for screenshot descriptions.

    All heavy logic runs in background threads. GUI updates are delegated
    via registered callbacks, keeping this module free of tkinter dependencies.
    """

    MODEL_ID = "gemini-3.5-flash-lite"

    def __init__(self):
        self._api_key = ""
        self._client = None
        self._is_analyzing = False

        # GUI callbacks — set via register_callbacks()
        self._on_progress = None       # (current: int, total: int, message: str) → None
        self._on_complete = None       # (results: list) → None
        self._on_error = None          # (error_msg: str) → None

    # ── Public API ──────────────────────────────────────────────────────────

    def register_callbacks(self, on_progress=None, on_complete=None, on_error=None):
        """Register GUI callbacks. All are optional."""
        self._on_progress = on_progress
        self._on_complete = on_complete
        self._on_error = on_error

    @property
    def api_key(self):
        return self._api_key

    def set_api_key(self, key: str):
        """Set the API key and reset the client (will lazy-init on next use)."""
        self._api_key = key or ""
        self._client = None

    @property
    def is_configured(self):
        """True if an API key has been set."""
        return bool(self._api_key)

    @property
    def is_analyzing(self):
        return self._is_analyzing

    def _get_client(self):
        """Lazy-init the genai Client. Returns None if no key set or SDK missing."""
        if not self._api_key:
            return None
        if not _GENAI_AVAILABLE:
            return None
        if self._client is None:
            self._client = _genai.Client(api_key=self._api_key)
        return self._client

    def test_connection(self) -> tuple:
        """
        Test the API key with a simple text request.
        Returns (True, 'Gemini 3.5 Flash-Lite') on success or (False, 'error message').
        """
        if not self._api_key:
            return False, "No API key set"
        if not _GENAI_AVAILABLE:
            return False, "google-genai package not installed. Run: pip install google-genai"
        try:
            client = self._get_client()
            resp = client.models.generate_content(
                model=self.MODEL_ID,
                contents="Respond with exactly: OK"
            )
            if resp.text:
                return True, "Gemini 3.5 Flash-Lite"
            return False, "Empty response from API"
        except Exception as e:
            return False, str(e)[:300]

    def analyze_screenshot(self, image_path: str) -> str:
        """
        Analyze a single screenshot synchronously.
        Returns the description text, or empty string on error.
        """
        client = self._get_client()
        if not client:
            return ""

        try:
            # Load image as raw bytes for inline upload (faster than file upload API)
            with open(image_path, "rb") as f:
                image_data = f.read()

            image_part = _genai_types.Part.from_bytes(
                data=image_data,
                mime_type="image/jpeg"
            )

            prompt = (
                "You are analyzing a screenshot from a content creator's recording session. "
                "In 1-2 concise sentences, describe what is happening on screen — "
                "the game, app, or content visible, any key UI elements, and the action taking place. "
                "Keep it factual and brief. Do not use markdown formatting."
            )

            resp = client.models.generate_content(
                model=self.MODEL_ID,
                contents=[prompt, image_part]
            )
            return resp.text.strip() if resp.text else ""
        except Exception as e:
            print(f"[Gemini] Screenshot analysis error: {e}")
            return ""

    def analyze_all_screenshots(self, screenshot_entries: list):
        """
        Analyze multiple screenshots in a background thread.

        Args:
            screenshot_entries: List of dicts with keys:
                'counter' (int), 'filepath' (str), 'filename' (str)

        Calls on_progress(current, total, message) during analysis,
        then on_complete(results) or on_error(msg) when done.
        """
        if self._is_analyzing:
            return
        if not self._api_key:
            self._fire_error("No Gemini API key configured. Add one in Settings → AI.")
            return
        if not _GENAI_AVAILABLE:
            self._fire_error("google-genai package not installed. Run: pip install google-genai")
            return
        if not screenshot_entries:
            self._fire_error("No screenshots found in the current log.")
            return

        self._is_analyzing = True
        threading.Thread(
            target=self._analyze_thread,
            args=(screenshot_entries,),
            daemon=True,
        ).start()

    # ── Internal: threaded analysis ──────────────────────────────────────────

    def _analyze_thread(self, entries: list):
        total = len(entries)
        results = []

        for i, entry in enumerate(entries):
            filepath = entry.get("filepath", "")
            counter = entry.get("counter", 0)

            if not os.path.exists(filepath):
                print(f"[Gemini] Screenshot not found: {filepath}")
                results.append({**entry, "description": ""})
                self._fire_progress(i + 1, total, f"Skipped [{counter}] (file missing)")
                continue

            self._fire_progress(i + 1, total, f"Analyzing [{counter}]...")

            description = self.analyze_screenshot(filepath)
            results.append({**entry, "description": description})

            if description:
                self._fire_progress(i + 1, total, f"Done [{counter}]")
            else:
                self._fire_progress(i + 1, total, f"Failed [{counter}]")

        self._is_analyzing = False
        self._fire_complete(results)

    # ── Internal: callback helpers ───────────────────────────────────────────

    def _fire_progress(self, current, total, message):
        if self._on_progress:
            try:
                self._on_progress(current, total, message)
            except Exception as e:
                print(f"[Gemini] Progress callback error: {e}")

    def _fire_complete(self, results):
        if self._on_complete:
            try:
                self._on_complete(results)
            except Exception as e:
                print(f"[Gemini] Complete callback error: {e}")

    def _fire_error(self, msg):
        self._is_analyzing = False
        if self._on_error:
            try:
                self._on_error(msg)
            except Exception as e:
                print(f"[Gemini] Error callback error: {e}")
