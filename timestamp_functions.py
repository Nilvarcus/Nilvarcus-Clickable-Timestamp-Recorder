import time
import os
from datetime import datetime
from pynput import keyboard

class TimestampManager:
    def __init__(self):
        """Initialize the timestamp manager."""
        self.stopwatch_running = False
        self.start_time = None
        self.current_file_path = None
        self.counter = 0  # Initialize counter for timestamps

    def create_file(self, initial_dir=None):
        """
        Create a new file with a timestamped name.
        
        Args:
            initial_dir (str, optional): Directory to start file dialog. 
                                         Defaults to user home directory.
        
        Returns:
            str: Path of the created file, or None if file creation was cancelled.
        """
        import tkinter as tk
        from tkinter import filedialog

        # Use user home directory if no initial directory provided
        initial_dir = initial_dir or os.path.expanduser("~")
        
        # Generate default filename with current timestamp
        default_name = datetime.now().strftime("[%d-%m-%Y][%H-%M-%S] - WRITE HERE.txt")
        
        # Open file dialog
        file_name = filedialog.asksaveasfilename(
            title="Save File",
            filetypes=[("Text Files", "*.txt")],
            defaultextension=".txt",
            initialdir=initial_dir,
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
                file.write(f"\n0 - {timestamp} -\n\n")
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
                file.write(f"\n{self.counter} - {formatted_time} -")
            return formatted_time
        return None

    def stop_recording(self):
        """
        Stop and reset the stopwatch.
        
        Returns:
            bool: True if recording stopped successfully, False otherwise.
        """
        if self.current_file_path and self.stopwatch_running:
            with open(self.current_file_path, "a", encoding="utf-8") as file:
                file.write("\n______________________________________________\n")
            self.stopwatch_running = False
            self.start_time = None
            self.counter = 0  # Reset counter on stop
            return True
        return False

    def save_short(self):
        """
        Take a short and add it to the current file.
        
        Returns:
            bool: True if short was saved successfully, False otherwise.
        """
        if self.current_file_path:
            with open(self.current_file_path, "a", encoding="utf-8") as file:
                timestamp = datetime.now().strftime("[%d-%m][%H-%M-%S]")
                file.write(f"\nSHORT-{timestamp} -\n\n")
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
                file.write(text_content.strip())
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
    


    def mark_voice_note(self):
        """
        Mark 'VOICE NOTE' in the file.
        
        Returns:
            bool: True if voice note marked successfully, False otherwise.
        """
        if self.current_file_path and self.stopwatch_running:
            with open(self.current_file_path, "a", encoding="utf-8") as file:
                file.write("*VOICE NOTE*")
            return True
        return False
