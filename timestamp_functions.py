import time
import os
from datetime import datetime

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
        self.screenshot_resolution = "720p"

    def set_screenshot_resolution(self, res: str):
        """Set the screenshot resolution ('720p', '1080p', or 'Original')."""
        self.screenshot_resolution = res

    def set_output_dir(self, path: str):
        """Set a custom output directory for timestamp files."""
        self.output_dir = path

    def create_file(self):
        """
        Create a new file with a timestamped name.
        
        Returns:
            str: Path of the created file, or None if file creation was cancelled.
        """
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
                file.write(f"\n## 0 - Filename: {timestamp}\n")
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
                file.write(f"\n*  **[{self.counter}]**  **{formatted_time}** - ")
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
                file.write(f"\n*  **[{self.counter}]**  **{formatted_time}** - {note_text}")
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
                file.write(f"\n\nTotal Recording Time: {elapsed_time}\n")
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
            import re as _re
            # Collapse 3+ consecutive blank lines into 2 (prevents blank-line spam)
            text_content = _re.sub(r'\n{3,}', '\n\n', text_content)
            # Strip empty Starting/Ending Notes placeholders
            text_content = _re.sub(r'\* \*\*Starting Notes\*\*\s*-\s*\n?', '', text_content)
            text_content = _re.sub(r'\* \*\*Ending Notes\*\*\s*-\s*\n?', '', text_content)
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

    def get_screenshot_entries(self):
        """
        Parse the current file for all screenshot wikilinks and return their info.
        Screenshots that already have an AI description (> 🤖) are skipped.
        
        Returns:
            list: List of dicts with 'counter', 'filename', 'filepath' keys.
                  Only entries where the image file exists on disk AND no AI
                  description has been written yet are returned.
        """
        if not self.current_file_path:
            return []

        import re
        content = self.read_file_content()
        if not content:
            return []

        file_basename = os.path.basename(self.current_file_path)
        folder_name = os.path.splitext(file_basename)[0]
        screenshots_dir = os.path.join(self.output_dir, "Screenshots", folder_name)

        entries = []
        # Match: *  **[N]**   **[HH:MM:SS]** - ![[filename]]
        pattern = r'\*\s*\*\*\[(\d+)\]\*\*\s*\*\*\[.*?\]\*\*\s*-\s*!\[\[(.+?)\]\]'
        for match in re.finditer(pattern, content):
            counter = int(match.group(1))
            filename = match.group(2)
            filepath = os.path.join(screenshots_dir, filename)
            if not os.path.exists(filepath):
                continue

            # Check if this screenshot already has an AI description.
            # add_ai_description() inserts '  > 🤖 ...' right after the screenshot line.
            after_match = content[match.end():]
            # Skip if any AI description already exists (handles blank lines + old/new format)
            if re.match(r'(?:\s*\n)*[^\S\n]*[-–—>][^\S\n]*🤖', after_match):
                continue  # Already analyzed — skip

            entries.append({
                'counter': counter,
                'filename': filename,
                'filepath': filepath,
            })
        return entries

    def add_ai_description(self, counter: int, filename: str, description: str):
        """
        Insert an AI-generated description after a screenshot's timestamp line.
        
        Args:
            counter: The screenshot counter number to match.
            filename: The screenshot's unique filename (e.g. shot_20260722_133954.jpg).
                      Used alongside counter to uniquely identify the line when
                      multiple recording sessions share counter numbers.
            description: The AI description text to insert.
        
        Returns:
            bool: True if the description was inserted, False otherwise.
        """
        if not self.current_file_path or not description:
            return False

        import re
        content = self.read_file_content()
        if not content:
            return False

        # Find the screenshot line by counter AND unique filename.
        # Counter alone is ambiguous when multiple recordings exist in one file.
        escaped_fn = re.escape(filename)
        pattern = r'(\*\s*\*\*\[' + str(counter) + r'\]\*\*\s*\*\*\[.*?\]\*\*\s*-\s*!\[\[' + escaped_fn + r'\]\])'
        match = re.search(pattern, content)
        if not match:
            return False

        # Insert the AI description on a new line right after the screenshot line
        screenshot_line = match.group(1)
        replacement = f'{screenshot_line}\n    - 🤖 {description}'

        new_content = content[:match.start()] + replacement + content[match.end():]

        with open(self.current_file_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return True

    def take_screenshot(self):
        """
        Captures the screen and links it as a markdown image in the timestamp log.
        Returns True if successful.
        """
        if not self.stopwatch_running or not self.current_file_path:
            return False

        try:
            from PIL import Image, ImageGrab
            
            file_basename = os.path.basename(self.current_file_path)
            folder_name = os.path.splitext(file_basename)[0]
            screenshots_dir = os.path.join(self.output_dir, "Screenshots", folder_name)
            os.makedirs(screenshots_dir, exist_ok=True)
            
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"shot_{timestamp_str}.jpg"
            filepath = os.path.join(screenshots_dir, filename)
            
            # Use default capture (Primary Monitor Only)
            img = ImageGrab.grab(all_screens=False)
            
            res = self.screenshot_resolution
            if res == "720p":
                img = img.resize((1280, 720), Image.Resampling.LANCZOS)
            elif res == "1080p":
                img = img.resize((1920, 1080), Image.Resampling.LANCZOS)
                
            img = img.convert('RGB')
            img.save(filepath, "JPEG", quality=85)
            
            self.counter += 1
            elapsed = self.get_elapsed_time()
            line = f"\n*  **[{self.counter}]**  **{elapsed}** - ![[{filename}]]"
            
            with open(self.current_file_path, 'a', encoding='utf-8') as f:
                f.write(line)

            # Return filepath so the GUI can embed the image inline
            return filepath
        except Exception as e:
            print(f"Error taking screenshot: {e}")
            return False


