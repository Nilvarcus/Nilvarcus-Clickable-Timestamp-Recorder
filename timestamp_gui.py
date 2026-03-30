import tkinter as tk
from tkinter import font, messagebox, filedialog
from pynput import keyboard
from threading import Thread
import json
import os
import sys
import customtkinter as ctk

# Import the TimestampManager and OBSManager from local modules
from timestamp_functions import TimestampManager
from timestamp_obs import OBSManager

def get_base_path() -> str:
    """Gets the base path for the application, whether running as a script or a frozen exe."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

def get_input_devices():
    """
    Returns a list of (index, name) tuples for all available audio input devices.
    Falls back to an empty list if sounddevice is unavailable.
    """
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        return [(i, d['name']) for i, d in enumerate(devices) if d['max_input_channels'] > 0]
    except Exception:
        return []

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
    """A floating HUD widget to show recording time, status, and recent logs."""
    def __init__(self, parent):
        super().__init__(parent.root)
        self.parent = parent
        self.title("Recording HUD")
        self.attributes('-topmost', True) # Keep window on top
        self.attributes('-alpha', parent.hud_opacity)
        
        self.geometry("280x200")
        self.minsize(280, 180)
        self.resizable(False, False)
        
        self.create_widgets()
        self.update_timer()
        
        # Border animation states
        self.anim_step = 0
        self.anim_dir = 1
        self.current_border_state = "recording" # default
        self._animate_border()
        
        x = parent.root.winfo_x() + parent.root.winfo_width() + 10
        y = parent.root.winfo_y()
        self.geometry(f'+{x}+{y}')
        
        self.protocol("WM_DELETE_WINDOW", self.hide_widget)
        
    def create_widgets(self):
        # We need a frame with a border for the glow
        self.main_frame = ctk.CTkFrame(self, fg_color="#1E1E1E", border_width=3, border_color=Theme.RED)
        self.main_frame.pack(expand=True, fill=tk.BOTH, padx=5, pady=5)
        
        top_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        top_frame.pack(side=tk.TOP, fill=tk.X, pady=(10, 5))
        
        self.time_label = ctk.CTkLabel(top_frame, text="00:00:00", font=Theme.FONT_TITLE)
        self.time_label.pack(side=tk.TOP)

        self.status_label = ctk.CTkLabel(top_frame, text="", font=Theme.FONT_BODY, text_color=Theme.RED)
        self.status_label.pack(side=tk.TOP)
        
        sep = ctk.CTkFrame(self.main_frame, height=2, fg_color=Theme.GREY)
        sep.pack(side=tk.TOP, fill=tk.X, padx=10, pady=5)
        
        self.log_textbox = ctk.CTkTextbox(
            self.main_frame, font=Theme.FONT_BODY, fg_color="transparent", 
            text_color="#DDDDDD", wrap=tk.WORD
        )
        self.log_textbox.pack(side=tk.TOP, expand=True, fill=tk.BOTH, padx=10, pady=(0, 10))
        self.refresh_logs()

    def update_timer(self):
        if not self.winfo_exists(): return
        if self.parent.timestamp_manager.stopwatch_running:
            elapsed_str = self.parent.timestamp_manager.get_elapsed_time()
            if elapsed_str:
                self.time_label.configure(text=elapsed_str)
            self.after(500, self.update_timer)

    def set_border_state(self, state):
        self.current_border_state = state
        self.anim_step = 0
        self.anim_dir = 1

    def _animate_border(self):
        if not self.winfo_exists(): return
        states = {
            "recording": ("#FF3333", "#660000"),
            "transcribing": ("#FF9900", "#663300"),
            "error": ("#FF0000", "#330000"),
            "success": ("#00FF99", "#003311"),
        }
        
        if self.current_border_state not in states:
            self.current_border_state = "recording"
            
        color1, color2 = states[self.current_border_state]
        
        def hex_to_rgb(h): return tuple(int(h[i:i+2], 16) for i in (1, 3, 5))
        def rgb_to_hex(r, g, b): return f"#{int(r):02x}{int(g):02x}{int(b):02x}"
        
        c1, c2 = hex_to_rgb(color1), hex_to_rgb(color2)
        
        steps = 20
        self.anim_step += self.anim_dir
        if self.anim_step >= steps:
            self.anim_step = steps
            self.anim_dir = -1
        elif self.anim_step <= 0:
            self.anim_step = 0
            self.anim_dir = 1
            
        ratio = self.anim_step / steps
        r = c1[0] * ratio + c2[0] * (1 - ratio)
        g = c1[1] * ratio + c2[1] * (1 - ratio)
        b = c1[2] * ratio + c2[2] * (1 - ratio)
        
        new_color = rgb_to_hex(r, g, b)
        try:
            self.main_frame.configure(border_color=new_color)
        except Exception:
            return
            
        self._anim_job = self.after(50, self._animate_border)

    def show_status(self, message, duration=3000, color=Theme.GREEN):
        self.status_label.configure(text=message, text_color=color)
        
        # Update border state temporally
        if color == Theme.RED: self.set_border_state("error")
        elif color == Theme.PURPLE or message == "Transcribing...": self.set_border_state("transcribing")
        else: self.set_border_state("success")
            
        if hasattr(self, '_hide_status_job') and self._hide_status_job:
            self.after_cancel(self._hide_status_job)
            
        def reset_status():
            self.status_label.configure(text="")
            self.set_border_state("recording")
            
        self._hide_status_job = self.after(duration, reset_status)
        
    def refresh_logs(self):
        events = self.parent.timestamp_manager.get_recent_log_events(count=3)
        self.log_textbox.configure(state='normal')
        self.log_textbox.delete("1.0", tk.END)
        for e in events:
            self.log_textbox.insert(tk.END, e + "\n\n")
        self.log_textbox.see(tk.END)
        self.log_textbox.configure(state='disabled')

    def destroy(self):
        if hasattr(self, '_anim_job'): self.after_cancel(self._anim_job)
        if hasattr(self, '_hide_status_job') and self._hide_status_job: self.after_cancel(self._hide_status_job)
        super().destroy()

    def hide_widget(self):
        self.withdraw()

class SettingsWindow(ctk.CTkToplevel):
    """A Toplevel window for app settings, organised into tabs."""
    def __init__(self, parent):
        super().__init__(parent.root)
        self.parent = parent
        self.title("Settings")
        self.transient(parent.root)
        self.grab_set()
        self.resizable(True, True)

        self.new_keybinds = parent.keybinds.copy()
        self.new_custom_texts = parent.custom_texts.copy()
        self.new_output_folder = parent.output_folder
        self.new_mic_device_index = parent.mic_device_index
        self.new_obs_settings = parent.obs_settings.copy()
        self.new_hud_enabled = parent.hud_enabled
        self.new_hud_opacity = parent.hud_opacity
        self.bind_buttons = {}
        self.text_entries = {}
        self._input_devices = get_input_devices()

        self.create_widgets()

        # Centre on parent at a sensible starting size
        self.minsize(560, 420)
        self.geometry("640x580")
        px = self.parent.root.winfo_x()
        py = self.parent.root.winfo_y()
        pw = self.parent.root.winfo_width()
        ph = self.parent.root.winfo_height()
        self.geometry(f"+{px + pw // 2 - 320}+{py + ph // 2 - 290}")

    def create_widgets(self):
        root_frame = ctk.CTkFrame(self, fg_color="transparent")
        root_frame.pack(expand=True, fill=tk.BOTH, padx=16, pady=16)
        root_frame.grid_rowconfigure(0, weight=1)
        root_frame.grid_columnconfigure(0, weight=1)

        # ── Tab view ──────────────────────────────────────────────────────────
        tabs = ctk.CTkTabview(root_frame)
        tabs.grid(row=0, column=0, sticky='nsew')

        tab_general  = tabs.add("General")
        tab_obs      = tabs.add("OBS")
        tab_keybinds = tabs.add("Keybinds")

        for t in (tab_general, tab_obs, tab_keybinds):
            t.grid_rowconfigure(0, weight=1)
            t.grid_columnconfigure(0, weight=1)

        # ── GENERAL TAB ───────────────────────────────────────────────────────
        gen = ctk.CTkScrollableFrame(tab_general, fg_color="transparent")
        gen.grid(row=0, column=0, sticky='nsew')
        gen.columnconfigure((0, 1), weight=1)

        # Output Folder — left column
        ctk.CTkLabel(gen, text="Output Folder", font=Theme.FONT_SUBTITLE, anchor='w').grid(
            row=0, column=0, sticky='w', padx=(8, 4), pady=(8, 2))

        folder_frame = ctk.CTkFrame(gen)
        folder_frame.grid(row=1, column=0, sticky='ew', padx=(8, 4), pady=(0, 12))
        folder_frame.columnconfigure(0, weight=1)

        self.folder_label = ctk.CTkLabel(
            folder_frame, text=self.new_output_folder,
            font=Theme.FONT_BODY, anchor='w', wraplength=200
        )
        self.folder_label.grid(row=0, column=0, sticky='ew', padx=10, pady=(8, 4))
        ctk.CTkButton(
            folder_frame, text="Browse", font=Theme.FONT_BUTTON,
            command=self._browse_folder
        ).grid(row=1, column=0, padx=10, pady=(4, 10), sticky='ew')

        # Microphone — right column
        ctk.CTkLabel(gen, text="Microphone", font=Theme.FONT_SUBTITLE, anchor='w').grid(
            row=0, column=1, sticky='w', padx=(4, 8), pady=(8, 2))

        mic_frame = ctk.CTkFrame(gen)
        mic_frame.grid(row=1, column=1, sticky='nsew', padx=(4, 8), pady=(0, 12))
        mic_frame.columnconfigure(0, weight=1)

        device_names = ["System Default"] + [name for _, name in self._input_devices]
        current_name = "System Default"
        if self.new_mic_device_index is not None:
            for idx, name in self._input_devices:
                if idx == self.new_mic_device_index:
                    current_name = name
                    break

        self.mic_var = ctk.StringVar(value=current_name)
        ctk.CTkOptionMenu(
            mic_frame, values=device_names, variable=self.mic_var,
            font=Theme.FONT_BODY, dynamic_resizing=True,
        ).grid(row=0, column=0, padx=10, pady=14, sticky='ew')
        
        # HUD Settings — spans both columns
        hud_frame = ctk.CTkFrame(gen)
        hud_frame.grid(row=2, column=0, columnspan=2, sticky='ew', padx=(8, 8), pady=(8, 12))
        hud_frame.columnconfigure((0, 1), weight=1)

        self.hud_var = ctk.BooleanVar(value=self.new_hud_enabled)
        ctk.CTkCheckBox(
            hud_frame, text="Enable HUD Overlay",
            variable=self.hud_var, font=Theme.FONT_BODY
        ).grid(row=0, column=0, sticky='w', padx=10, pady=(10, 5))

        ctk.CTkButton(
            hud_frame, text="Re-Open HUD Overlay", font=Theme.FONT_BUTTON,
            fg_color=Theme.GREY, hover_color=Theme.HOVER_GREY, command=self.reopen_hud
        ).grid(row=1, column=0, sticky='w', padx=10, pady=(5, 10))
        
        opacity_frame = ctk.CTkFrame(hud_frame, fg_color="transparent")
        opacity_frame.grid(row=0, column=1, rowspan=2, sticky='e', padx=10, pady=(10, 10))
        
        ctk.CTkLabel(opacity_frame, text="HUD Opacity:", font=Theme.FONT_BODY).pack(side=tk.LEFT, padx=(0, 10))
        self.opacity_slider = ctk.CTkSlider(opacity_frame, from_=0.2, to=1.0, width=120)
        self.opacity_slider.set(self.new_hud_opacity)
        self.opacity_slider.pack(side=tk.LEFT)

        # ── OBS TAB ───────────────────────────────────────────────────────────
        obs = ctk.CTkScrollableFrame(tab_obs, fg_color="transparent")
        obs.grid(row=0, column=0, sticky='nsew')
        obs.columnconfigure((0, 1), weight=1)

        # Host + Port on same row
        ctk.CTkLabel(obs, text="Host", font=Theme.FONT_SUBTITLE, anchor='w').grid(
            row=0, column=0, sticky='w', padx=(8, 4), pady=(8, 2))
        ctk.CTkLabel(obs, text="Port", font=Theme.FONT_SUBTITLE, anchor='w').grid(
            row=0, column=1, sticky='w', padx=(4, 8), pady=(8, 2))

        self.obs_host_entry = ctk.CTkEntry(obs, font=Theme.FONT_BODY)
        self.obs_host_entry.insert(0, self.new_obs_settings.get('host', 'localhost'))
        self.obs_host_entry.grid(row=1, column=0, sticky='ew', padx=(8, 4), pady=(0, 10))

        self.obs_port_entry = ctk.CTkEntry(obs, font=Theme.FONT_BODY)
        self.obs_port_entry.insert(0, str(self.new_obs_settings.get('port', 4455)))
        self.obs_port_entry.grid(row=1, column=1, sticky='ew', padx=(4, 8), pady=(0, 10))

        # Password full width
        ctk.CTkLabel(obs, text="Password", font=Theme.FONT_SUBTITLE, anchor='w').grid(
            row=2, column=0, columnspan=2, sticky='w', padx=(8, 8), pady=(0, 2))
        self.obs_pass_entry = ctk.CTkEntry(obs, font=Theme.FONT_BODY, show='*')
        self.obs_pass_entry.insert(0, self.new_obs_settings.get('password', ''))
        self.obs_pass_entry.grid(row=3, column=0, columnspan=2, sticky='ew', padx=(8, 8), pady=(0, 10))

        # Auto-connect checkbox
        self.obs_auto_var = ctk.BooleanVar(value=self.new_obs_settings.get('auto_connect', False))
        ctk.CTkCheckBox(
            obs, text="Auto-connect on startup",
            variable=self.obs_auto_var, font=Theme.FONT_BODY
        ).grid(row=4, column=0, columnspan=2, sticky='w', padx=(8, 8), pady=(0, 10))

        # Test button + result label on same row
        self.obs_test_label = ctk.CTkLabel(obs, text="", font=Theme.FONT_BODY, anchor='w')
        self.obs_test_label.grid(row=5, column=1, sticky='ew', padx=(4, 8), pady=(0, 8))
        ctk.CTkButton(
            obs, text="Test Connection", font=Theme.FONT_BUTTON,
            command=self._test_obs_connection
        ).grid(row=5, column=0, sticky='ew', padx=(8, 4), pady=(0, 8))

        # ── KEYBINDS TAB ──────────────────────────────────────────────────────
        kb = ctk.CTkScrollableFrame(tab_keybinds, fg_color="transparent")
        kb.grid(row=0, column=0, sticky='nsew')
        kb.columnconfigure(0, weight=1)

        for action_id, label_text in self.parent.action_labels.items():
            frame = ctk.CTkFrame(kb)
            frame.pack(fill=tk.X, pady=3, padx=4)
            frame.columnconfigure(0, weight=1)

            ctk.CTkLabel(frame, text=f"{label_text}:", font=Theme.FONT_BODY, anchor='w').pack(
                side=tk.LEFT, padx=(12, 0), pady=6)

            key_str = self.new_keybinds.get(action_id, "N/A").upper()
            btn = ctk.CTkButton(
                frame, text=key_str, font=Theme.FONT_BODY, width=100,
                command=lambda aid=action_id: self.change_key(aid)
            )
            btn.pack(side=tk.RIGHT, padx=(12, 12), pady=6)
            self.bind_buttons[action_id] = btn

            if action_id.startswith("custom_note_"):
                entry = ctk.CTkEntry(frame, placeholder_text="Note text...", font=Theme.FONT_BODY)
                entry.insert(0, self.new_custom_texts.get(action_id, ""))
                entry.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(12, 0), pady=6)
                self.text_entries[action_id] = entry

        # ── Save / Cancel ─────────────────────────────────────────────────────
        btn_row = ctk.CTkFrame(root_frame, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky='ew', pady=(10, 0))
        btn_row.columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            btn_row, text="Save", command=self.save_and_close,
            fg_color=Theme.GREEN, hover_color=Theme.HOVER_GREEN, font=Theme.FONT_BUTTON
        ).grid(row=0, column=0, padx=(0, 4), sticky='ew')
        ctk.CTkButton(
            btn_row, text="Cancel", command=self.destroy,
            fg_color=Theme.RED, hover_color=Theme.HOVER_RED, font=Theme.FONT_BUTTON
        ).grid(row=0, column=1, padx=(4, 0), sticky='ew')

    def _browse_folder(self):
        chosen = filedialog.askdirectory(
            title="Choose Output Folder",
            initialdir=self.new_output_folder
        )
        if chosen:
            self.new_output_folder = chosen
            self.folder_label.configure(text=chosen)

    def reopen_hud(self):
        if self.parent.timestamp_manager.stopwatch_running:
            if self.parent.mini_widget is None or not self.parent.mini_widget.winfo_exists():
                self.parent.mini_widget = RecordingWidget(self.parent)
            else:
                self.parent.mini_widget.deiconify()
                self.parent.mini_widget.update_timer()

    def _test_obs_connection(self):
        self.obs_test_label.configure(text="Testing...", text_color=Theme.GREY)
        self.update_idletasks()
        host = self.obs_host_entry.get().strip()
        port = self.obs_port_entry.get().strip()
        password = self.obs_pass_entry.get()
        ok, msg = self.parent.obs_manager.test_connection(host, port, password)
        if ok:
            self.obs_test_label.configure(text=f"✅ {msg}", text_color=Theme.GREEN)
        else:
            self.obs_test_label.configure(text="❌ Failed", text_color=Theme.RED)

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

        # Resolve selected mic name back to a device index
        chosen_name = self.mic_var.get()
        if chosen_name == "System Default":
            self.new_mic_device_index = None
        else:
            for idx, name in self._input_devices:
                if name == chosen_name:
                    self.new_mic_device_index = idx
                    break

        # Gather OBS settings
        self.new_obs_settings = {
            'host': self.obs_host_entry.get().strip(),
            'port': int(self.obs_port_entry.get().strip() or 4455),
            'password': self.obs_pass_entry.get(),
            'auto_connect': self.obs_auto_var.get(),
        }

        self.parent.keybinds = self.new_keybinds
        self.parent.custom_texts = self.new_custom_texts
        self.parent.output_folder = self.new_output_folder
        self.parent.mic_device_index = self.new_mic_device_index
        self.parent.obs_settings = self.new_obs_settings
        self.parent.hud_enabled = self.hud_var.get()
        self.parent.hud_opacity = self.opacity_slider.get()
        self.parent.timestamp_manager.set_output_dir(self.new_output_folder)
        self.parent.timestamp_manager.set_mic_device(self.new_mic_device_index)
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
        self.output_folder = os.path.join(get_base_path(), "Timestamp_TXT")  # default
        self.mic_device_index = None  # None = system default
        self.hud_enabled = True
        self.hud_opacity = 0.8
        self.obs_settings = {
            'host': 'localhost', 'port': 4455, 'password': '', 'auto_connect': False
        }
        self.obs_manager = OBSManager(self.timestamp_manager)
        
        self.action_labels = {
            'create_file': "Create / Open File", 'start_recording': "Start Recording",
            'mark_time': "Mark Time", 'stop_recording': "Stop Recording",
            'save_short': "Save Short", 'mark_voice_note': "Voice Note",
            'take_screenshot': "Take Screenshot", 'mark_ptt_voice_note': "PTT Voice Note",
            'custom_note_1': "Custom Note 1", 'custom_note_2': "Custom Note 2",
            'custom_note_3': "Custom Note 3", 'custom_note_4': "Custom Note 4",
            'custom_note_5': "Custom Note 5",
        }
        self.default_keybinds = {
            'create_file': 'f13', 'start_recording': 'f14', 'mark_time': 'f15',
            'stop_recording': 'f16', 'save_short': 'f18', 'mark_voice_note': 'f17',
            'take_screenshot': 'f19', 'mark_ptt_voice_note': '',
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
        self._setup_obs()

        self.auto_save()
        self._start_keyboard_listener()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _create_widgets(self):
        self._create_header()
        self._create_text_viewer()
        self._create_filename_display()
        self._create_obs_status_bar()
        self._create_buttons()
        
    def load_keybinds(self):
        try:
            with open(self.keybinds_file, 'r') as f:
                data = json.load(f)
                
            # Handle legacy format where it was just the keybinds dictionary directly
            if 'keybinds' in data:
                self.keybinds = data.get('keybinds', {})
                self.custom_texts = data.get('custom_texts', {})
                # Load saved output folder, fall back to default
                saved_folder = data.get('output_folder', '')
                if saved_folder and os.path.isdir(saved_folder):
                    self.output_folder = saved_folder
                # Load saved mic device index
                saved_mic = data.get('mic_device_index', None)
                if saved_mic is not None:
                    self.mic_device_index = int(saved_mic)
                # Load saved obs settings
                saved_obs = data.get('obs_settings', {})
                if saved_obs:
                    self.obs_settings.update(saved_obs)
                self.hud_enabled = data.get('hud_enabled', True)
                self.hud_opacity = data.get('hud_opacity', 0.8)
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
        
        # Apply the (possibly loaded) output folder and mic device to the manager
        self.timestamp_manager.set_output_dir(self.output_folder)
        self.timestamp_manager.set_mic_device(self.mic_device_index)
        self.save_keybinds()

    def save_keybinds(self):
        with open(self.keybinds_file, 'w') as f:
            data = {
                'keybinds': self.keybinds,
                'custom_texts': self.custom_texts,
                'output_folder': self.output_folder,
                'mic_device_index': self.mic_device_index,
                'obs_settings': self.obs_settings,
                'hud_enabled': self.hud_enabled,
                'hud_opacity': self.hud_opacity,
            }
            json.dump(data, f, indent=4)

    def on_closing(self):
        self.save_changes()
        self.save_keybinds()
        self.obs_manager.disconnect()
        print("Final autosave and keybinds saved before closing")
        self.root.destroy()

    def auto_save(self):
        self.save_changes()
        self.root.after(60000, self.auto_save)

    def _create_header(self):
        header_container = ctk.CTkFrame(self.root)
        header_container.grid(row=0, column=0, sticky='ew', padx=10, pady=(10, 0))
        
        title_label = ctk.CTkLabel(header_container, text="Nilvarcus Timestamp App", font=Theme.FONT_TITLE)
        title_label.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=15)
        
        self.voice_status_label = ctk.CTkLabel(
            header_container, text="", font=Theme.FONT_SUBTITLE, text_color=Theme.RED
        )
        self.voice_status_label.pack(side=tk.RIGHT, padx=20)

    def _create_text_viewer(self):
        text_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        text_frame.grid(row=1, column=0, padx=10, pady=10, sticky='nsew')
        
        self.text_viewer = ctk.CTkTextbox(text_frame, wrap=tk.WORD, font=Theme.FONT_TEXT_AREA)
        # Using pack so it expands naturally
        self.text_viewer.pack(expand=True, fill=tk.BOTH)

    def _create_filename_display(self):
        filename_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        filename_frame.grid(row=2, column=0, padx=10, pady=(0, 2), sticky='ew')

        label = ctk.CTkLabel(filename_frame, text="Current File:", font=Theme.FONT_SUBTITLE)
        label.pack(side=tk.LEFT, padx=(5, 10))
        
        self.filename_label = ctk.CTkLabel(filename_frame, text="No file open", font=Theme.FONT_BODY, anchor="w")
        self.filename_label.pack(side=tk.LEFT, expand=True, fill=tk.X)

    def _create_obs_status_bar(self):
        obs_bar = ctk.CTkFrame(self.root, fg_color="transparent")
        obs_bar.grid(row=3, column=0, padx=10, pady=(0, 4), sticky='ew')

        self.obs_status_label = ctk.CTkLabel(
            obs_bar, text="🔴  OBS: Not Connected",
            font=Theme.FONT_BODY, text_color=Theme.RED, anchor='w'
        )
        self.obs_status_label.pack(side=tk.LEFT, padx=(5, 0))

        self.obs_connect_btn = ctk.CTkButton(
            obs_bar, text="Connect", width=90,
            font=Theme.FONT_BUTTON, fg_color=Theme.GREY, hover_color=Theme.HOVER_GREY,
            command=self._toggle_obs_connection
        )
        self.obs_connect_btn.pack(side=tk.RIGHT)

    def _update_filename_display(self):
        if self.timestamp_manager.current_file_path:
            self.filename_label.configure(text=os.path.basename(self.timestamp_manager.current_file_path))
        else:
            self.filename_label.configure(text="No file open")

    def _create_buttons(self):
        button_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        button_frame.grid(row=4, column=0, padx=10, pady=(0, 10), sticky='ew')
        button_frame.columnconfigure((0, 1), weight=1)

        button_config = {
            'create_file': (self.create_file, Theme.BLUE, Theme.HOVER_BLUE, 0, 0),
            'start_recording': (self.start_recording, Theme.GREEN, Theme.HOVER_GREEN, 0, 1),
            'mark_time': (self.mark_time, Theme.ORANGE, Theme.HOVER_ORANGE, 1, 0),
            'stop_recording': (self.stop_recording, Theme.RED, Theme.HOVER_RED, 1, 1),
            'save_short': (self.save_short, Theme.TURQUOISE, Theme.HOVER_TURQUOISE, 2, 0),
            'mark_voice_note': (self.mark_voice_note, Theme.PURPLE, Theme.HOVER_PURPLE, 2, 1),
            'take_screenshot': (self.take_screenshot, Theme.TURQUOISE, Theme.HOVER_TURQUOISE, 3, 0),
        }

        for action_id, (command, bg, hover, row, col) in button_config.items():
            btn = ctk.CTkButton(button_frame, command=command, fg_color=bg, hover_color=hover, font=Theme.FONT_BUTTON)
            if action_id == 'take_screenshot':
                btn.grid(row=row, column=0, columnspan=2, padx=4, pady=4, sticky='ew')
            else:
                btn.grid(row=row, column=col, padx=4, pady=4, sticky='ew')
            self.buttons[action_id] = btn

        settings_btn = ctk.CTkButton(button_frame, text="Settings", command=self.open_settings_window, fg_color=Theme.GREY, hover_color=Theme.HOVER_GREY, font=Theme.FONT_BUTTON)
        settings_btn.grid(row=4, column=0, columnspan=2, padx=4, pady=4, sticky='ew')

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
            'mark_ptt_voice_note': self.start_ptt_voice_note,
            'custom_note_1': lambda: self.mark_custom_note_n('custom_note_1'),
            'custom_note_2': lambda: self.mark_custom_note_n('custom_note_2'),
            'custom_note_3': lambda: self.mark_custom_note_n('custom_note_3'),
            'custom_note_4': lambda: self.mark_custom_note_n('custom_note_4'),
            'custom_note_5': lambda: self.mark_custom_note_n('custom_note_5'),
        }
        self.pressed_keys = set()
        Thread(target=lambda: keyboard.Listener(on_press=self._on_press, on_release=self._on_release).start(), daemon=True).start()

    def _on_press(self, key):
        key_str = self.get_key_str(key)
        if key_str in self.pressed_keys:
            return  # Prevent auto-repeat triggers
        self.pressed_keys.add(key_str)
        
        # Only map non-empty keybinds
        key_to_action = {v: k for k, v in self.keybinds.items() if v}
        action_id = key_to_action.get(key_str)
        if action_id in self.action_map:
            try:
                self.root.after(0, self.action_map[action_id])
            except Exception as e:
                print(f"Error executing action '{action_id}': {e}")
                
    def _on_release(self, key):
        key_str = self.get_key_str(key)
        if key_str in self.pressed_keys:
            self.pressed_keys.remove(key_str)
            
        key_to_action = {v: k for k, v in self.keybinds.items() if v}
        action_id = key_to_action.get(key_str)
        
        # Handle features that require an explicit release trigger
        if action_id == 'mark_ptt_voice_note':
            try:
                self.root.after(0, self.stop_ptt_voice_note)
            except Exception as e:
                print(f"Error executing release action '{action_id}': {e}")

    def create_file(self):
        file_path = self.timestamp_manager.create_file()
        if file_path: self.update_text_viewer(); self._update_filename_display()

    def start_recording(self, from_obs=False):
        self.save_changes()
        if self.timestamp_manager.start_recording():
            self.update_text_viewer()
            if self.hud_enabled:
                if self.mini_widget is None or not self.mini_widget.winfo_exists():
                    self.mini_widget = RecordingWidget(self)
                else:
                    self.mini_widget.deiconify()
                    self.mini_widget.update_timer()
            
            if not from_obs:
                self.obs_manager.start_obs_recording()

    def mark_time(self):
        self.save_changes()
        if self.timestamp_manager.mark_time():
            self.update_text_viewer()
            if self.mini_widget and self.mini_widget.winfo_exists():
                self.mini_widget.show_status("Timestamp Marked!", color=Theme.BLUE)

    def stop_recording(self, from_obs=False):
        self.save_changes()
        if self.timestamp_manager.stop_recording():
            self.update_text_viewer()
            if self.mini_widget and self.mini_widget.winfo_exists():
                self.mini_widget.destroy()
                self.mini_widget = None
                
            if not from_obs:
                self.obs_manager.stop_obs_recording()

    def save_short(self):
        """Save Short marker — also triggers OBS replay buffer save if connected."""
        self.save_changes()
        
        is_error = False
        if self.obs_manager.is_connected:
            success = self.obs_manager.save_replay_buffer()
            if not success:
                is_error = True
                
        if self.timestamp_manager.save_short(error=is_error):
            self.update_text_viewer()
            if self.mini_widget and self.mini_widget.winfo_exists():
                if is_error:
                    self.mini_widget.show_status("Replay Error!", color=Theme.RED)
                else:
                    self.mini_widget.show_status("Short Saved!", color=Theme.TURQUOISE)

    def mark_voice_note(self):
        self.save_changes()
        self.mark_time()
        if self.timestamp_manager.mark_voice_note():
            pass

    def take_screenshot(self):
        self.save_changes()
        if self.timestamp_manager.take_screenshot():
            self.update_text_viewer()
            if self.mini_widget and self.mini_widget.winfo_exists():
                self.mini_widget.show_status("Screenshot Saved!", color=Theme.TURQUOISE)

    def start_ptt_voice_note(self):
        self.save_changes()
        self.mark_time()
        if self.timestamp_manager.start_ptt_voice_note():
            pass
            
    def stop_ptt_voice_note(self):
        if self.timestamp_manager.stop_ptt_voice_note():
            pass

    def _setup_obs(self):
        """Register OBS callbacks and auto-connect if configured."""
        self.obs_manager.register_callbacks(
            on_status_change=self._on_obs_status_change,
            on_scene_change=self._on_obs_scene_change,
            on_replay_saved=self._on_obs_replay_saved,
            on_recording_started=self._on_obs_recording_started,
            on_recording_stopped=self._on_obs_recording_stopped,
        )
        if self.obs_settings.get('auto_connect'):
            s = self.obs_settings
            self.obs_manager.connect(s['host'], s['port'], s['password'])

    def _toggle_obs_connection(self):
        if self.obs_manager.is_connected:
            self.obs_manager.disconnect()
        else:
            s = self.obs_settings
            self.obs_manager.connect(s['host'], s['port'], s['password'])

    # ── OBS Callbacks (called from background thread → routed via root.after) ──

    def _on_obs_status_change(self, status: str):
        def update():
            if status == "connected":
                self.obs_status_label.configure(text="🟢  OBS: Connected", text_color=Theme.GREEN)
                self.obs_connect_btn.configure(text="Disconnect")
            elif status == "connecting":
                self.obs_status_label.configure(text="🟡  OBS: Connecting...", text_color=Theme.ORANGE)
                self.obs_connect_btn.configure(text="Cancel")
            elif status == "disconnected":
                self.obs_status_label.configure(text="🔴  OBS: Not Connected", text_color=Theme.RED)
                self.obs_connect_btn.configure(text="Connect")
            elif status.startswith("error:"):
                self.obs_status_label.configure(text="❌  OBS: Error", text_color=Theme.RED)
                self.obs_connect_btn.configure(text="Connect")
        self.root.after(0, update)

    def _on_obs_recording_started(self):
        """Called from OBS background thread — route to main thread via root.after."""
        self.root.after(0, lambda: self.start_recording(from_obs=True))

    def _on_obs_recording_stopped(self):
        """Called from OBS background thread — route to main thread via root.after."""
        self.root.after(0, lambda: self.stop_recording(from_obs=True))

    def _on_obs_scene_change(self, scene_name: str):
        def update():
            self.update_text_viewer()
            if self.mini_widget and self.mini_widget.winfo_exists():
                self.mini_widget.show_status(f"📺 {scene_name}", color=Theme.BLUE)
        self.root.after(0, update)

    def _on_obs_replay_saved(self):
        def update():
            self.update_text_viewer()
            if self.mini_widget and self.mini_widget.winfo_exists():
                self.mini_widget.show_status("💾 Replay Saved!", color=Theme.TURQUOISE)
        self.root.after(0, update)

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
                self.voice_status_label.configure(text="")
            else:
                if 'mark_voice_note' in self.buttons:
                    self.buttons['mark_voice_note'].configure(text=status)
                
                if self.mini_widget and self.mini_widget.winfo_exists():
                    self.mini_widget.show_status(status, duration=10000, color=Theme.PURPLE)
                    
                # Update main GUI voice status header
                if "error" in status.lower() or "no audio" in status.lower():
                    self.voice_status_label.configure(text=status, text_color=Theme.RED)
                elif "transcribing" in status.lower():
                    self.voice_status_label.configure(text="⏳ Transcribing...", text_color=Theme.ORANGE)
                elif "recording" in status.lower():
                    self.voice_status_label.configure(text="🎙️ RECORDING...", text_color=Theme.RED)
                else:
                    self.voice_status_label.configure(text=status, text_color=Theme.RED)
                    
        self.root.after(0, update_gui)
            
    def save_changes(self):
        if self.timestamp_manager.current_file_path:
            self.timestamp_manager.save_changes(self.text_viewer.get("1.0", tk.END))

    def update_text_viewer(self):
        text_content = self.timestamp_manager.read_file_content()
        self.text_viewer.delete("1.0", tk.END)
        self.text_viewer.insert(tk.END, text_content)
        self.text_viewer.see(tk.END)
        if hasattr(self, 'mini_widget') and self.mini_widget and self.mini_widget.winfo_exists():
            self.mini_widget.refresh_logs()

def main():
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    app = TimestampApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
