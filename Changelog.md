# Nilvarcus Timestamp App - Changelog

All notable changes to the Nilvarcus Timestamp App will be documented in this file.

---

## [2.6.0] - 2026-07-22

### 🧹 Whisper Removal (Lightweight Edition)
* **Removed Voice Transcription:** Completely stripped out OpenAI Whisper, PyTorch, and associated heavy ML dependencies. No more massive download size, model loading overhead, or system stutters.
* **Simplified Dependencies:** Removed heavy external libraries like `sounddevice` and `numpy`. The application now loads and starts up instantly, with a dramatically smaller disk footprint.
* **UI Clean-up:** Removed Whisper model settings, microphone device selection, transcription language dropdowns, and CUDA hardware acceleration readouts.

### 🤖 Gemini AI Screenshot Analysis
* **Google Gemini Integration:** Replaced local Whisper with cloud-powered Gemini AI for screenshot analysis. Just add a free API key from Google AI Studio in Settings → AI.
* **Auto-Insert AI Descriptions:** Press the "AI: Analyze Screenshots" button (or bind a hotkey) to send all screenshots in the current log to Gemini. Descriptions are auto-inserted as italic amber notes beneath each screenshot.
* **Smart Skip (Already Analyzed):** Only un-analyzed screenshots are sent — already-described screenshots are skipped automatically. If every screenshot already has a description, the app tells you there's nothing new to do.
* **Model:** Uses `gemini-3.5-flash-lite` for fast, cost-effective analysis.

### 📸 Screenshot Size Control
* **Custom Resolution Settings:** Added a new "Screenshot Size" option inside the General Settings tab. Users can now choose to save screenshots in:
  * **720p:** Resizes captures to 1280x720 (optimized for lightweight logs and low disk usage).
  * **1080p:** Resizes captures to 1920x1080 (excellent balance of clarity and storage).
  * **Original:** Saves the screenshot at the monitor's native resolution.
* **Per-Session Subfolders:** Screenshots are now organised into dedicated subfolders per recording session (`Screenshots/<session_name>/`) instead of a single flat folder, keeping multi-session logs clean and navigable.
* **Storage Space Savings:** Resizing screenshots helps manage disk space when taking dozens of screen captures during long gaming or recording sessions.

### 📏 Viewer Scale Control
* **Zoom Slider:** New "Viewer Scale" slider in General Settings (60% – 140%) that adjusts the size of text and screenshot thumbnails in the main viewer.
* **Fit More on Screen:** Scale down to see more of your log at once, or scale up for easier reading on high-resolution displays.
* **Live Preview:** The percentage label updates in real-time as you drag the slider.

### 🔐 Administrator Auto-Elevation
* **No More Admin Setup:** The app now detects whether it has administrator privileges at launch and automatically requests elevation via a UAC prompt. If declined, the app exits — it refuses to run without admin rights.
* **Why It Matters:** Global hotkeys require administrator privileges to function inside full-screen games and applications (Windows UIPI). Previously, users had to manually right-click and "Run as Administrator" every time.

### 🔧 Behavioural & Workflow Tweaks
* **Replay Buffer Gating for Save Short:** "Save Short" no longer requires an active recording — it now checks whether the OBS replay buffer is actually running. If OBS isn't connected or the replay buffer is off, the button shows a red HUD status and writes nothing to the file. Only a live replay buffer triggers the save and writes a `## SHORT` marker.
* **Take Screenshot Still Requires Recording:** "Take Screenshot" continues to require a recording in progress (stopwatch running) to prevent orphaned captures without timing context.
* **Simplified HUD:** Removed the live scrolling event feed from the Recording HUD to keep it lean and minimally distracting during gameplay. The HUD now shows only the elapsed timer and transient status messages.

### 🔌 OBS Replay Buffer State Tracking
* **Live Replay Buffer Awareness:** The app now tracks whether the OBS replay buffer output is active via the `ReplayBufferStateChanged` WebSocket event. State is queried on connect and kept in sync in real time.
* **Save Short Only When Buffer Is Hot:** "Save Short" gates on replay buffer being active — no more failed saves or confusing error markers when the buffer isn't running.

### 🎨 UI/UX Polish
* **Crimson & Black Theme:** Full visual overhaul with a Material Design 3–inspired crimson-on-black palette. Deeper surfaces, refined typography, and improved button styling.
* **Inline Screenshot Thumbnails:** The text viewer now renders screenshot thumbnails directly inline, giving you a visual timeline alongside your notes.
* **Screenshot Format:** Screenshots are now saved as optimized JPEG with Obsidian-compatible `![[filename]]` wikilinks.
* **Tabbed Settings Window:** Settings are now organised into tidy tabs (General, OBS, AI, Keybinds) instead of one long scrollable list.

---

## [2.5.0] - 2026-04-16

### 🎨 The HUD Refinement Update

#### 🖥️ Brand New Modern HUD
* **Frameless Design:** The Recording HUD has been completely reimagined! Stripped away the bulky Windows title bar and borders for a sleek, "floating" aesthetic.
* **Horizontal Layout:** Optimized for minimal screen space usage. The HUD now rests as a slim horizontal bar, perfect for placing at the top or bottom of your monitor.
* **Interactive Dragging:** Click and drag anywhere on the HUD bar to move it to your preferred location on screen.

#### 🌟 Visual Feedback & Animations
* **Dynamic Glowing Border:** Added a breathing animation to the HUD border. The color shifts dynamically to represent the active state:
  * 🔴 **Pulsing Red:** Standard recording is active.
  * 🟠 **Glowing Orange:** Transcription is in progress.
  * 🟢 **Green Flash:** Action successful (Note saved or Screenshot taken).
* **High-Visibility Recording State:** When recording a Voice Note or using PTT, the timer is replaced by a large red countdown to show exactly how much time remains.

#### 🧪 Settings & Customization
* **Secondary Voice Notes:** Added new "Secondary Voice Note" and "Secondary PTT Voice Note" actions. These allow recording a voice note that inserts directly into the log without generating a new timestamp.
* **Cleaner Voice Note Output:** Removed the redundant `**Voice Note:**` prefix from transcribed audio so that text flows naturally.
* **Voice Note Length Control:** Customize the duration of Voice Note recordings under Whisper Settings.
* **Opacity Control:** Adjust HUD transparency in General Settings from 20% (ghostly transparent) to 100% (fully opaque).
* **Mouse 4 & Mouse 5 Support:** Keybinder now natively detects auxiliary mouse buttons (Mouse 4, Mouse 5, and Middle Click).
* **Re-Open Shortcut:** Added a "Re-Open HUD Overlay" button in settings to easily retrieve a closed HUD.

#### 🎬 Video Editor Integration
* **DaVinci Resolve Export:** Introducing a direct way to bring your markers into DaVinci Resolve. Generates Python code that automatically creates markers on your timeline.
  * **Customizable FPS & Duration:** Adjust target FPS and clip duration directly in the export dialog.
  * **One-Click Workflow:** Code is automatically copied to the clipboard for pasting into the DaVinci Resolve console.

#### 🔧 Stability & Polish
* **Window Management Improvements:** Refined the "Always-on-Top" logic to ensure the HUD stays visible over full-screen applications.
* **Memory Optimization:** Optimized the border animation engine to use minimal CPU resources and avoid impact on game FPS.

---

## [2.4.0] - 2026-04-05

### 🚀 The AI Power Update

#### ⚡ Hardware Acceleration (CUDA Support)
* **NVIDIA GPU Integration:** Added automatic detection and utilization of NVIDIA GPUs via CUDA. AI transcriptions are up to 5-10x faster than CPU-only processing.
* **Automatic FP16 Optimization:** Runs on CUDA with float16 precision natively, doubling performance while reducing VRAM usage.
* **Intelligent Fallback:** Gracefully falls back to CPU processing if compatible GPU or CUDA drivers are missing.

#### 🎡 Whisper Model Selector ("Model Wheel")
* **Dynamic Model Swapping:** Switch between all 5 official Whisper models (`tiny`, `base`, `small`, `medium`, `turbo`) directly from settings.
* **On-the-Fly Loading:** Changing models triggers a background reload, allowing scaling of accuracy vs speed without restarting the app.
* **Language Selection:** Specify an ISO language code (e.g., `fi`, `en`, `ja`) for more accurate specialized recognition.

#### 📊 Vitality & Feedback
* **Acceleration Status Indicator:** Settings menu features a live Hardware Acceleration Status readout to show if CUDA is active or running in CPU-Only mode.
* **Model Health Tracking:** New status labels inform you if a model is currently loading or if there was an error.

#### 🔧 Engine Improvements
* **Threaded Model Management:** Moved the transcription engine to a robust background threading model to prevent UI micro-stutters.
* **Audio Buffer Safety:** Refined memory management for long PTT recordings to prevent leaks during high-frequency sessions.

---

## [2.3.0] - 2026-03-25

### 🚀 New Features & Improvements

#### 🎛️ HUD Redesign & Optimizations
* **Compact UI:** Downsized the Recording HUD to a more compact interface. Removed the live log feed of recent events to reduce screen clutter.
* **Dynamic Countdown Integration:** HUD overlay detects and displays active countdown timers for both Voice Notes and Push-to-Talk (PTT) recordings.
* **Graceful Timer Cancellation:** Stopping a PTT recording manually before the timer expires cancels the countdown and transitions smoothly to "Transcribing...".

#### 🎙️ Extended Push-to-Talk Limits
* **Tripled Recording Time:** Increased the maximum PTT recording limit from 60 seconds to a full 3 minutes (180 seconds).

#### ⌨️ Keybind Management
* **Complete Unbinding Flexibility:** Supports clearing and unbinding specific actions in the Settings menu to free up keyboard layouts.

---

## [2.2.0] - 2026-03-10

### 🚀 New Features

#### 🎮 Comprehensive OBS Studio Integration
* **WebSocket Connection:** Connect directly to OBS via WebSocket (Port 4455).
* **Bi-Directional Recording Sync:** App stopwatch and OBS recording start/stop in perfect sync.
* **Auto-Scene Logging:** Scene transitions inside OBS are automatically logged into the timestamp text file.
* **Replay Buffer Hook:** "Save Short" triggers the OBS Replay Buffer instantly, with built-in failsafe alerts.

#### 🎙️ Push-to-Talk (PTT) Voice Notes
* **Hold to Record:** Dedicated hotkey to manually capture voice notes for exactly as long as the button is held (up to 60 seconds).
* **UI Indication:** Main header flashes `🎙️ RECORDING...` to confirm raw audio capture.

#### 🎛️ Dynamic HUD Overlay & Visual Edge Glow
* **Live Log Feed:** Floating mini-widget upgraded into a transparent HUD, displaying the last 3 marked timestamps.
* **Pulsing Animations:** HUD border dynamically pulses colors based on the app state (Recording, Transcribing, Success, Error).
* **HUD Customization:** Added an Opacity Slider and a master Enable Toggle inside settings.

#### 📸 Synced Screenshots
* **Native Screen Capture:** Press hotkey (default `F19`) to capture primary monitor without lag.
* **Auto-Folder Generation:** Pushes all images into an auto-generated `Screenshots/` directory.
* **Markdown Auto-Embedding:** Injects Markdown image syntax links directly onto a new line in the text log with the current elapsed time.

#### 📂 Configurable Output Directory
* **Custom Save Locations:** Choose exactly where timestamp files are saved with persistence across sessions.
