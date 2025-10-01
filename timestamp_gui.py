import tkinter as tk
from tkinter import scrolledtext, font, Toplevel, messagebox
from pynput import keyboard
from threading import Thread
import json
import os
import sys

# Import the TimestampManager from the local module
from timestamp_functions import TimestampManager

def get_base_path() -> str:
    """Gets the base path for the application, whether running as a script or a frozen exe."""
    if getattr(sys, 'frozen', False):
        # If the application is run as a bundle, the base path is the executable's directory
        return os.path.dirname(sys.executable)
    else:
        # If the application is run as a script, the base path is the script's directory
        return os.path.dirname(os.path.abspath(__file__))

class Theme:
    """A centralized class for managing the application's visual theme."""
    COLOR_BACKGROUND = '#F0F4F8'
    COLOR_FRAME = '#FFFFFF'
    COLOR_TEXT_PRIMARY = '#2C3E50'
    COLOR_TEXT_SECONDARY = '#5D6D7E'
    COLOR_BORDER = '#D6DBDF'

    # Button Colors
    BLUE = ('#3498DB', '#2980B9')
    GREEN = ('#2ECC71', '#27AE60')
    ORANGE = ('#F39C12', '#D35400')
    RED = ('#E74C3C', '#C0392B')
    PURPLE = ('#9B59B6', '#8E44AD')
    TURQUOISE = ('#1ABC9C', '#16A085')
    GREY = ('#95A5A6', '#7F8C8D')

    # Fonts
    FONT_FAMILY = "Segoe UI"
    FONT_TITLE = (FONT_FAMILY, 16, "bold")
    FONT_SUBTITLE = (FONT_FAMILY, 11, "bold")
    FONT_BODY = (FONT_FAMILY, 10)
    FONT_BUTTON = (FONT_FAMILY, 10, "bold")
    FONT_TEXT_AREA = ("Consolas", 11)


class StyledButton(tk.Button):
    """A custom button with enhanced styling and hover effects."""
    def __init__(self, master, bg_color, hover_color, **kwargs):
        super().__init__(master, **kwargs)
        
        self.bg_color = bg_color
        self.hover_color = hover_color
        
        self.configure(
            font=Theme.FONT_BUTTON,
            bg=self.bg_color,
            fg='white',
            activebackground=self.hover_color,
            activeforeground='white',
            relief=tk.FLAT,
            borderwidth=0,
            pady=8,
            cursor="hand2",
            highlightthickness=2,
            highlightbackground=self.bg_color
        )
        
        self.bind('<Enter>', self._on_enter)
        self.bind('<Leave>', self._on_leave)

    def _on_enter(self, e):
        self.configure(bg=self.hover_color, highlightbackground=self.hover_color)

    def _on_leave(self, e):
        self.configure(bg=self.bg_color, highlightbackground=self.bg_color)


class SettingsWindow(Toplevel):
    """A Toplevel window for changing keybinds, styled and auto-sized."""
    def __init__(self, parent):
        super().__init__(parent.root)
        self.parent = parent
        self.title("Keybind Settings")
        # self.geometry("450x400") # <-- REMOVE THIS LINE
        self.configure(bg=Theme.COLOR_BACKGROUND)
        self.transient(parent.root)
        self.grab_set()

        self.new_keybinds = parent.keybinds.copy()
        self.bind_buttons = {}

        self.create_widgets()

        # --- NEW: Auto-sizing and Centering Logic ---
        # 1. Force the window to calculate the size it needs for all its widgets
        self.update_idletasks()

        # 2. Get the calculated required size
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()

        # 3. Get the main window's position and size
        parent_x = self.parent.root.winfo_x()
        parent_y = self.parent.root.winfo_y()
        parent_width = self.parent.root.winfo_width()
        parent_height = self.parent.root.winfo_height()

        # 4. Calculate the position to center the settings window over the main window
        x = parent_x + (parent_width // 2) - (width // 2)
        y = parent_y + (parent_height // 2) - (height // 2)
        
        # 5. Set the window's minimum size to what we calculated, and set its position
        self.minsize(width, height)
        self.geometry(f'+{x}+{y}')


    def create_widgets(self):
        main_frame = tk.Frame(self, bg=Theme.COLOR_BACKGROUND, padx=20, pady=20)
        main_frame.pack(expand=True, fill=tk.BOTH)

        title_label = tk.Label(main_frame, text="Change Keybinds", font=Theme.FONT_TITLE, bg=Theme.COLOR_BACKGROUND, fg=Theme.COLOR_TEXT_PRIMARY)
        title_label.pack(pady=(0, 20))

        for action_id, label_text in self.parent.action_labels.items():
            frame = tk.Frame(main_frame, bg=Theme.COLOR_FRAME, pady=8)
            frame.pack(fill=tk.X, pady=4)
            
            label = tk.Label(frame, text=f"{label_text}:", font=Theme.FONT_BODY, bg=Theme.COLOR_FRAME, fg=Theme.COLOR_TEXT_SECONDARY, anchor='w')
            label.pack(side=tk.LEFT, padx=(15, 0))

            key_str = self.new_keybinds.get(action_id, "N/A").upper()
            btn = tk.Button(
                frame, text=key_str, font=Theme.FONT_BODY, width=15, 
                relief=tk.FLAT, bg='#ECF0F1', fg=Theme.COLOR_TEXT_PRIMARY,
                command=lambda aid=action_id: self.change_key(aid)
            )
            btn.pack(side=tk.RIGHT, padx=(0, 15))
            self.bind_buttons[action_id] = btn

        button_frame = tk.Frame(main_frame, bg=Theme.COLOR_BACKGROUND, pady=15)
        button_frame.pack(fill=tk.X, side=tk.BOTTOM)
        button_frame.columnconfigure((0, 1), weight=1)

        save_btn = StyledButton(button_frame, text="Save", command=self.save_and_close, bg_color=Theme.GREEN[0], hover_color=Theme.GREEN[1])
        save_btn.grid(row=0, column=0, padx=5, sticky='ew')
        
        cancel_btn = StyledButton(button_frame, text="Cancel", command=self.destroy, bg_color=Theme.RED[0], hover_color=Theme.RED[1])
        cancel_btn.grid(row=0, column=1, padx=5, sticky='ew')

    def change_key(self, action_id: str):
        button = self.bind_buttons[action_id]
        original_text = button.cget('text')
        button.config(text="Press a key...", state=tk.DISABLED)

        def on_press_capture(key):
            new_key_str = self.parent.get_key_str(key)
            
            for aid, bound_key in self.new_keybinds.items():
                if bound_key == new_key_str and aid != action_id:
                    messagebox.showerror("Error", f"Key '{new_key_str.upper()}' is already bound.", parent=self)
                    button.config(text=original_text, state=tk.NORMAL)
                    return False

            self.new_keybinds[action_id] = new_key_str
            button.config(text=new_key_str.upper(), state=tk.NORMAL)
            return False

        listener = keyboard.Listener(on_press=on_press_capture)
        listener.start()

    def save_and_close(self):
        self.parent.keybinds = self.new_keybinds
        self.parent.save_keybinds()
        self.parent.update_button_text()
        self.destroy()

class TimestampApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Nilvarcus Timestamp App")
        self.root.geometry("450x650")
        self.root.minsize(450, 500)
        self.root.configure(bg=Theme.COLOR_BACKGROUND)
        
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        self.timestamp_manager = TimestampManager()
        self.keybinds_file = os.path.join(get_base_path(), 'keybinds.json')
        self.buttons = {}
        
        self.action_labels = {
            'create_file': "Create / Open File", 'start_recording': "Start Recording",
            'mark_time': "Mark Time", 'stop_recording': "Stop Recording",
            'save_short': "Save Short", 'mark_voice_note': "Voice Note",
        }
        self.default_keybinds = {
            'create_file': 'f13', 'start_recording': 'f14', 'mark_time': 'f15',
            'stop_recording': 'f16', 'save_short': 'f18', 'mark_voice_note': 'f17',
        }
        self.load_keybinds()

        self._create_widgets()
        self.update_button_text()

        self.auto_save()
        self._start_keyboard_listener()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _create_widgets(self):
        """Single method to create all GUI elements."""
        self._create_header()
        self._create_text_viewer()
        self._create_filename_display()
        self._create_buttons()
        
    def load_keybinds(self):
        try:
            with open(self.keybinds_file, 'r') as f:
                self.keybinds = json.load(f)
            for action in self.default_keybinds:
                if action not in self.keybinds:
                    self.keybinds[action] = self.default_keybinds[action]
        except (FileNotFoundError, json.JSONDecodeError):
            self.keybinds = self.default_keybinds.copy()
        self.save_keybinds()

    def save_keybinds(self):
        with open(self.keybinds_file, 'w') as f:
            json.dump(self.keybinds, f, indent=4)

    def on_closing(self):
        self.save_changes()
        self.save_keybinds()
        print("Final autosave and keybinds saved before closing")
        self.root.destroy()

    def auto_save(self):
        self.save_changes()
        self.root.after(60000, self.auto_save)

    def _create_header(self):
        header_container = tk.Frame(self.root, bg=Theme.COLOR_FRAME)
        header_container.grid(row=0, column=0, sticky='ew', padx=10, pady=(10, 0))
        
        title_label = tk.Label(header_container, text="Nilvarcus Timestamp App", font=Theme.FONT_TITLE, bg=Theme.COLOR_FRAME, fg=Theme.COLOR_TEXT_PRIMARY, pady=15)
        title_label.pack(fill=tk.X, expand=True)

        # Bottom border for separation
        separator = tk.Frame(header_container, height=1, bg=Theme.COLOR_BORDER)
        separator.pack(fill=tk.X)

    def _create_text_viewer(self):
        text_frame = tk.Frame(self.root, bg=Theme.COLOR_FRAME, bd=1, relief=tk.SOLID, highlightbackground=Theme.COLOR_BORDER, highlightthickness=1)
        text_frame.grid(row=1, column=0, padx=10, pady=10, sticky='nsew')
        
        self.text_viewer = scrolledtext.ScrolledText(text_frame, wrap=tk.WORD, font=Theme.FONT_TEXT_AREA, bg=Theme.COLOR_FRAME, fg=Theme.COLOR_TEXT_PRIMARY, borderwidth=0, relief=tk.FLAT, padx=10, pady=10)
        self.text_viewer.pack(expand=True, fill=tk.BOTH)

    def _create_filename_display(self):
        filename_frame = tk.Frame(self.root, bg=Theme.COLOR_BACKGROUND)
        filename_frame.grid(row=2, column=0, padx=10, pady=(0, 5), sticky='ew')

        label = tk.Label(filename_frame, text="Current File:", font=Theme.FONT_SUBTITLE, bg=Theme.COLOR_BACKGROUND, fg=Theme.COLOR_TEXT_PRIMARY)
        label.pack(side=tk.LEFT, padx=(5, 10))
        
        self.filename_label = tk.Label(filename_frame, text="No file open", font=Theme.FONT_BODY, bg=Theme.COLOR_BACKGROUND, fg=Theme.COLOR_TEXT_SECONDARY, anchor="w")
        self.filename_label.pack(side=tk.LEFT, expand=True, fill=tk.X)

    def _update_filename_display(self):
        if self.timestamp_manager.current_file_path:
            self.filename_label.config(text=os.path.basename(self.timestamp_manager.current_file_path))
        else:
            self.filename_label.config(text="No file open")

    def _create_buttons(self):
        button_frame = tk.Frame(self.root, bg=Theme.COLOR_BACKGROUND)
        button_frame.grid(row=3, column=0, padx=10, pady=10, sticky='ew')
        button_frame.columnconfigure((0, 1), weight=1)

        button_config = {
            'create_file': (self.create_file, Theme.BLUE, 0, 0),
            'start_recording': (self.start_recording, Theme.GREEN, 0, 1),
            'mark_time': (self.mark_time, Theme.ORANGE, 1, 0),
            'stop_recording': (self.stop_recording, Theme.RED, 1, 1),
            'save_short': (self.save_short, Theme.TURQUOISE, 2, 0),
            'mark_voice_note': (self.mark_voice_note, Theme.PURPLE, 2, 1),
        }

        for action_id, (command, (bg, hover), row, col) in button_config.items():
            btn = StyledButton(button_frame, command=command, bg_color=bg, hover_color=hover)
            btn.grid(row=row, column=col, padx=5, pady=4, sticky='ew')
            self.buttons[action_id] = btn
        
        settings_btn = StyledButton(button_frame, text="Settings", command=self.open_settings_window, bg_color=Theme.GREY[0], hover_color=Theme.GREY[1])
        settings_btn.grid(row=3, column=0, columnspan=2, padx=5, pady=4, sticky='ew')

    def update_button_text(self):
        for action_id, button in self.buttons.items():
            key_name = self.keybinds.get(action_id, 'N/A').upper()
            label_text = self.action_labels.get(action_id, 'Unknown')
            button.config(text=f"{label_text} ({key_name})")

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
        }
        Thread(target=lambda: keyboard.Listener(on_press=self._on_press).start(), daemon=True).start()

    def _on_press(self, key):
        key_str = self.get_key_str(key)
        key_to_action = {v: k for k, v in self.keybinds.items()}
        action_id = key_to_action.get(key_str)
        if action_id in self.action_map:
            try:
                # Schedule GUI updates to be run in the main thread
                self.root.after(0, self.action_map[action_id])
            except Exception as e:
                print(f"Error executing action '{action_id}': {e}")

    # --- Action Methods (wrapped to ensure GUI updates happen in main thread) ---
    def create_file(self):
        file_path = self.timestamp_manager.create_file()
        if file_path: self.update_text_viewer(); self._update_filename_display()

    def start_recording(self):
        self.save_changes()
        if self.timestamp_manager.start_recording(): self.update_text_viewer()

    def mark_time(self):
        self.save_changes()
        if self.timestamp_manager.mark_time(): self.update_text_viewer()

    def stop_recording(self):
        self.save_changes()
        if self.timestamp_manager.stop_recording(): self.update_text_viewer()

    def save_short(self):
        self.save_changes()
        if self.timestamp_manager.save_short(): self.update_text_viewer()

    def mark_voice_note(self):
        self.save_changes()
        self.mark_time()
        if self.timestamp_manager.mark_voice_note(): self.update_text_viewer()
            
    def save_changes(self):
        if self.timestamp_manager.current_file_path:
            self.timestamp_manager.save_changes(self.text_viewer.get("1.0", tk.END))

    def update_text_viewer(self):
        text_content = self.timestamp_manager.read_file_content()
        self.text_viewer.delete("1.0", tk.END)
        self.text_viewer.insert(tk.END, text_content)
        self.text_viewer.see(tk.END)

def main():
    root = tk.Tk()
    app = TimestampApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
