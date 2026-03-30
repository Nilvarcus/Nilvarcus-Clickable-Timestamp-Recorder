# Nilvarcus Timestamp App

![App Screenshot](app_screenshot-2.0.png)

A professional, streamlined Python application designed for content creators to efficiently mark and manage timestamps during video recordings. It features a modern dark UI, global hotkeys, direct OBS WebSocket integration, live HUD Overlays, and AI-powered voice transcription.

## 🚀 Key Features

*   **Comprehensive OBS Integration:** Connects seamlessly to OBS Studio via WebSocket. Features bi-directional recording sync (triggering one starts the other), automatic Scene Transition logging into your timeline, and Replay Buffer capture hooks.
*   **Dynamic HUD Overlay:** A customizable, game-ready transparent overlay featuring a live text feed of your latest tracked notes. The glowing border dynamically pulses based on backend state (Recording, Transcribing, Success, Error).
*   **AI Voice Transcription:** Integrated `OpenAI Whisper` support. Record 10-second background voice clips or use the **Push-to-Talk** hotkey to record endless memos. Transcriptions are typed natively into your log file.
*   **Synced Screenshots:** Instantly snap your primary gaming monitor natively without lag. Images auto-save to a dedicated `Screenshots/` folder and inject clean Markdown embed links right alongside your elapsed time.
*   **Global Hotkeys:** Full hardware level support for `F13-F24` keys natively, bypassing UI focus. Maps perfectly onto a Stream Deck or Macro Pad.
*   **Advanced Markdown Formatting:** Generates clean, bolded, highly readable `.txt` files built meticulously for Markdown previewing inside Obsidian or GitHub.
*   **Configurable Environment:** Manually set custom `Output Directories`, define precise Microphone hardware, and tweak HUD opacities via an intuitive Settings graphical tab.

## ⌨️ Default Keybinds

| Action | Key | Description |
| :--- | :--- | :--- |
| **Create/Open File** | `F13` | Initialize a new session file in your target Output Folder. |
| **Start Recording** | `F14` | Synchronize your stopwatch (and command OBS to start). |
| **Mark Time** | `F15` | Instantly drop a bolded timestamp mark into the timeline. |
| **Stop Recording** | `F16` | Finalize the log and stop OBS tracking. |
| **Voice Note (10s)** | `F17` | Record a rapid 10s audio memo for AI transcription. |
| **Voice Note (PTT)** | `Unbound`| Hold to record endless audio manually for up to 60s. |
| **Save Short** | `F18` | Drops a bold header and saves your active OBS Replay Buffer. |
| **Take Screenshot** | `F19` | Silently captures primary monitor and injects image markdown. |
| **Custom Notes** | `F20-F24`| Inject your 5 pre-configured custom text markers natively. |

## 🛠️ Installation & Setup

### Prerequisites
- **Python 3.x**
- **FFmpeg** (Required for OpenAI Whisper audio processing)

### Dependencies
```bash
pip install customtkinter pynput openai-whisper sounddevice numpy obsws-python Pillow
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

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---
*Developed by Nilvarcus. Designed for creators, by a creator.*
