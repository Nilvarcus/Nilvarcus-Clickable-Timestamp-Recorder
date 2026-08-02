import ctypes
import subprocess
import tkinter as tk
from tkinter import messagebox, filedialog
from pynput import keyboard, mouse
from threading import Thread
import json
import os
import sys
import customtkinter as ctk
from PIL import Image, ImageTk

# Import the TimestampManager, OBSManager, and GeminiAnalyzer from local modules
from timestamp_functions import TimestampManager
from timestamp_obs import OBSManager
from timestamp_resolve import open_resolve_export_dialog
from timestamp_gemini import GeminiAnalyzer

def is_admin() -> bool:
    """Check if the process is running with administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def get_base_path() -> str:
    """Gets the base path for the application, whether running as a script or a frozen exe."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

class Theme:
    """A centralized class for managing the application's visual theme.

    Crimson & Black palette inspired by Google Material Design 3 principles:
    - Deep black surfaces with subtle elevation
    - Crimson (#DC143C) as the primary accent
    - Warm amber for secondary actions
    - Generous spacing, rounded corners, clear typographic hierarchy
    """
    # ── Surface colors (black tones with elevation) ──
    BG_DARKEST = '#0A0A0A'      # Window background
    BG_SURFACE = '#141414'      # Cards / elevated surfaces
    BG_SURFACE_HOVER = '#1E1E1E'
    BG_ENTRY = '#1A1A1A'        # Input fields

    # ── Primary: Crimson ──
    CRIMSON = '#DC143C'
    CRIMSON_HOVER = '#B8112F'
    CRIMSON_DARK = '#8E0D24'
    CRIMSON_GLOW = '#FF1744'

    # ── Secondary accents ──
    AMBER = '#FFAB00'
    AMBER_HOVER = '#FF8F00'
    GREEN = '#00E676'
    GREEN_HOVER = '#00C853'
    BLUE = '#448AFF'
    BLUE_HOVER = '#2962FF'

    # ── Neutral button surfaces ──
    BTN_SURFACE = '#1C1C1E'
    BTN_SURFACE_HOVER = '#2C2C2E'
    BTN_TEXT = '#CCCCCC'
    DIVIDER = '#2A2A2A'

    # ── Semantic colors ──
    RED = '#FF5252'
    RED_HOVER = '#D32F2F'
    GREY = '#616161'
    GREY_HOVER = '#424242'
    TEXT_DIM = '#AAAAAA'
    TEXT_BRIGHT = '#FFFFFF'

    # ── Typography (Segoe UI for clean, modern look) ──
    FONT_FAMILY = "Segoe UI"
    FONT_TITLE = (FONT_FAMILY, 22, "bold")
    FONT_SUBTITLE = (FONT_FAMILY, 13, "bold")
    FONT_BODY = (FONT_FAMILY, 12)
    FONT_SMALL = (FONT_FAMILY, 10)
    FONT_BUTTON = (FONT_FAMILY, 12, "bold")
    FONT_TEXT_AREA = ("Consolas", 12)
    FONT_MONO = ("Consolas", 11)

class RecordingWidget(ctk.CTkToplevel):
    """A floating HUD widget to show recording time, status, and recent logs."""
    def __init__(self, parent):
        super().__init__(parent.root)
        self.parent = parent
        self.title("Recording HUD")
        self.attributes('-topmost', True) # Keep window on top
        self.attributes('-alpha', parent.hud_opacity)
        self.overrideredirect(True)
        
        self.geometry("440x76")
        self.minsize(340, 76)
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
        self.main_frame = ctk.CTkFrame(self, fg_color=Theme.BG_SURFACE, corner_radius=12, border_width=3, border_color=Theme.CRIMSON)
        self.main_frame.pack(expand=True, fill=tk.BOTH)
        
        content_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        content_frame.pack(expand=True, fill=tk.BOTH, padx=18, pady=6)
        
        self.time_label = ctk.CTkLabel(content_frame, text="00:00:00", font=(Theme.FONT_FAMILY, 34, "bold"), text_color=Theme.TEXT_BRIGHT)
        self.time_label.pack(side=tk.LEFT, padx=(0, 22))

        self.status_label = ctk.CTkLabel(content_frame, text="", font=(Theme.FONT_FAMILY, 15, "bold"), text_color=Theme.CRIMSON_GLOW)
        self.status_label.pack(side=tk.RIGHT)

        # Allow dragging the frameless window
        for widget in (self, self.main_frame, content_frame, self.time_label, self.status_label):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

    def start_move(self, event):
        self._drag_start_x = event.x_root - self.winfo_x()
        self._drag_start_y = event.y_root - self.winfo_y()

    def do_move(self, event):
        x = event.x_root - self._drag_start_x
        y = event.y_root - self._drag_start_y
        self.geometry(f"+{x}+{y}")

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
            "recording": (Theme.CRIMSON_GLOW, Theme.CRIMSON_DARK),
            "error": ("#FF0000", "#330000"),
            "success": (Theme.GREEN, "#003311"),
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

    def show_status(self, message, duration=3000, color=Theme.GREEN, font=None):
        if font is None:
            font = (Theme.FONT_FAMILY, 15, "bold")

        self.status_label.configure(text=message, text_color=color, font=font)
        
        # Update border state temporally
        if color == Theme.RED or color == Theme.CRIMSON: self.set_border_state("error")
        else: self.set_border_state("success")
            
        if hasattr(self, '_hide_status_job') and self._hide_status_job:
            self.after_cancel(self._hide_status_job)
            
        def reset_status():
            self.status_label.configure(text="", font=(Theme.FONT_FAMILY, 15, "bold"))
            self.set_border_state("recording")
            
        self._hide_status_job = self.after(duration, reset_status)
        
    def destroy(self):
        if hasattr(self, '_anim_job'): self.after_cancel(self._anim_job)
        if hasattr(self, '_hide_status_job') and self._hide_status_job: self.after_cancel(self._hide_status_job)
        super().destroy()

    def hide_widget(self):
        self.withdraw()

class RichTextLog(ctk.CTkTextbox):
    """A text viewer that embeds screenshots inline while preserving editable text.

    Uses tk.Text.image_create() to insert images at the end of screenshot wikilinks.
    Clean text extraction via _get_text_only() strips image objects so the file
    stays clean markdown. Users can still type notes freely between images.
    """
    # Thumbnail width for inline display at 100% scale (pixels)
    BASE_THUMB_WIDTH = 400

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        # Keep references to PhotoImage objects so they aren't garbage collected
        self._image_refs = []
        # Reference to TimestampManager — set by the app after creation
        self.timestamp_manager = None
        # Current scale factor (1.0 = 100%)
        self._scale = 1.0
        # Current thumbnail width (scaled)
        self.THUMB_WIDTH = self.BASE_THUMB_WIDTH
        # Tag for AI description lines
        self._inner().tag_configure("ai_desc", foreground=Theme.AMBER, font=(Theme.FONT_FAMILY, 11, "italic"))
        # Tag for screenshot thumbnails
        self._inner().tag_configure("screenshot_img", justify="center")

    def set_scale(self, scale: float):
        """Adjust font size and thumbnail width by the given scale factor.

        Args:
            scale: Multiplier, e.g. 1.0 = 100%, 0.8 = 80%, 1.2 = 120%.
        """
        self._scale = scale
        # Update thumbnail size
        self.THUMB_WIDTH = int(self.BASE_THUMB_WIDTH * scale)
        # Update main text font (keep the original monospace family)
        new_size = max(6, int(12 * scale))
        self.configure(font=("Consolas", new_size))
        # Update AI description tag font
        ai_size = max(6, int(11 * scale))
        self._inner().tag_configure("ai_desc", font=(Theme.FONT_FAMILY, ai_size, "italic"))

    def _inner(self):
        """Return the underlying tk.Text widget for direct API access."""
        return self._textbox

    def clear(self):
        self._image_refs.clear()
        self.delete("1.0", tk.END)

    def insert_text(self, text):
        """Insert plain text, then scan for screenshot wikilinks and embed images."""
        self.delete("1.0", tk.END)
        self._image_refs.clear()

        import re
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if i > 0:
                self.insert(tk.END, "\n")

            # Check if this line contains a screenshot wikilink: ![[filename]]
            match = re.search(r'(.*)!\[\[(.+?)\]\](.*)', line)
            if match:
                before = match.group(1)
                filename = match.group(2)
                after = match.group(3)

                # Insert text before the image
                if before:
                    self.insert(tk.END, before)

                # Insert the wikilink text
                self.insert(tk.END, f"![[{filename}]]")

                # Insert a marked newline + thumbnail image on the next line.
                # The zero-width space (\u200B) marks this as a UI-only spacer
                # so get_text_only() can strip it without affecting real content.
                self._inner().insert(tk.END, "\n\u200B")
                self._embed_image(filename)

                # Insert any text after the wikilink
                if after:
                    self.insert(tk.END, after)
            else:
                # Check for AI description lines: '    - 🤖 ...' (new) or '  > 🤖 ...' (legacy)
                stripped = line.strip()
                if stripped.startswith("- 🤖") or stripped.startswith("> 🤖"):
                    self.insert(tk.END, line, "ai_desc")
                else:
                    self.insert(tk.END, line)

        self.see(tk.END)

    def _embed_image(self, filename):
        """Load a screenshot file and embed it as a thumbnail."""
        try:
            tm = self.timestamp_manager
            if not tm or not tm.current_file_path:
                return

            file_basename = os.path.basename(tm.current_file_path)
            folder_name = os.path.splitext(file_basename)[0]
            screenshots_dir = os.path.join(tm.output_dir, "Screenshots", folder_name)
            filepath = os.path.join(screenshots_dir, filename)

            if not os.path.exists(filepath):
                self.insert(tk.END, f"  [image not found: {filename}]")
                return

            # Load and resize for thumbnail display
            pil_img = Image.open(filepath)
            ratio = self.THUMB_WIDTH / pil_img.width
            thumb_height = int(pil_img.height * ratio)
            pil_img = pil_img.resize((self.THUMB_WIDTH, thumb_height), Image.Resampling.LANCZOS)

            photo = ImageTk.PhotoImage(pil_img)
            self._image_refs.append(photo)  # prevent GC

            self._inner().image_create(tk.END, image=photo, padx=20, pady=4)
        except Exception as e:
            print(f"[RichTextLog] Image embed error: {e}")

    def get_text_only(self):
        """Extract text content, stripping all embedded image objects.

        Uses dump() to iterate text segments and skip 'image' entries,
        preserving all text and newlines exactly as written.
        """
        result = []
        for entry in self._inner().dump("1.0", tk.END):
            kind = entry[0]
            value = entry[1]
            if kind == "text":
                result.append(value)
            # All other entry types (image, window, mark, tagon, tagoff)
            # are automatically skipped — only text segments are kept
        text = "".join(result)
        # Remove the trailing newline that Tkinter always appends
        if text.endswith("\n"):
            text = text[:-1]
        # Strip UI-only image-spacer markers (\n + zero-width space).
        # insert_text() uses "\n\u200B" before embedded screenshots for
        # visual spacing; stripping the image leaves this marker behind.
        text = text.replace("\n\u200B", "")
        return text


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
        self.new_obs_settings = parent.obs_settings.copy()
        self.new_hud_enabled = parent.hud_enabled
        self.new_hud_opacity = parent.hud_opacity
        self.new_screenshot_resolution = parent.screenshot_resolution
        self.new_gemini_api_key = parent.gemini_api_key
        self.new_viewer_scale = parent.viewer_scale
        self.bind_buttons = {}
        self.text_entries = {}

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
        tab_ai       = tabs.add("AI")
        tab_keybinds = tabs.add("Keybinds")

        for t in (tab_general, tab_obs, tab_ai, tab_keybinds):
            t.grid_rowconfigure(0, weight=1)
            t.grid_columnconfigure(0, weight=1)

        # ── GENERAL TAB ───────────────────────────────────────────────────────
        gen = ctk.CTkScrollableFrame(tab_general, fg_color="transparent")
        gen.grid(row=0, column=0, sticky='nsew')
        gen.columnconfigure((0, 1), weight=1)

        # Output Folder — left column
        ctk.CTkLabel(gen, text="Output Folder", font=Theme.FONT_SUBTITLE, anchor='w').grid(
            row=0, column=0, sticky='w', padx=(8, 4), pady=(8, 2))

        folder_frame = ctk.CTkFrame(gen, fg_color=Theme.BG_SURFACE, corner_radius=10)
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

        # Screenshot Resolution — right column
        ctk.CTkLabel(gen, text="Screenshot Size", font=Theme.FONT_SUBTITLE, anchor='w').grid(
            row=0, column=1, sticky='w', padx=(4, 8), pady=(8, 2))

        res_frame = ctk.CTkFrame(gen, fg_color=Theme.BG_SURFACE, corner_radius=10)
        res_frame.grid(row=1, column=1, sticky='nsew', padx=(4, 8), pady=(0, 12))
        res_frame.columnconfigure(0, weight=1)
        
        self.res_var = ctk.StringVar(value=self.new_screenshot_resolution)
        ctk.CTkOptionMenu(
            res_frame, values=["720p", "1080p", "Original"], variable=self.res_var,
            font=Theme.FONT_BODY, dynamic_resizing=True,
        ).grid(row=0, column=0, padx=10, pady=14, sticky='ew')
        
        # HUD Settings — spans both columns
        hud_frame = ctk.CTkFrame(gen, fg_color=Theme.BG_SURFACE, corner_radius=10)
        hud_frame.grid(row=2, column=0, columnspan=2, sticky='ew', padx=(8, 8), pady=(8, 12))
        hud_frame.columnconfigure((0, 1), weight=1)

        self.hud_var = ctk.BooleanVar(value=self.new_hud_enabled)
        ctk.CTkCheckBox(
            hud_frame, text="Enable HUD Overlay",
            variable=self.hud_var, font=Theme.FONT_BODY
        ).grid(row=0, column=0, sticky='w', padx=10, pady=(10, 5))

        ctk.CTkButton(
            hud_frame, text="Re-Open HUD Overlay", font=Theme.FONT_BUTTON,
            fg_color=Theme.GREY, hover_color=Theme.GREY_HOVER, command=self.reopen_hud
        ).grid(row=1, column=0, sticky='w', padx=10, pady=(5, 10))
        
        opacity_frame = ctk.CTkFrame(hud_frame, fg_color="transparent")
        opacity_frame.grid(row=0, column=1, rowspan=2, sticky='e', padx=10, pady=(10, 10))
        
        ctk.CTkLabel(opacity_frame, text="HUD Opacity:", font=Theme.FONT_BODY).pack(side=tk.LEFT, padx=(0, 10))
        self.opacity_slider = ctk.CTkSlider(opacity_frame, from_=0.2, to=1.0, width=120)
        self.opacity_slider.set(self.new_hud_opacity)
        self.opacity_slider.pack(side=tk.LEFT)

        # Viewer Scale — spans both columns
        scale_frame = ctk.CTkFrame(gen, fg_color=Theme.BG_SURFACE, corner_radius=10)
        scale_frame.grid(row=3, column=0, columnspan=2, sticky='ew', padx=(8, 8), pady=(8, 12))
        scale_frame.columnconfigure(0, weight=1)

        scale_header = ctk.CTkFrame(scale_frame, fg_color="transparent")
        scale_header.grid(row=0, column=0, sticky='ew', padx=10, pady=(10, 0))

        ctk.CTkLabel(
            scale_header, text="Viewer Scale", font=Theme.FONT_SUBTITLE, anchor='w'
        ).pack(side=tk.LEFT)

        self.scale_pct_label = ctk.CTkLabel(
            scale_header, text=f"{int(self.new_viewer_scale * 100)}%",
            font=Theme.FONT_BODY, text_color=Theme.CRIMSON, anchor='e'
        )
        self.scale_pct_label.pack(side=tk.RIGHT)

        self.scale_slider = ctk.CTkSlider(
            scale_frame, from_=0.6, to=1.4, number_of_steps=16,
            command=self._on_scale_slider,
        )
        self.scale_slider.set(self.new_viewer_scale)
        self.scale_slider.grid(row=1, column=0, sticky='ew', padx=10, pady=(4, 10))



        # ── AI TAB ────────────────────────────────────────────────────────────
        ai = ctk.CTkScrollableFrame(tab_ai, fg_color="transparent")
        ai.grid(row=0, column=0, sticky='nsew')
        ai.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            ai, text="Google Gemini AI",
            font=Theme.FONT_TITLE, anchor='w', text_color=Theme.CRIMSON
        ).grid(row=0, column=0, sticky='w', padx=(8, 8), pady=(8, 2))

        ctk.CTkLabel(
            ai, text="Analyze screenshots with Gemini 3.5 Flash-Lite.\nGet your free API key at aistudio.google.com/apikey",
            font=Theme.FONT_SMALL, anchor='w', text_color=Theme.TEXT_DIM
        ).grid(row=1, column=0, sticky='w', padx=(8, 8), pady=(0, 12))

        # API Key card
        ai_card = ctk.CTkFrame(ai, fg_color=Theme.BG_SURFACE, corner_radius=10)
        ai_card.grid(row=2, column=0, sticky='ew', padx=(8, 8), pady=(0, 8))
        ai_card.columnconfigure(0, weight=1)

        ctk.CTkLabel(
            ai_card, text="API Key", font=Theme.FONT_SUBTITLE, anchor='w'
        ).grid(row=0, column=0, sticky='w', padx=(12, 12), pady=(12, 2))

        self.gemini_key_entry = ctk.CTkEntry(ai_card, font=Theme.FONT_BODY, show='•', placeholder_text="AIzaSy...")
        self.gemini_key_entry.insert(0, self.new_gemini_api_key)
        self.gemini_key_entry.grid(row=1, column=0, sticky='ew', padx=(12, 12), pady=(0, 8))

        # Test button + result label
        test_row = ctk.CTkFrame(ai_card, fg_color="transparent")
        test_row.grid(row=2, column=0, sticky='ew', padx=(12, 12), pady=(4, 12))
        test_row.columnconfigure(1, weight=1)

        ctk.CTkButton(
            test_row, text="Test API Key", font=Theme.FONT_BUTTON,
            fg_color=Theme.CRIMSON, hover_color=Theme.CRIMSON_HOVER,
            command=self._test_gemini_key
        ).grid(row=0, column=0, padx=(0, 8), sticky='w')

        self.gemini_test_label = ctk.CTkLabel(test_row, text="", font=Theme.FONT_BODY, anchor='w')
        self.gemini_test_label.grid(row=0, column=1, sticky='ew')

        ctk.CTkLabel(
            ai,
            text="\nℹ️  Press the 'Analyze Screenshots' button (or its hotkey) during\n"
                 "a recording session to send all screenshots to Gemini and\n"
                 "auto-insert AI descriptions into your timestamp log.",
            font=Theme.FONT_SMALL, anchor='w', text_color=Theme.TEXT_DIM,
            justify=tk.LEFT
        ).grid(row=5, column=0, sticky='w', padx=(8, 8), pady=(10, 8))

        # ── OBS TAB ───────────────────────────────────────────────────────────
        obs = ctk.CTkScrollableFrame(tab_obs, fg_color="transparent")
        obs.grid(row=0, column=0, sticky='nsew')
        obs.columnconfigure((0, 1), weight=1)

        # Connection card
        obs_card = ctk.CTkFrame(obs, fg_color=Theme.BG_SURFACE, corner_radius=10)
        obs_card.grid(row=0, column=0, columnspan=2, sticky='ew', padx=(8, 8), pady=(8, 8))
        obs_card.columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(obs_card, text="Host", font=Theme.FONT_SUBTITLE, anchor='w').grid(
            row=0, column=0, sticky='w', padx=(12, 4), pady=(12, 2))
        ctk.CTkLabel(obs_card, text="Port", font=Theme.FONT_SUBTITLE, anchor='w').grid(
            row=0, column=1, sticky='w', padx=(4, 12), pady=(12, 2))

        self.obs_host_entry = ctk.CTkEntry(obs_card, font=Theme.FONT_BODY)
        self.obs_host_entry.insert(0, self.new_obs_settings.get('host', 'localhost'))
        self.obs_host_entry.grid(row=1, column=0, sticky='ew', padx=(12, 4), pady=(0, 8))

        self.obs_port_entry = ctk.CTkEntry(obs_card, font=Theme.FONT_BODY)
        self.obs_port_entry.insert(0, str(self.new_obs_settings.get('port', 4455)))
        self.obs_port_entry.grid(row=1, column=1, sticky='ew', padx=(4, 12), pady=(0, 8))

        ctk.CTkLabel(obs_card, text="Password", font=Theme.FONT_SUBTITLE, anchor='w').grid(
            row=2, column=0, columnspan=2, sticky='w', padx=(12, 12), pady=(0, 2))
        self.obs_pass_entry = ctk.CTkEntry(obs_card, font=Theme.FONT_BODY, show='*')
        self.obs_pass_entry.insert(0, self.new_obs_settings.get('password', ''))
        self.obs_pass_entry.grid(row=3, column=0, columnspan=2, sticky='ew', padx=(12, 12), pady=(0, 8))

        # Separator
        ctk.CTkFrame(obs_card, height=1, fg_color=Theme.DIVIDER).grid(
            row=4, column=0, columnspan=2, sticky='ew', padx=12, pady=(4, 8))

        self.obs_auto_var = ctk.BooleanVar(value=self.new_obs_settings.get('auto_connect', False))
        ctk.CTkCheckBox(
            obs_card, text="Auto-connect on startup",
            variable=self.obs_auto_var, font=Theme.FONT_BODY
        ).grid(row=5, column=0, columnspan=2, sticky='w', padx=(12, 12), pady=(0, 4))

        # Test button + result label
        test_row = ctk.CTkFrame(obs_card, fg_color="transparent")
        test_row.grid(row=6, column=0, columnspan=2, sticky='ew', padx=(12, 12), pady=(4, 12))
        test_row.columnconfigure(1, weight=1)

        self.obs_test_label = ctk.CTkLabel(test_row, text="", font=Theme.FONT_BODY, anchor='w')
        self.obs_test_label.grid(row=0, column=1, sticky='ew', padx=(8, 0))
        ctk.CTkButton(
            test_row, text="Test Connection", font=Theme.FONT_BUTTON,
            command=self._test_obs_connection
        ).grid(row=0, column=0, sticky='w')

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

            key_str = self.new_keybinds.get(action_id, "").upper()
            if not key_str: key_str = "UNBOUND"
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
            fg_color=Theme.GREEN, hover_color=Theme.GREEN_HOVER, font=Theme.FONT_BUTTON
        ).grid(row=0, column=0, padx=(0, 4), sticky='ew')
        ctk.CTkButton(
            btn_row, text="Cancel", command=self.destroy,
            fg_color=Theme.RED, hover_color=Theme.RED_HOVER, font=Theme.FONT_BUTTON
        ).grid(row=0, column=1, padx=(4, 0), sticky='ew')

    def _browse_folder(self):
        chosen = filedialog.askdirectory(
            title="Choose Output Folder",
            initialdir=self.new_output_folder
        )
        if chosen:
            self.new_output_folder = chosen
            self.folder_label.configure(text=chosen)

    def _on_scale_slider(self, value):
        self.scale_pct_label.configure(text=f"{int(float(value) * 100)}%")

    def reopen_hud(self):
        if self.parent.timestamp_manager.stopwatch_running:
            self.parent._show_hud()

    def _test_gemini_key(self):
        key = self.gemini_key_entry.get().strip()
        if not key:
            self.gemini_test_label.configure(text="Enter a key first", text_color=Theme.RED)
            return
        self.gemini_test_label.configure(text="Testing...", text_color=Theme.GREY)
        self.update_idletasks()
        # Test in a thread so we don't freeze the UI
        def run_test():
            analyzer = GeminiAnalyzer()
            analyzer.set_api_key(key)
            ok, msg = analyzer.test_connection()
            self.parent.root.after(0, lambda: self.gemini_test_label.configure(
                text=f"✅ {msg}" if ok else f"❌ {msg}",
                text_color=Theme.GREEN if ok else Theme.RED
            ))
        Thread(target=run_test, daemon=True).start()

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
        
        capture_done = False
        
        def stop_listeners():
            if kb_listener.running: kb_listener.stop()
            if ms_listener.running: ms_listener.stop()

        def on_press_capture(key):
            nonlocal capture_done
            if capture_done: return False
            new_key_str = self.parent.get_key_str(key)
            
            if new_key_str == 'esc':
                button.configure(text=original_text, state="normal")
                capture_done = True
                stop_listeners()
                return False
                
            if new_key_str in ('backspace', 'delete'):
                self.new_keybinds[action_id] = ""
                button.configure(text="UNBOUND", state="normal")
                capture_done = True
                stop_listeners()
                return False
            
            for aid, bound_key in self.new_keybinds.items():
                if bound_key == new_key_str and aid != action_id and bound_key != "":
                    self.parent.root.after(0, lambda n=new_key_str: messagebox.showerror("Error", f"Key '{n.upper()}' is already bound.", parent=self))
                    button.configure(text=original_text, state="normal")
                    capture_done = True
                    stop_listeners()
                    return False

            self.new_keybinds[action_id] = new_key_str
            button.configure(text=new_key_str.upper(), state="normal")
            capture_done = True
            stop_listeners()
            return False

        def on_click_capture(x, y, button_event, pressed):
            nonlocal capture_done
            if not pressed or capture_done: return
            
            if button_event in (mouse.Button.left, mouse.Button.right):
                return
                
            new_key_str = f"mouse_{button_event.name}"
            
            for aid, bound_key in self.new_keybinds.items():
                if bound_key == new_key_str and aid != action_id and bound_key != "":
                    self.parent.root.after(0, lambda n=new_key_str: messagebox.showerror("Error", f"Key '{n.upper()}' is already bound.", parent=self))
                    button.configure(text=original_text, state="normal")
                    capture_done = True
                    stop_listeners()
                    return False

            self.new_keybinds[action_id] = new_key_str
            button.configure(text=new_key_str.upper(), state="normal")
            capture_done = True
            stop_listeners()
            return False

        kb_listener = keyboard.Listener(on_press=on_press_capture)
        ms_listener = mouse.Listener(on_click=on_click_capture)
        kb_listener.start()
        ms_listener.start()

    def save_and_close(self):
        # Save custom texts from entries
        for action_id, entry in self.text_entries.items():
            self.new_custom_texts[action_id] = entry.get()

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
        self.parent.obs_settings = self.new_obs_settings
        self.parent.hud_enabled = self.hud_var.get()
        self.parent.hud_opacity = self.opacity_slider.get()
        self.parent.screenshot_resolution = self.res_var.get()
        self.parent.gemini_api_key = self.gemini_key_entry.get().strip()
        self.parent.gemini_analyzer.set_api_key(self.parent.gemini_api_key)
        self.parent.viewer_scale = round(self.scale_slider.get(), 2)
        self.parent.text_viewer.set_scale(self.parent.viewer_scale)
        
        self.parent.timestamp_manager.set_output_dir(self.new_output_folder)
        self.parent.timestamp_manager.set_screenshot_resolution(self.parent.screenshot_resolution)
        self.parent.save_keybinds()
        self.parent.update_button_text()
        self.destroy()

class TimestampApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Nilvarcus Timestamp App")
        self.root.geometry("560x720")
        self.root.minsize(520, 600)
        self.root.resizable(True, True)
        
        self.root.grid_rowconfigure(1, weight=1)  # Text viewer expands
        self.root.grid_columnconfigure(0, weight=1)

        self.timestamp_manager = TimestampManager(base_path=get_base_path())
        self.keybinds_file = os.path.join(get_base_path(), 'keybinds.json')
        self.buttons = {}
        self.mini_widget = None
        self._key_to_action = {}  # Cached inverse map of keybinds (rebuilt on change)
        self.output_folder = os.path.join(get_base_path(), "Timestamp_TXT")  # default
        self.hud_enabled = True
        self.hud_opacity = 0.8
        self.screenshot_resolution = "720p"
        self.obs_settings = {
            'host': 'localhost', 'port': 4455, 'password': '', 'auto_connect': False
        }
        self.obs_manager = OBSManager(self.timestamp_manager)
        self.gemini_analyzer = GeminiAnalyzer()
        self.gemini_api_key = ""
        self.viewer_scale = 1.0
        
        self.action_labels = {
            'create_file': "📁  Open File", 'start_recording': "⏺  Start Rec",
            'mark_time': "📍  Mark Time", 'stop_recording': "⏹  Stop Rec",
            'save_short': "✂️  Save Short", 'take_screenshot': "📸  Screenshot",
            'resolve_export': "🎬  Resolve",
            'analyze_screenshots': "🤖  Analyze",
            'custom_note_1': "Custom Note 1", 'custom_note_2': "Custom Note 2",
            'custom_note_3': "Custom Note 3", 'custom_note_4': "Custom Note 4",
            'custom_note_5': "Custom Note 5",
        }
        self.default_keybinds = {
            'create_file': 'f13', 'start_recording': 'f14', 'mark_time': 'f15',
            'stop_recording': 'f16', 'save_short': 'f18', 'take_screenshot': 'f19',
            'resolve_export': '', 'analyze_screenshots': '',
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
        self._setup_obs()

        self.auto_save()
        self._start_keyboard_listener()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _create_widgets(self):
        self._create_header_bar()
        self._create_text_viewer()
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
                # Load saved obs settings
                saved_obs = data.get('obs_settings', {})
                if saved_obs:
                    self.obs_settings.update(saved_obs)
                self.hud_enabled = data.get('hud_enabled', True)
                self.hud_opacity = data.get('hud_opacity', 0.8)
                self.screenshot_resolution = data.get('screenshot_resolution', '720p')
                self.gemini_api_key = data.get('gemini_api_key', '')
                self.viewer_scale = data.get('viewer_scale', 1.0)
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
        
        # Apply settings to the manager
        self.timestamp_manager.set_output_dir(self.output_folder)
        self.timestamp_manager.set_screenshot_resolution(self.screenshot_resolution)
        self.gemini_analyzer.set_api_key(self.gemini_api_key)
        self.save_keybinds()

    def save_keybinds(self):
        with open(self.keybinds_file, 'w') as f:
            data = {
                'keybinds': self.keybinds,
                'custom_texts': self.custom_texts,
                'output_folder': self.output_folder,
                'obs_settings': self.obs_settings,
                'hud_enabled': self.hud_enabled,
                'hud_opacity': self.hud_opacity,
                'screenshot_resolution': self.screenshot_resolution,
                'gemini_api_key': self.gemini_api_key,
                'viewer_scale': self.viewer_scale,
            }
            json.dump(data, f, indent=4)

    def on_closing(self):
        self.save_changes()
        self.save_keybinds()
        self.obs_manager.disconnect()
        # Stop global hotkey listeners
        if hasattr(self, '_kb_listener') and self._kb_listener.running:
            self._kb_listener.stop()
        if hasattr(self, '_ms_listener') and self._ms_listener.running:
            self._ms_listener.stop()
        self.root.destroy()

    def auto_save(self):
        self.save_changes()
        self.root.after(60000, self.auto_save)

    def _create_header_bar(self):
        """Compact header bar: filename on the left, OBS status + connect on the right."""
        header = ctk.CTkFrame(self.root, fg_color=Theme.BG_SURFACE, corner_radius=10, height=36)
        header.grid(row=0, column=0, padx=12, pady=(12, 6), sticky='ew')
        header.grid_propagate(False)

        # Left: file indicator
        self.filename_label = ctk.CTkLabel(
            header, text="📄  No file open", font=Theme.FONT_BODY,
            anchor='w', text_color=Theme.TEXT_DIM
        )
        self.filename_label.pack(side=tk.LEFT, padx=(12, 0), pady=8)

        # Right: OBS section
        obs_row = ctk.CTkFrame(header, fg_color="transparent")
        obs_row.pack(side=tk.RIGHT, padx=(0, 6), pady=4)

        self.obs_status_label = ctk.CTkLabel(
            obs_row, text="🔴", font=Theme.FONT_BODY, text_color=Theme.RED, width=20
        )
        self.obs_status_label.pack(side=tk.LEFT, padx=(0, 2))

        self.obs_connect_btn = ctk.CTkButton(
            obs_row, text="Connect", width=80, height=26,
            font=Theme.FONT_SMALL, fg_color=Theme.GREY, hover_color=Theme.GREY_HOVER,
            command=self._toggle_obs_connection
        )
        self.obs_connect_btn.pack(side=tk.LEFT)

    def _create_text_viewer(self):
        text_frame = ctk.CTkFrame(self.root, fg_color=Theme.BG_SURFACE, corner_radius=14)
        text_frame.grid(row=1, column=0, padx=12, pady=(4, 10), sticky='nsew')
        
        self.text_viewer = RichTextLog(
            text_frame, wrap=tk.WORD, font=Theme.FONT_TEXT_AREA,
            fg_color=Theme.BG_ENTRY, corner_radius=10,
            text_color=Theme.TEXT_BRIGHT
        )
        self.text_viewer.timestamp_manager = self.timestamp_manager
        self.text_viewer.set_scale(self.viewer_scale)
        self.text_viewer.pack(expand=True, fill=tk.BOTH, padx=8, pady=8)

    def _update_filename_display(self):
        if self.timestamp_manager.current_file_path:
            self.filename_label.configure(
                text=f"📄  {os.path.basename(self.timestamp_manager.current_file_path)}",
                text_color=Theme.TEXT_BRIGHT
            )
        else:
            self.filename_label.configure(text="📄  No file open", text_color=Theme.TEXT_DIM)

    def _create_buttons(self):
        button_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        button_frame.grid(row=2, column=0, padx=12, pady=(0, 12), sticky='ew')
        button_frame.columnconfigure((0, 1), weight=1)

        button_config = {
            'create_file': (self.create_file, Theme.BTN_SURFACE, Theme.BTN_SURFACE_HOVER, 0, 0),
            'start_recording': (self.start_recording, Theme.GREEN, Theme.GREEN_HOVER, 0, 1),
            'mark_time': (self.mark_time, Theme.BTN_SURFACE, Theme.BTN_SURFACE_HOVER, 1, 0),
            'stop_recording': (self.stop_recording, Theme.RED, Theme.RED_HOVER, 1, 1),
            'save_short': (self.save_short, Theme.BTN_SURFACE, Theme.BTN_SURFACE_HOVER, 2, 0),
            'take_screenshot': (self.take_screenshot, Theme.BTN_SURFACE, Theme.BTN_SURFACE_HOVER, 2, 1),
            'resolve_export': (self.open_resolve_dialog, Theme.BTN_SURFACE, Theme.BTN_SURFACE_HOVER, 3, 0),
            'analyze_screenshots': (self.analyze_screenshots, Theme.BTN_SURFACE, Theme.BTN_SURFACE_HOVER, 3, 1),
        }

        for action_id, (command, bg, hover, row, col) in button_config.items():
            # Primary actions get white text; neutral buttons get dim text
            txt_color = Theme.TEXT_BRIGHT if action_id in ('start_recording', 'stop_recording') else Theme.BTN_TEXT
            btn = ctk.CTkButton(button_frame, command=command, fg_color=bg, hover_color=hover,
                               font=Theme.FONT_BUTTON, corner_radius=10, text_color=txt_color)
            btn.grid(row=row, column=col, padx=4, pady=4, sticky='ew')
            self.buttons[action_id] = btn

        # Divider separator
        divider = ctk.CTkFrame(button_frame, height=1, fg_color=Theme.DIVIDER)
        divider.grid(row=4, column=0, columnspan=2, sticky='ew', padx=8, pady=(10, 8))

        settings_btn = ctk.CTkButton(button_frame, text="⚙  Settings", command=self.open_settings_window,
                                     fg_color=Theme.GREY, hover_color=Theme.GREY_HOVER,
                                     font=Theme.FONT_BUTTON, corner_radius=10)
        settings_btn.grid(row=5, column=0, columnspan=2, padx=4, pady=4, sticky='ew')

    def update_button_text(self):
        for action_id, button in self.buttons.items():
            key_name = self.keybinds.get(action_id, '').upper()
            label_text = self.action_labels.get(action_id, 'Unknown')
            if key_name:
                button.configure(text=f"{label_text}    [{key_name}]")
            else:
                button.configure(text=label_text)
        self._rebuild_key_to_action()

    def _rebuild_key_to_action(self):
        """Rebuild the cached inverse keybind map. Call whenever keybinds change."""
        self._key_to_action = {v: k for k, v in self.keybinds.items() if v}

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
            'save_short': self.save_short,
            'take_screenshot': self.take_screenshot,
            'resolve_export': self.open_resolve_dialog,
            'analyze_screenshots': self.analyze_screenshots,
            'custom_note_1': lambda: self.mark_custom_note_n('custom_note_1'),
            'custom_note_2': lambda: self.mark_custom_note_n('custom_note_2'),
            'custom_note_3': lambda: self.mark_custom_note_n('custom_note_3'),
            'custom_note_4': lambda: self.mark_custom_note_n('custom_note_4'),
            'custom_note_5': lambda: self.mark_custom_note_n('custom_note_5'),
        }
        self.pressed_keys = set()
        # pynput Listeners are Thread subclasses — start them directly, don't wrap in Thread()
        self._kb_listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._kb_listener.daemon = True
        self._kb_listener.start()

        self._ms_listener = mouse.Listener(on_click=self._on_mouse_click)
        self._ms_listener.daemon = True
        self._ms_listener.start()

    def _on_press(self, key):
        key_str = self.get_key_str(key)
        if key_str in self.pressed_keys:
            return  # Prevent auto-repeat triggers
        self.pressed_keys.add(key_str)
        
        action_id = self._key_to_action.get(key_str)
        if action_id in self.action_map:
            try:
                self.root.after(0, self.action_map[action_id])
            except Exception as e:
                print(f"Error executing action '{action_id}': {e}")
                
    def _on_release(self, key):
        key_str = self.get_key_str(key)
        if key_str in self.pressed_keys:
            self.pressed_keys.remove(key_str)

    def _on_mouse_click(self, x, y, button, pressed):
        key_str = f"mouse_{button.name}"
        if not pressed:
            if key_str in self.pressed_keys:
                self.pressed_keys.remove(key_str)
            return
            
        if key_str in self.pressed_keys:
            return
        self.pressed_keys.add(key_str)
        
        action_id = self._key_to_action.get(key_str)
        if action_id in self.action_map:
            try:
                self.root.after(0, self.action_map[action_id])
            except Exception as e:
                print(f"Error executing action '{action_id}': {e}")

    def create_file(self):
        file_path = self.timestamp_manager.create_file()
        if file_path: self.update_text_viewer(); self._update_filename_display()

    def _show_hud(self):
        """Create the HUD widget if needed, or deiconify + resume timer if it exists."""
        if self.mini_widget is None or not self.mini_widget.winfo_exists():
            self.mini_widget = RecordingWidget(self)
        else:
            self.mini_widget.deiconify()
            self.mini_widget.update_timer()

    def _hud_status(self, message, color=Theme.GREEN):
        """Show a status message on the HUD if it exists and is visible."""
        if self.mini_widget and self.mini_widget.winfo_exists():
            self.mini_widget.show_status(message, color=color)

    def start_recording(self, from_obs=False):
        self.save_changes()
        if self.timestamp_manager.start_recording():
            self.update_text_viewer()
            if self.hud_enabled:
                self._show_hud()
            
            if not from_obs:
                self.obs_manager.start_obs_recording()

    def mark_time(self):
        self.save_changes()
        if self.timestamp_manager.mark_time():
            self.update_text_viewer()
            self._hud_status("Timestamp Marked!", color=Theme.BLUE)

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
        """Save Short marker — only works when OBS replay buffer is actively running."""
        self.save_changes()

        # Require OBS to be connected and replay buffer to be active.
        # Save Short is meaningless without the replay buffer running.
        if not self.obs_manager.is_connected:
            self._hud_status("OBS Not Connected!", color=Theme.RED)
            return

        if not self.obs_manager.is_replay_buffer_active:
            self._hud_status("Replay Buffer Off!", color=Theme.RED)
            return

        # Replay buffer is active — save it and write the SHORT marker.
        success = self.obs_manager.save_replay_buffer()
        is_error = not success

        if self.timestamp_manager.save_short(error=is_error):
            self.update_text_viewer()
            if is_error:
                self._hud_status("Replay Save Failed!", color=Theme.RED)
            else:
                self._hud_status("Short Saved!", color=Theme.GREEN)

    def take_screenshot(self):
        self.save_changes()
        if self.timestamp_manager.take_screenshot():
            self.update_text_viewer()
            self._hud_status("Screenshot Saved!", color=Theme.GREEN)

    def open_resolve_dialog(self):
        self.save_changes()
        open_resolve_export_dialog(self)

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
                self.obs_status_label.configure(text="🟢", text_color=Theme.GREEN)
                self.obs_connect_btn.configure(text="Disconnect")
            elif status == "connecting":
                self.obs_status_label.configure(text="🟡", text_color=Theme.AMBER)
                self.obs_connect_btn.configure(text="Cancel")
            elif status == "disconnected":
                self.obs_status_label.configure(text="🔴", text_color=Theme.RED)
                self.obs_connect_btn.configure(text="Connect")
            elif status.startswith("error:"):
                self.obs_status_label.configure(text="❌", text_color=Theme.RED)
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
            self._hud_status(f"📺 {scene_name}", color=Theme.BLUE)
        self.root.after(0, update)

    def _on_obs_replay_saved(self):
        def update():
            self.update_text_viewer()
            self._hud_status("💾 Replay Saved!", color=Theme.GREEN)
        self.root.after(0, update)

    def mark_custom_note_n(self, action_id):
        self.save_changes()
        custom_text = self.custom_texts.get(action_id, "")
        if self.timestamp_manager.mark_custom_note(custom_text):
            self.update_text_viewer()
            self._hud_status(f"Added: {custom_text}", color=Theme.BLUE)

    def save_changes(self):
        if self.timestamp_manager.current_file_path:
            self.timestamp_manager.save_changes(self.text_viewer.get_text_only())

    def update_text_viewer(self):
        text_content = self.timestamp_manager.read_file_content()
        self.text_viewer.insert_text(text_content)
        self.text_viewer.see(tk.END)

    # ── Gemini AI Integration ────────────────────────────────────────────────

    def analyze_screenshots(self):
        """Analyze all un-analyzed screenshots in the current log using Gemini AI."""
        self.save_changes()
        if not self.gemini_analyzer.is_configured:
            messagebox.showwarning(
                "Gemini Not Configured",
                "Please add your Google Gemini API key in Settings → AI tab.",
                parent=self.root
            )
            return
        if not self.timestamp_manager.current_file_path:
            messagebox.showwarning("No File", "Create or open a file first.", parent=self.root)
            return

        # Check if the file contains any screenshot wikilinks at all
        content = self.timestamp_manager.read_file_content()
        has_screenshots = "![[" in content

        entries = self.timestamp_manager.get_screenshot_entries()
        if not entries:
            if has_screenshots:
                messagebox.showinfo(
                    "Already Analyzed",
                    "All screenshots in this log have already been analyzed — nothing new to do.",
                    parent=self.root
                )
            else:
                messagebox.showinfo(
                    "No Screenshots",
                    "No screenshots found in the current log file.",
                    parent=self.root
                )
            return

        # Register callbacks and start analysis
        self.gemini_analyzer.register_callbacks(
            on_progress=self._on_gemini_progress,
            on_complete=self._on_gemini_complete,
            on_error=self._on_gemini_error,
        )
        self._hud_status("🤖 AI Analyzing...", color=Theme.AMBER)
        self.gemini_analyzer.analyze_all_screenshots(entries)

    def _on_gemini_progress(self, current, total, message):
        def update():
            self._hud_status(f"🤖 {current}/{total} {message}", color=Theme.AMBER)
        self.root.after(0, update)

    def _on_gemini_complete(self, results):
        def update():
            # Insert each AI description into the file, matched by
            # counter AND unique filename to avoid collisions across
            # multiple recording sessions in the same file.
            for result in results:
                desc = result.get('description', '')
                counter = result.get('counter', 0)
                filename = result.get('filename', '')
                if desc and filename:
                    self.timestamp_manager.add_ai_description(counter, filename, desc)

            # Re-read the file and refresh the viewer to show new descriptions.
            # Don't call save_changes() first — the file was just written by
            # add_ai_description() and is the source of truth.
            text_content = self.timestamp_manager.read_file_content()
            self.text_viewer.insert_text(text_content)
            self.text_viewer.see(tk.END)

            successful = sum(1 for r in results if r.get('description'))
            total = len(results)
            self._hud_status(f"🤖 Done! {successful}/{total} analyzed", color=Theme.GREEN)
        self.root.after(0, update)

    def _on_gemini_error(self, error_msg):
        def update():
            self._hud_status(f"🤖 Error: {error_msg}", color=Theme.RED)
            messagebox.showerror("Gemini Error", error_msg, parent=self.root)
        self.root.after(0, update)

def main():
    # Global hotkeys won't work in games unless we run as admin (Windows UIPI).
    if not is_admin():
        # Relaunch with admin rights. If the user declines UAC, the
        # original process exits — the app simply won't run without admin.
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, subprocess.list2cmdline(sys.argv), None, 1
        )
        sys.exit()

    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    # Set window background to match our theme
    root = ctk.CTk()
    root.configure(fg_color=Theme.BG_DARKEST)
    app = TimestampApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
