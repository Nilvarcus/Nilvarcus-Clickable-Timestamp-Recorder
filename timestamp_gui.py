import tkinter as tk
from tkinter import font, messagebox
from pynput import keyboard
from threading import Thread
import json
import os
import sys
import customtkinter as ctk

# Import the TimestampManager from the local module
from timestamp_functions import TimestampManager

def get_base_path() -> str:
    """Gets the base path for the application, whether running as a script or a frozen exe."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

class Theme:
    """A centralized class for managing the application's visual theme."""
    # CustomTkinter handles main background/text colors in dark mode automatically,
    # but we still want our specific accent colors for buttons and states.
    BLUE = '#3498DB'
    HOVER_BLUE = '#2980B9'
    GREEN = '#2ECC71'
    HOVER_GREEN = '#27AE60'
    ORANGE = '#F39C12'
    HOVER_ORANGE = '#D35400'
    RED = '#E74C3C'
    HOVER_RED = '#C0392B'
    PURPLE = '#9B59B6'
    HOVER_PURPLE = '#8E44AD'
    TURQUOISE = '#1ABC9C'
    HOVER_TURQUOISE = '#16A085'
    GREY = '#95A5A6'
    HOVER_GREY = '#7F8C8D'

    FONT_FAMILY = "Segoe UI"
    FONT_TITLE = (FONT_FAMILY, 16, "bold")
    FONT_SUBTITLE = (FONT_FAMILY, 11, "bold")
    FONT_BODY = (FONT_FAMILY, 12)
    FONT_BUTTON = (FONT_FAMILY, 12, "bold")
    FONT_TEXT_AREA = ("Consolas", 12)

class RecordingWidget(ctk.CTkToplevel):
    """A floating mini-widget to show recording time and statuses."""
    def __init__(self, parent):
        super().__init__(parent.root)
        self.parent = parent
        self.title("Recording")
        self.attributes('-topmost', True) # Keep window on top
        
        # Make the window somewhat compact
        self.geometry("200x80")
        self.minsize(150, 60)
        self.resizable(False, False)
        
        self.create_widgets()
        self.update_timer()
        
        # Position near the top right of the main window or screen
        x = parent.root.winfo_x() + parent.root.winfo_width() + 10
        y = parent.root.winfo_y()
        self.geometry(f'+{x}+{y}')
        
        self.protocol("WM_DELETE_WINDOW", self.hide_widget)
        
    def create_widgets(self):
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)
        
        self.time_label = ctk.CTkLabel(main_frame, text="00:00:00", font=Theme.FONT_TITLE)
        self.time_label.pack(side=tk.TOP, expand=True)

        self.status_label = ctk.CTkLabel(main_frame, text="", font=Theme.FONT_BODY, text_color=Theme.RED)
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)

    def update_timer(self):
        if self.parent.timestamp_manager.stopwatch_running:
            elapsed_str = self.parent.timestamp_manager.get_elapsed_time()
            if elapsed_str:
                self.time_label.configure(text=elapsed_str)
            self.after(500, self.update_timer)

    def show_status(self, message, duration=3000, color=Theme.GREEN):
        self.status_label.configure(text=message, text_color=color)
        
        if hasattr(self, '_hide_status_job') and self._hide_status_job:
            self.after_cancel(self._hide_status_job)
            
        self._hide_status_job = self.after(duration, lambda: self.status_label.configure(text=""))
        
    def hide_widget(self):
        self.withdraw()

class SettingsWindow(ctk.CTkToplevel):
    """A Toplevel window for changing keybinds, styled and auto-sized."""
    def __init__(self, parent):
        super().__init__(parent.root)
        self.parent = parent
        self.title("Keybind Settings")
        self.transient(parent.root)
        self.grab_set()

        self.new_keybinds = parent.keybinds.copy()
        self.new_custom_texts = parent.custom_texts.copy()
        self.bind_buttons = {}
        self.text_entries = {}

        self.create_widgets()

        self.update_idletasks()
        width = self.winfo_reqwidth() + 60
        height = self.winfo_reqheight() + 40

        parent_x = self.parent.root.winfo_x()
        parent_y = self.parent.root.winfo_y()
        parent_width = self.parent.root.winfo_width()
        parent_height = self.parent.root.winfo_height()

        x = parent_x + (parent_width // 2) - (width // 2)
        y = parent_y + (parent_height // 2) - (height // 2)
        
        self.minsize(width, height)
        self.geometry(f'+{x}+{y}')

    def create_widgets(self):
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(expand=True, fill=tk.BOTH, padx=20, pady=20)

        title_label = ctk.CTkLabel(main_frame, text="Change Keybinds", font=Theme.FONT_TITLE)
        title_label.pack(pady=(0, 20))

        for action_id, label_text in self.parent.action_labels.items():
            frame = ctk.CTkFrame(main_frame)
            frame.pack(fill=tk.X, pady=4)
            
            label = ctk.CTkLabel(frame, text=f"{label_text}:", font=Theme.FONT_BODY, anchor='w')
            label.pack(side=tk.LEFT, padx=(15, 0), pady=8)

            key_str = self.new_keybinds.get(action_id, "N/A").upper()
            btn = ctk.CTkButton(
                frame, text=key_str, font=Theme.FONT_BODY, width=100, 
                command=lambda aid=action_id: self.change_key(aid)
            )
            btn.pack(side=tk.RIGHT, padx=(15, 15), pady=8)
            self.bind_buttons[action_id] = btn

            # If this is a custom note, add a text entry field
            if action_id.startswith("custom_note_"):
                entry = ctk.CTkEntry(frame, placeholder_text="Enter note text...", width=150, font=Theme.FONT_BODY)
                entry.insert(0, self.new_custom_texts.get(action_id, ""))
                entry.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(15, 0), pady=8)
                self.text_entries[action_id] = entry

        button_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        button_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(15, 0))
        button_frame.columnconfigure((0, 1), weight=1)

        save_btn = ctk.CTkButton(button_frame, text="Save", command=self.save_and_close, fg_color=Theme.GREEN, hover_color=Theme.HOVER_GREEN, font=Theme.FONT_BUTTON)
        save_btn.grid(row=0, column=0, padx=5, sticky='ew')
        
        cancel_btn = ctk.CTkButton(button_frame, text="Cancel", command=self.destroy, fg_color=Theme.RED, hover_color=Theme.HOVER_RED, font=Theme.FONT_BUTTON)
        cancel_btn.grid(row=0, column=1, padx=5, sticky='ew')

    def change_key(self, action_id: str):
        button = self.bind_buttons[action_id]
        original_text = button.cget('text')
        button.configure(text="Press a key...", state="disabled")

        def on_press_capture(key):
            new_key_str = self.parent.get_key_str(key)
            
            for aid, bound_key in self.new_keybinds.items():
                if bound_key == new_key_str and aid != action_id:
                    messagebox.showerror("Error", f"Key '{new_key_str.upper()}' is already bound.", parent=self)
                    button.configure(text=original_text, state="normal")
                    return False

            self.new_keybinds[action_id] = new_key_str
            button.configure(text=new_key_str.upper(), state="normal")
            return False

        listener = keyboard.Listener(on_press=on_press_capture)
        listener.start()

    def save_and_close(self):
        # Save custom texts from entries
        for action_id, entry in self.text_entries.items():
            self.new_custom_texts[action_id] = entry.get()
            
        self.parent.keybinds = self.new_keybinds
        self.parent.custom_texts = self.new_custom_texts
        self.parent.save_keybinds()
        self.parent.update_button_text()
        self.destroy()

class TimestampApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Nilvarcus Timestamp App")
        self.root.geometry("450x650")
        self.root.minsize(450, 500)
        self.root.resizable(True, True)
        
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        self.timestamp_manager = TimestampManager(base_path=get_base_path())
        self.keybinds_file = os.path.join(get_base_path(), 'keybinds.json')
        self.buttons = {}
        self.mini_widget = None
        
        self.action_labels = {
            'create_file': "Create / Open File", 'start_recording': "Start Recording",
            'mark_time': "Mark Time", 'stop_recording': "Stop Recording",
            'save_short': "Save Short", 'mark_voice_note': "Voice Note",
            'custom_note_1': "Custom Note 1", 'custom_note_2': "Custom Note 2",
            'custom_note_3': "Custom Note 3", 'custom_note_4': "Custom Note 4",
            'custom_note_5': "Custom Note 5",
        }
        self.default_keybinds = {
            'create_file': 'f13', 'start_recording': 'f14', 'mark_time': 'f15',
            'stop_recording': 'f16', 'save_short': 'f18', 'mark_voice_note': 'f17',
            'custom_note_1': 'f20', 'custom_note_2': 'f21', 'custom_note_3': 'f22',
            'custom_note_4': 'f23', 'custom_note_5': 'f24',
        }
        self.default_texts = {
            'custom_note_1': 'Note 1', 'custom_note_2': 'Note 2',
            'custom_note_3': 'Note 3', 'custom_note_4': 'Note 4',
            'custom_note_5': 'Note 5',
        }
        self.custom_texts = {}
        self.load_keybinds()

        self._create_widgets()
        self.update_button_text()
        
        self.timestamp_manager.register_gui_callback(self.on_transcription_status)

        self.auto_save()
        self._start_keyboard_listener()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _create_widgets(self):
        self._create_header()
        self._create_text_viewer()
        self._create_filename_display()
        self._create_buttons()
        
    def load_keybinds(self):
        try:
            with open(self.keybinds_file, 'r') as f:
                data = json.load(f)
                
            # Handle legacy format where it was just the keybinds dictionary directly
            if 'keybinds' in data:
                self.keybinds = data.get('keybinds', {})
                self.custom_texts = data.get('custom_texts', {})
            else:
                self.keybinds = data
                self.custom_texts = {}
                
            for action in self.default_keybinds:
                if action not in self.keybinds:
                    self.keybinds[action] = self.default_keybinds[action]
            for action in self.default_texts:
                if action not in self.custom_texts:
                    self.custom_texts[action] = self.default_texts[action]
                    
        except (FileNotFoundError, json.JSONDecodeError):
            self.keybinds = self.default_keybinds.copy()
            self.custom_texts = self.default_texts.copy()
        
        self.save_keybinds()

    def save_keybinds(self):
        with open(self.keybinds_file, 'w') as f:
            data = {
                'keybinds': self.keybinds,
                'custom_texts': self.custom_texts
            }
            json.dump(data, f, indent=4)

    def on_closing(self):
        self.save_changes()
        self.save_keybinds()
        print("Final autosave and keybinds saved before closing")
        self.root.destroy()

    def auto_save(self):
        self.save_changes()
        self.root.after(60000, self.auto_save)

    def _create_header(self):
        header_container = ctk.CTkFrame(self.root)
        header_container.grid(row=0, column=0, sticky='ew', padx=10, pady=(10, 0))
        
        title_label = ctk.CTkLabel(header_container, text="Nilvarcus Timestamp App", font=Theme.FONT_TITLE)
        title_label.pack(fill=tk.X, expand=True, pady=15)

    def _create_text_viewer(self):
        text_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        text_frame.grid(row=1, column=0, padx=10, pady=10, sticky='nsew')
        
        self.text_viewer = ctk.CTkTextbox(text_frame, wrap=tk.WORD, font=Theme.FONT_TEXT_AREA)
        # Using pack so it expands naturally
        self.text_viewer.pack(expand=True, fill=tk.BOTH)

    def _create_filename_display(self):
        filename_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        filename_frame.grid(row=2, column=0, padx=10, pady=(0, 5), sticky='ew')

        label = ctk.CTkLabel(filename_frame, text="Current File:", font=Theme.FONT_SUBTITLE)
        label.pack(side=tk.LEFT, padx=(5, 10))
        
        self.filename_label = ctk.CTkLabel(filename_frame, text="No file open", font=Theme.FONT_BODY, anchor="w")
        self.filename_label.pack(side=tk.LEFT, expand=True, fill=tk.X)

    def _update_filename_display(self):
        if self.timestamp_manager.current_file_path:
            self.filename_label.configure(text=os.path.basename(self.timestamp_manager.current_file_path))
        else:
            self.filename_label.configure(text="No file open")

    def _create_buttons(self):
        button_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        button_frame.grid(row=3, column=0, padx=10, pady=(0, 10), sticky='ew')
        button_frame.columnconfigure((0, 1), weight=1)

        button_config = {
            'create_file': (self.create_file, Theme.BLUE, Theme.HOVER_BLUE, 0, 0),
            'start_recording': (self.start_recording, Theme.GREEN, Theme.HOVER_GREEN, 0, 1),
            'mark_time': (self.mark_time, Theme.ORANGE, Theme.HOVER_ORANGE, 1, 0),
            'stop_recording': (self.stop_recording, Theme.RED, Theme.HOVER_RED, 1, 1),
            'save_short': (self.save_short, Theme.TURQUOISE, Theme.HOVER_TURQUOISE, 2, 0),
            'mark_voice_note': (self.mark_voice_note, Theme.PURPLE, Theme.HOVER_PURPLE, 2, 1),
        }

        for action_id, (command, bg, hover, row, col) in button_config.items():
            btn = ctk.CTkButton(button_frame, command=command, fg_color=bg, hover_color=hover, font=Theme.FONT_BUTTON)
            btn.grid(row=row, column=col, padx=4, pady=4, sticky='ew')
            self.buttons[action_id] = btn
        
        settings_btn = ctk.CTkButton(button_frame, text="Settings", command=self.open_settings_window, fg_color=Theme.GREY, hover_color=Theme.HOVER_GREY, font=Theme.FONT_BUTTON)
        settings_btn.grid(row=3, column=0, columnspan=2, padx=4, pady=4, sticky='ew')

    def update_button_text(self):
        for action_id, button in self.buttons.items():
            key_name = self.keybinds.get(action_id, 'N/A').upper()
            label_text = self.action_labels.get(action_id, 'Unknown')
            button.configure(text=f"{label_text} ({key_name})")

    def open_settings_window(self):
        SettingsWindow(self)

    def get_key_str(self, key) -> str:
        if hasattr(key, 'name'): return key.name
        if hasattr(key, 'char'): return key.char
        return 'unknown'

    def _start_keyboard_listener(self):
        self.action_map = {
            'create_file': self.create_file, 'start_recording': self.start_recording,
            'mark_time': self.mark_time, 'stop_recording': self.stop_recording,
            'save_short': self.save_short, 'mark_voice_note': self.mark_voice_note,
            'custom_note_1': lambda: self.mark_custom_note_n('custom_note_1'),
            'custom_note_2': lambda: self.mark_custom_note_n('custom_note_2'),
            'custom_note_3': lambda: self.mark_custom_note_n('custom_note_3'),
            'custom_note_4': lambda: self.mark_custom_note_n('custom_note_4'),
            'custom_note_5': lambda: self.mark_custom_note_n('custom_note_5'),
        }
        Thread(target=lambda: keyboard.Listener(on_press=self._on_press).start(), daemon=True).start()

    def _on_press(self, key):
        key_str = self.get_key_str(key)
        key_to_action = {v: k for k, v in self.keybinds.items()}
        action_id = key_to_action.get(key_str)
        if action_id in self.action_map:
            try:
                self.root.after(0, self.action_map[action_id])
            except Exception as e:
                print(f"Error executing action '{action_id}': {e}")

    def create_file(self):
        file_path = self.timestamp_manager.create_file()
        if file_path: self.update_text_viewer(); self._update_filename_display()

    def start_recording(self):
        self.save_changes()
        if self.timestamp_manager.start_recording():
            self.update_text_viewer()
            if self.mini_widget is None or not self.mini_widget.winfo_exists():
                self.mini_widget = RecordingWidget(self)
            else:
                self.mini_widget.deiconify()
                self.mini_widget.update_timer()

    def mark_time(self):
        self.save_changes()
        if self.timestamp_manager.mark_time():
            self.update_text_viewer()
            if self.mini_widget and self.mini_widget.winfo_exists():
                self.mini_widget.show_status("Timestamp Marked!", color=Theme.BLUE)

    def stop_recording(self):
        self.save_changes()
        if self.timestamp_manager.stop_recording():
            self.update_text_viewer()
            if self.mini_widget and self.mini_widget.winfo_exists():
                self.mini_widget.destroy()
                self.mini_widget = None

    def save_short(self):
        self.save_changes()
        if self.timestamp_manager.save_short():
            self.update_text_viewer()
            if self.mini_widget and self.mini_widget.winfo_exists():
                self.mini_widget.show_status("Short Saved!", color=Theme.TURQUOISE)

    def mark_voice_note(self):
        self.save_changes()
        self.mark_time()
        if self.timestamp_manager.mark_voice_note():
            pass

    def mark_custom_note_n(self, action_id):
        self.save_changes()
        custom_text = self.custom_texts.get(action_id, "")
        if self.timestamp_manager.mark_custom_note(custom_text):
            self.update_text_viewer()
            if self.mini_widget and self.mini_widget.winfo_exists():
                self.mini_widget.show_status(f"Added: {custom_text}", color=Theme.BLUE)

    def on_transcription_status(self, status):
        def update_gui():
            if status.startswith("COMPLETE|"):
                transcription = status.split("|", 1)[1]
                if transcription:
                    self.text_viewer.insert(tk.END, f" **Voice Note:** {transcription}\n")
                self.save_changes()
                
                key_name = self.keybinds.get('mark_voice_note', 'N/A').upper()
                label_text = self.action_labels.get('mark_voice_note', 'Unknown')
                if 'mark_voice_note' in self.buttons:
                    self.buttons['mark_voice_note'].configure(text=f"{label_text} ({key_name})")
                self.text_viewer.see(tk.END)
                
                if self.mini_widget and self.mini_widget.winfo_exists():
                    self.mini_widget.show_status("Transcribed!", duration=4000, color=Theme.GREEN)
            else:
                if 'mark_voice_note' in self.buttons:
                    self.buttons['mark_voice_note'].configure(text=status)
                
                if self.mini_widget and self.mini_widget.winfo_exists():
                    self.mini_widget.show_status(status, duration=10000, color=Theme.PURPLE)
                    
        self.root.after(0, update_gui)
            
    def save_changes(self):
        if self.timestamp_manager.current_file_path:
            self.timestamp_manager.save_changes(self.text_viewer.get("1.0", tk.END))

    def update_text_viewer(self):
        text_content = self.timestamp_manager.read_file_content()
        self.text_viewer.delete("1.0", tk.END)
        self.text_viewer.insert(tk.END, text_content)
        self.text_viewer.see(tk.END)

def main():
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    app = TimestampApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
