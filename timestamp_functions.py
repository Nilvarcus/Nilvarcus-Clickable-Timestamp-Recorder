import time
import os
from datetime import datetime
from pynput import keyboard

class TimestampManager:
    def __init__(self, base_path=None):
        """Initialize the timestamp manager."""
        self.stopwatch_running = False
        self.start_time = None
        self.current_file_path = None
        self.counter = 0  # Initialize counter for timestamps
        self.base_path = base_path or os.getcwd()
        # Default output directory; can be overridden via set_output_dir()
        self.output_dir = os.path.join(self.base_path, "Timestamp_TXT")

        self.whisper_model = None
        self.is_transcribing = False
        self.gui_callback = None
        self.mic_device_index = None  # None = system default
        
        # Load whisper in background to avoid freezing the app
        import threading
        threading.Thread(target=self._load_whisper_model, daemon=True).start()

    def _load_whisper_model(self):
        try:
            import whisper
            self.whisper_model = whisper.load_model("base")
            print("Whisper model loaded.")
        except Exception as e:
            print(f"Error loading whisper: {e}")

    def register_gui_callback(self, callback):
        self.gui_callback = callback

    def set_output_dir(self, path: str):
        """Set a custom output directory for timestamp files."""
        self.output_dir = path

    def set_mic_device(self, device_index):
        """Set the microphone device index for voice recordings. None = system default."""
        self.mic_device_index = device_index

    def create_file(self, initial_dir=None):
        """
        Create a new file with a timestamped name.
        
        Args:
            initial_dir (str, optional): Directory to start file dialog. 
                                         Defaults to the configured output_dir.
        
        Returns:
            str: Path of the created file, or None if file creation was cancelled.
        """
        import tkinter as tk
        from tkinter import filedialog

        # Use the configured output directory
        target_dir = self.output_dir
        
        # Create the folder if it doesn't exist
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)

        # Generate default filename with current timestamp
        default_name = datetime.now().strftime("[%d-%m-%Y][%H-%M-%S] - WRITE HERE.md")
        
        # Open file dialog
        file_name = filedialog.asksaveasfilename(
            title="Save File",
            filetypes=[("Markdown Files", "*.md"), ("Text Files", "*.txt")],
            defaultextension=".md",
            initialdir=target_dir,
            initialfile=default_name,
        )

        # If a file was selected, create it and return the path
        if file_name:
            self.current_file_path = file_name
            with open(self.current_file_path, "a", encoding="utf-8") as file:
                file.write("")  # Ensure the file exists
            return self.current_file_path
        return None

    def start_recording(self):
        """
        Start recording by adding a timestamp to the file.
        
        Returns:
            bool: True if recording started successfully, False otherwise.
        """
        if self.current_file_path and not self.stopwatch_running:
            with open(self.current_file_path, "a", encoding="utf-8") as file:
                timestamp = datetime.now().strftime("[%d-%m][%H-%M-%S]")
                self.counter = 0  # Reset counter on start
                file.write(f"\n## 0 - Filename: {timestamp}\n\n* **Starting Notes** - \n")
            self.start_time = time.time()
            self.stopwatch_running = True
            return True
        return False

    def mark_time(self):
        """
        Mark the current stopwatch time in the file.
        
        Returns:
            str: Formatted time if marked successfully, None otherwise.
        """
        if self.current_file_path and self.stopwatch_running:
            elapsed_time = time.time() - self.start_time
            formatted_time = time.strftime("[%H:%M:%S]", time.gmtime(elapsed_time))
            self.counter += 1  # Increment counter on each timestamp
            with open(self.current_file_path, "a", encoding="utf-8") as file:
                file.write(f"\n*  **[{self.counter}]**   **{formatted_time}** - ")
            return formatted_time
        return None

    def get_elapsed_time(self):
        """
        Get the current elapsed time as a formatted string.
        
        Returns:
            str: Formatted time 'HH:MM:SS' if recording, None otherwise.
        """
        if self.stopwatch_running and self.start_time:
            elapsed_time = time.time() - self.start_time
            return time.strftime("[%H:%M:%S]", time.gmtime(elapsed_time))
        return None

    def mark_custom_note(self, note_text: str):
        """
        Mark the current stopwatch time with a custom text note.
        
        Args:
            note_text (str): The custom text to append after the timestamp.
            
        Returns:
            str: Formatted time if marked successfully, None otherwise.
        """
        if self.current_file_path and self.stopwatch_running:
            elapsed_time = time.time() - self.start_time
            formatted_time = time.strftime("[%H:%M:%S]", time.gmtime(elapsed_time))
            self.counter += 1  # Increment counter on each timestamp
            with open(self.current_file_path, "a", encoding="utf-8") as file:
                file.write(f"\n*  **[{self.counter}]**   **{formatted_time}** - {note_text}")
            return formatted_time
        return None

    def stop_recording(self):
        """
        Stop and reset the stopwatch.
        
        Returns:
            bool: True if recording stopped successfully, False otherwise.
        """
        if self.current_file_path and self.stopwatch_running:
            elapsed_time = self.get_elapsed_time()
            with open(self.current_file_path, "a", encoding="utf-8") as file:
                file.write(f"\n\n* **Ending Notes** - ")
                file.write(f"\nTotal Recording Time: {elapsed_time}\n")
                file.write("\n---\n")
            self.stopwatch_running = False
            self.start_time = None
            self.counter = 0  # Reset counter on stop
            return True
        return False

    def save_short(self, error=False):
        """
        Take a short and add it to the current file.
        
        Args:
            error (bool): If True, writes an error marker instead of standard short.
            
        Returns:
            bool: True if short was saved successfully, False otherwise.
        """
        if self.current_file_path:
            with open(self.current_file_path, "a", encoding="utf-8") as file:
                timestamp = datetime.now().strftime("[%d-%m][%H-%M-%S]")
                if error:
                    file.write(f"\n\n## ERROR - NO REPLAY BUFFER RUNNING \n")
                else:
                    file.write(f"\n\n## SHORT - {timestamp} - \n")
            return True
        return False

    def save_changes(self, text_content):
        """
        Save changes to the current file.
        
        Args:
            text_content (str): Content to be saved to the file.
        
        Returns:
            bool: True if changes were saved successfully, False otherwise.
        """
        if self.current_file_path:
            with open(self.current_file_path, "w", encoding="utf-8") as file:
                # Remove only the very last newline that Tkinter adds automatically, 
                # but keep all other trailing whitespace/spaces.
                if text_content.endswith('\n'):
                    text_content = text_content[:-1]
                file.write(text_content)
            return True
        return False

    def read_file_content(self):
        """
        Read the content of the current file.
        
        Returns:
            str: File contents if file exists, empty string otherwise.
        """
        if self.current_file_path:
            try:
                with open(self.current_file_path, "r", encoding="utf-8") as file:
                    return file.read()
            except FileNotFoundError:
                return ""
        return ""

    def take_screenshot(self) -> bool:
        """
        Captures the screen and links it as a markdown image in the timestamp log.
        Returns True if successful.
        """
        if not self.stopwatch_running or not self.current_file_path:
            return False

        try:
            import os
            from PIL import ImageGrab
            from datetime import datetime
            
            screenshots_dir = os.path.join(self.output_dir, "Screenshots")
            os.makedirs(screenshots_dir, exist_ok=True)
            
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"shot_{timestamp_str}.png"
            filepath = os.path.join(screenshots_dir, filename)
            
            # Use default capture (Primary Monitor Only)
            img = ImageGrab.grab(all_screens=False)
                
            img.save(filepath, "PNG")
            
            self.counter += 1
            elapsed = self.get_elapsed_time()
            line = f"\n*  **[{self.counter}]**   **{elapsed}** - 📸 Screenshot → ![Screenshot](Screenshots/{filename})"
            
            with open(self.current_file_path, 'a', encoding='utf-8') as f:
                f.write(line)
                
            return True
        except Exception as e:
            print(f"Error taking screenshot: {e}")
            return False

    def mark_voice_note(self):
        """
        Record a 10s voice note, transcribe it using Whisper, and mark in the file asynchronously.
        
        Returns:
            bool: True if recording started, False otherwise.
        """
        if self.current_file_path and self.stopwatch_running:
            if getattr(self, 'is_transcribing', False):
                return False
            self.is_transcribing = True
            import threading
            threading.Thread(target=self._record_and_transcribe, daemon=True).start()
            return True
        return False

    def _record_and_transcribe(self):
        import sounddevice as sd
        import numpy as np
        
        if not getattr(self, 'whisper_model', None):
            if self.gui_callback:
                self.gui_callback("Model Loading...")
            import time
            wait_time = 0
            while not getattr(self, 'whisper_model', None) and wait_time < 30:
                time.sleep(1)
                wait_time += 1
            if not getattr(self, 'whisper_model', None):
                if self.gui_callback:
                    self.gui_callback("Model Error")
                self.is_transcribing = False
                return
            
        duration = 10  # seconds
        fs = 16000
        
        try:
            if self.gui_callback:
                self.gui_callback("Recording (10s)...")
                
            recording = sd.rec(
                int(duration * fs), samplerate=fs, channels=1, dtype='float32',
                device=self.mic_device_index
            )
            sd.wait()
            
            if self.gui_callback:
                self.gui_callback("Transcribing...")
                
            audio_data = recording.flatten()
            result = self.whisper_model.transcribe(audio_data, fp16=False)
            transcription = result['text'].strip()
            
            if self.gui_callback:
                # Use a specific format to pass the result back to the GUI
                self.gui_callback(f"COMPLETE|{transcription}")
                
        except Exception as e:
            print(f"Transcription error: {e}")
            if self.gui_callback:
                self.gui_callback("Error")
            import time
            time.sleep(2)
        finally:
            self.is_transcribing = False

    def start_ptt_voice_note(self):
        """Start a push-to-talk voice recording."""
        if not self.current_file_path or not self.stopwatch_running:
            return False
        if getattr(self, 'is_transcribing', False) or getattr(self, 'is_ptt_recording', False):
            return False
            
        self.is_ptt_recording = True
        self.ptt_audio_data = []
        
        import threading
        threading.Thread(target=self._ptt_record_thread, daemon=True).start()
        return True
        
    def stop_ptt_voice_note(self):
        """Stop PTT recording and trigger transcription."""
        if getattr(self, 'is_ptt_recording', False):
            self.is_ptt_recording = False
            return True
        return False
        
    def _ptt_record_thread(self):
        import time
        import sounddevice as sd
        
        # Ensure model is loaded first
        if not getattr(self, 'whisper_model', None):
            if self.gui_callback:
                self.gui_callback("Model Loading...")
            wait_time = 0
            while not getattr(self, 'whisper_model', None) and wait_time < 30:
                time.sleep(1)
                wait_time += 1
            if not getattr(self, 'whisper_model', None):
                if self.gui_callback:
                    self.gui_callback("Model Error")
                self.is_ptt_recording = False
                return

        fs = 16000
        
        def callback(indata, frames, time_info, status):
            if status:
                print(status)
            if self.is_ptt_recording:
                self.ptt_audio_data.append(indata.copy())

        try:
            max_seconds = 180
            if self.gui_callback:
                self.gui_callback(f"PTT Recording ({max_seconds}s)...")
                
            stream = sd.InputStream(
                samplerate=fs, channels=1, dtype='float32',
                device=self.mic_device_index, callback=callback
            )
            
            start_time = time.time()
            with stream:
                while self.is_ptt_recording:
                    # Hard limit
                    if time.time() - start_time > max_seconds:
                        self.is_ptt_recording = False
                        if self.gui_callback:
                            self.gui_callback("Max Time Reached!")
                        break
                    time.sleep(0.1)
            
            # Now stream is closed. Process audio.
            self._process_ptt_audio()
            
        except Exception as e:
            print(f"PTT Record error: {e}")
            if self.gui_callback:
                self.gui_callback("Error")
            self.is_ptt_recording = False

    def _process_ptt_audio(self):
        import numpy as np
        
        if not self.ptt_audio_data:
            self.is_ptt_recording = False
            if self.gui_callback:
                self.gui_callback("No Audio")
            return
            
        self.is_transcribing = True
        if self.gui_callback:
            self.gui_callback("Transcribing...")

        try:
            # Concatenate chunks and flatten into 1D array
            full_audio = np.concatenate(self.ptt_audio_data, axis=0)
            audio_data = full_audio.flatten()
            
            result = self.whisper_model.transcribe(audio_data, fp16=False)
            transcription = result['text'].strip()
            
            if transcription:
                if self.gui_callback:
                    self.gui_callback(f"COMPLETE|{transcription}")
            else:
                if self.gui_callback:
                    self.gui_callback("No speech detected")
                    
        except Exception as e:
            print(f"Transcription error: {e}")
            if self.gui_callback:
                self.gui_callback("Error")
        finally:
            self.is_transcribing = False
            self.ptt_audio_data = []

    def get_recent_log_events(self, count=3):
        """Parse the active file to retrieve the last few marked lines/notes."""
        content = self.read_file_content()
        if not content:
            return []
            
        events = []
        for line in content.splitlines():
            line = line.strip()
            if not line: continue
            if line.startswith("# ") and "SHORT" not in line and "ERROR" not in line: continue
            if line == "---" or "Total Recording Time:" in line: continue
            if "Starting Notes" in line or "Ending Notes" in line: continue
            
            # Clean up some markdown artifacts for cleaner HUD display
            display_line = line.replace("**", "").replace("📺  Scene →", "📺")
            events.append(display_line.strip())
            
        return events[-count:]
