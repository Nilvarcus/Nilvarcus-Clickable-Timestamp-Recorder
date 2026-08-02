# Nilvarcus Timestamp App

A professional, streamlined Python application designed for content creators to efficiently mark and manage timestamps during video recordings. It features a crimson-on-black Material Design theme, global hotkeys with automatic admin elevation, direct OBS WebSocket integration, live HUD Overlays, configurable screenshot sizes with AI-powered Gemini analysis, and a zoomable text viewer.

## 🚀 Key Features

*   **Comprehensive OBS Integration:** Connects seamlessly to OBS Studio via WebSocket. Features bi-directional recording sync (triggering one starts the other), automatic Scene Transition logging into your timeline, and Replay Buffer capture hooks.
*   **Dynamic HUD Overlay:** A customizable, game-ready transparent overlay with a pulsing glowing border that dynamically shifts color based on backend state (Recording, Success, Error). Keeps your timer visible without cluttering the screen.
*   **Synced Screenshots:** Instantly snap your primary gaming monitor natively without lag. Images auto-save into per-session subfolders (`Screenshots/<session>/`) as optimized JPEGs and inject Obsidian-compatible `![[wikilinks]]` alongside your elapsed time.
*   **Screenshot Size Control:** Customize image dimensions directly from settings (choose between 720p, 1080p, or Original monitor resolution) to balance image clarity and disk space.
*   **🤖 Gemini AI Screenshot Analysis:** Connect a free Google Gemini API key and press one button to send all session screenshots to Gemini. AI descriptions are auto-inserted as italic notes beneath each screenshot — already-analyzed shots are skipped automatically.
*   **📏 Viewer Scale Control:** A zoom slider in Settings (60%–140%) lets you scale text and inline screenshot thumbnails in the main viewer — shrink to see more of your log, or zoom in for easier reading.
*   **🔐 Automatic Admin Elevation:** The app auto-detects administrator privileges and requests elevation via UAC at launch. Global hotkeys work inside full-screen games without manual "Run as Administrator" every time.
*   **Global Hotkeys:** Full hardware level support for `F13-F24` keys natively, bypassing UI focus. Maps perfectly onto a Stream Deck or Macro Pad. Mouse buttons (4, 5, middle) also bindable.
*   **Advanced Markdown Formatting:** Generates clean, bolded, highly readable Markdown files built meticulously for previewing inside Obsidian or GitHub.
*   **Configurable Environment:** Tabbed Settings window (General, OBS, AI, Keybinds). Set custom `Output Directories`, tweak screenshot resolutions, configure Gemini API keys, and adjust HUD opacities — all persisted across sessions.

## ⌨️ Default Keybinds

| Action | Key | Description |
| :--- | :--- | :--- |
| **Create/Open File** | `F13` | Initialize a new session file in your target Output Folder. |
| **Start Recording** | `F14` | Synchronize your stopwatch (and command OBS to start). |
| **Mark Time** | `F15` | Instantly drop a bolded timestamp mark into the timeline. |
| **Stop Recording** | `F16` | Finalize the log and stop OBS tracking. |
| **Save Short** | `F18` | Saves your OBS Replay Buffer and drops a `## SHORT` marker. Only works when OBS is connected and the replay buffer is actively running. |
| **Take Screenshot** | `F19` | Silently captures primary monitor and injects an Obsidian wikilink. |
| **Analyze Screenshots** | `Unbound` | Send all session screenshots to Gemini AI for auto-descriptions. |
| **Resolve Export** | `Unbound` | Generate DaVinci Resolve marker code from your timestamp log. |
| **Custom Notes** | `F20-F24`| Inject your 5 pre-configured custom text markers natively. |

## 🛠️ Installation & Setup

### Prerequisites
- **Python 3.x**

### Dependencies
```bash
pip install customtkinter pynput obsws-python Pillow google-genai
```

### OBS Setup
To allow the app to command your recordings and listen for Scene Changes, ensure OBS WebSocket is enabled natively:
`Tools → OBS WebSocket Settings → Enable WebSockets (Port 4455)`

### Running the App
```bash
python timestamp_gui.py
```

## 💡 Usage Tips

*   **Stream Deck Mapping:** Use your Elgato or macro software to map generic physical buttons to the `F13-F24` keys for a completely hands-free physical control deck while gaming.
*   **Gemini API Key:** Grab a free API key from [Google AI Studio](https://aistudio.google.com/apikey), paste it in Settings → AI, then bind the "Analyze Screenshots" hotkey for one-press AI descriptions during sessions.
*   **Admin Privileges:** The app auto-elevates at launch. If you want to suppress the UAC prompt, create a scheduled task or shortcut configured to "Run with highest privileges."

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---
*Developed by Nilvarcus. Designed for creators, by a creator.*
