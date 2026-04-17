import tkinter as tk
import customtkinter as ctk
from tkinter import messagebox
import re
import os
import sys

class ResolveExportDialog(ctk.CTkToplevel):
    def __init__(self, parent, text_content, template_path):
        super().__init__(parent.root)
        self.title("Resolve Export Options")
        self.geometry("350x200")
        self.text_content = text_content
        self.template_path = template_path
        self.parent = parent
        
        # Center in parent
        self.transient(parent.root)
        self.grab_set()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        # FPS Input
        ctk.CTkLabel(self, text="FPS (e.g., 60):").grid(row=0, column=0, pady=(15, 0), padx=20, sticky="w")
        self.fps_entry = ctk.CTkEntry(self)
        self.fps_entry.insert(0, "60")
        self.fps_entry.grid(row=1, column=0, pady=(5, 5), padx=20, sticky="ew")
        
        # Duration Input
        ctk.CTkLabel(self, text="Clip Duration (seconds):").grid(row=2, column=0, pady=(5, 0), padx=20, sticky="w")
        self.duration_entry = ctk.CTkEntry(self)
        self.duration_entry.insert(0, "6")
        self.duration_entry.grid(row=3, column=0, pady=(5, 5), padx=20, sticky="ew")
        
        # Generate Button
        self.gen_btn = ctk.CTkButton(self, text="Generate & Copy to Clipboard", command=self.generate)
        self.gen_btn.grid(row=4, column=0, pady=(10, 20), padx=20, sticky="ew")
        
    def generate(self):
        fps = self.fps_entry.get().strip()
        duration = self.duration_entry.get().strip()
        
        try:
            fps_val = int(fps)
            dur_val = int(duration)
        except ValueError:
            messagebox.showerror("Error", "FPS and Duration must be valid numbers.", parent=self)
            return
            
        # Parse text content for timestamps
        # Format: *  **[1]**   **[00:00:10]** - Note text...
        timestamps = []
        lines = self.text_content.splitlines()
        current_clip = "UnknownClip.mp4" # fallback if no Filename: tag is present
        
        for line in lines:
            # Check if this line starts a new clip
            file_match = re.search(r"## 0 - Filename:\s*(.+)", line)
            if file_match:
                name = file_match.group(1).strip()
                if not name.lower().endswith(".mp4") and not name.lower().endswith(".mkv"):
                    name += ".mp4"
                current_clip = name
                continue
                
            match = re.search(r"\*\s*\*\*\[\d+\]\*\*\s*\*\*\[(.*?)\]\*\*\s*-\s*(.*)", line)
            if match:
                time_str = match.group(1).strip()
                note = match.group(2).strip()
                
                # Check if it's a screenshot and remove the markdown image part if present
                if "![Screenshot]" in note:
                    note = note.split("![Screenshot]")[0].strip()
                    if note.endswith("📸 Screenshot →"):
                        note = note.replace("📸 Screenshot →", "").strip()
                
                if not note: 
                    note = "Marker"
                
                # Escape strings safely
                note = note.replace('"', '\\"').replace("'", "\\'")
                timestamps.append((current_clip, time_str, note))
                
        if not timestamps:
            messagebox.showwarning("Warning", "No timestamps found in the current log.", parent=self)
            return

        # Format timestamps array
        ts_code = []
        for cn, ts, n in timestamps:
            ts_code.append(f'    ("{cn}", "{ts}", "{n}"),')
            
        ts_code_str = "\n".join(ts_code)
        
        # Read template
        if not os.path.exists(self.template_path):
            messagebox.showerror("Error", f"Resolve template not found at {self.template_path}", parent=self)
            return
            
        try:
            with open(self.template_path, "r", encoding="utf-8") as f:
                template = f.read()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read template: {e}", parent=self)
            return
            
        # Replace placeholders precisely
        template = re.sub(r"FPS\s*=\s*\d+", f"FPS = {fps_val}", template)
        template = re.sub(r"CLIP_DURATION_SECONDS\s*=\s*\d+", f"CLIP_DURATION_SECONDS = {dur_val}", template)
        template = template.replace("    # TIMESTAMPS_PLACEHOLDER", ts_code_str)
        template = template.replace("TIMELINE_NAME_PLACEHOLDER", "Export_Timeline")
        
        # Copy to clipboard
        self.parent.root.clipboard_clear()
        self.parent.root.clipboard_append(template)
        
        messagebox.showinfo(
            "Success!", 
            "Resolve code generated and copied to clipboard!\n\n"
            "Inside DaVinci Resolve:\n"
            "1. Open Workspace -> Console\n"
            "2. Select 'Python 3'\n"
            "3. Paste and press Enter.",
            parent=self.parent.root
        )
        self.destroy()

def open_resolve_export_dialog(app_instance):
    text_content = app_instance.text_viewer.get("1.0", tk.END)
    
    # Locate template file properly for both dev and PyInstaller environments
    base_path = os.path.dirname(os.path.abspath(__file__))
    if getattr(sys, 'frozen', False):
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        
    template_path = os.path.join(base_path, "resolve_template.py")
        
    ResolveExportDialog(app_instance, text_content, template_path)
