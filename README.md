# Nilvarcus Timestamp App

[Image of App's Interface]

This is a simple application designed to assist with timestamping video recordings, particularly for content creators using software like OBS Studio. The primary function is to:

1. **Track Recording Time:** A stopwatch is initiated when you start your recording session.
2. **Mark Timestamps:** You can manually mark specific points in time during the recording.
3. **Generate Timestamp File:** These timestamps are saved to a TXT file, making it easy to reference later.

**Features and Keybinds:**

* **Create File (F13):** Creates a new TXT file or opens an existing one.
* **Start Recording (F14):** Starts the stopwatch and begins timestamping the recording file.
* **Mark Time (F15):** Adds a timestamp to the TXT file.
* **Stop Recording (F16):** Stops the stopwatch.
* **Save Short (F18):** Saves a timestamp for a short clip or replay buffer.
* **Save Changes (F17):** Manually saves changes to the TXT file (usually automatic).

**Usage Tips:**

- **Keybind Software:** You'll need software like AutoHotkey or a Stream Deck to assign these keybinds and integrate them with OBS Studio.
- **OBS Studio Settings:** Configure OBS to use a specific naming format for your recordings, such as `[%DD-%MM][%hh-%mm-%ss]`. This will ensure consistent naming with your timestamp file.
- **Hybrid MP4 Recording:** Consider using the "Hybrid MP4" recording format in OBS. This allows you to add chapter markers based on the timestamps in your TXT file. When imported into video editing software like DaVinci Resolve, these markers will align with the corresponding timestamps.

**Disclaimer:**

This application is a personal project and may not be fully polished or feature-rich. It's designed to meet my specific needs and may not be suitable for everyone. Feel free to experiment and customize it to your preferences.
