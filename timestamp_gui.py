import tkinter as tk
from tkinter import scrolledtext, font
from pynput import keyboard
from threading import Thread

# Import the TimestampManager from the local module
from timestamp_functions import TimestampManager, os

class StyledButton(tk.Button):
    def __init__(self, master, **kwargs):
        """
        Custom styled button with rounded corners and color options.
        
        Additional kwargs:
        - bg_color: Background color
        - hover_color: Color when mouse hovers
        - text_color: Text color
        """
        # Extract custom styling parameters
        bg_color = kwargs.pop('bg_color', '#4A90E2')  # Default blue
        hover_color = kwargs.pop('hover_color', '#357ABD')  # Darker blue
        text_color = kwargs.pop('text_color', 'white')
        
        # Call parent constructor with modified kwargs
        super().__init__(master, **kwargs)
        
        # Configure button styling
        self.configure(
            bg=bg_color,
            fg=text_color,
            activebackground=hover_color,
            relief=tk.FLAT,
            borderwidth=0
        )
        
        # Custom hover effects
        def on_enter(e):
            self.configure(bg=hover_color)
        
        def on_leave(e):
            self.configure(bg=bg_color)
        
        self.bind('<Enter>', on_enter)
        self.bind('<Leave>', on_leave)

class TimestampApp:
    def __init__(self, root):
        """
        Initialize the Timestamping GUI application with enhanced styling.
        
        Args:
            root (tk.Tk): The main Tkinter root window.
        """
        self.root = root
        self.root.title("Nilvarcus Timestamp App")
        self.root.geometry("435x600")  # Set a default size
        self.root.configure(bg='#F0F4F8')  # Light background color
        
        # Make the root window resizable
        self.root.grid_rowconfigure(1, weight=1)  # Make the text viewer row expandable
        self.root.grid_columnconfigure(0, weight=1)  # Make the column expandable

        # Create TimestampManager instance
        self.timestamp_manager = TimestampManager()
        
        # Create GUI elements
        self._create_header()
        self._create_text_viewer()
        self._create_filename_display()
        self._create_buttons()


        # Start the autosave function
        self.auto_save()
        
        # Start keyboard listener
        self._start_keyboard_listener()

        # Bind the window closing event to run autosave one last time
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)


    def on_closing(self):
        # Run save_changes immediately before closing
        self.save_changes()
        print("Final autosave run before closing")
        self.root.destroy()

    def auto_save(self):
        self.save_changes()  # Call the save_changes method
        print("Auto-saved changes")  # Print to terminal
        # Reschedule auto_save to run again in 15 seconds (15000 ms)
        self.root.after(60000, self.auto_save)


    def _create_header(self):
        """Create a styled header with logo and title."""
        header_frame = tk.Frame(self.root, bg='#FFFFFF', padx=20, pady=15)
        header_frame.grid(row=0, column=0, sticky='ew', padx=10, pady=(10, 0))
        
        # Load your custom logo image
        logo_image = tk.PhotoImage(file="logo.png")  # Replace "your_logo.png" with your actual image path

        # Resize the image (adjust width and height as needed)
        resized_logo = logo_image.subsample(80, 80)  # This halves the image size in both dimensions
        
        # Display the logo image
        logo_label = tk.Label(
            header_frame,
            image=resized_logo,
            bg='#FFFFFF'
        )
        logo_label.image = resized_logo  # Keep a reference to prevent garbage collection
        logo_label.pack(side=tk.LEFT, anchor=tk.NE)
        
        # Title
        title_font = font.Font(family='Segoe UI', size=16, weight='bold')
        title_label = tk.Label(
            header_frame, 
            text="Nilvarcus Timestamp App", 
            font=title_font, 
            bg='#FFFFFF', 
            fg='#2C3E50'
        )
        title_label.pack(fill=tk.X, anchor=tk.CENTER) #Center the Title

        def check_window_width():
            if self.root.winfo_width() < 435:  # Adjust this threshold as needed
                title_label.pack_forget()
            else:
                title_label.pack(fill=tk.X, anchor=tk.CENTER)
        # Bind the check function to window resize events
        self.root.bind("<Configure>", lambda event: check_window_width())

        # Initial check to hide the title if the window is too small initially
        check_window_width()

    def _create_text_viewer(self):
        """Create the scrolled text viewer with improved styling."""
        text_frame = tk.Frame(self.root, bg='#F0F4F8')
        text_frame.grid(row=1, column=0, padx=20, pady=15, sticky='nsew')
        
        self.text_viewer = scrolledtext.ScrolledText(
            text_frame, 
            wrap=tk.WORD, 
            font=("Consolas", 10),
            bg='#FFFFFF',
            borderwidth=1,
            relief=tk.FLAT
        )
        self.text_viewer.pack(expand=True, fill=tk.BOTH)

    def _create_filename_display(self):
        """Create a section to display the currently open file's name."""
        filename_frame = tk.Frame(self.root, bg='#F0F4F8')
        filename_frame.grid(row=2, column=0, padx=20, pady=5, sticky='ew')

        # Add a label for "Current File:"
        label = tk.Label(
            filename_frame, 
            text="Current File:", 
            font=("Segoe UI", 10, "bold"), 
            bg='#F0F4F8', 
            fg='#2C3E50'
        )
        label.pack(side=tk.LEFT, padx=(0, 10))

        # Add a label to dynamically show the file name
        self.filename_label = tk.Label(
            filename_frame, 
            text="No file open", 
            font=("Segoe UI", 10), 
            bg='#F0F4F8', 
            fg='#34495E', 
            anchor="w"
        )
        self.filename_label.pack(side=tk.LEFT, expand=True, fill=tk.X)

    def _update_filename_display(self):
        """Update the filename label with the current file name."""
        if self.timestamp_manager.current_file_path:
            self.filename_label.config(
                text=os.path.basename(self.timestamp_manager.current_file_path)
            )
        else:
            self.filename_label.config(text="No file open")

    def _create_buttons(self):
        """Create styled buttons for various actions."""
        button_frame = tk.Frame(self.root, bg='#F0F4F8')
        button_frame.grid(row=3, column=0, pady=10, sticky='ew')

        # Configure the grid to allow column expansion
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)

        buttons = [
            ("Create File (F13)", self.create_file, '#3498DB', '#2980B9', 0, 0),
            ("Start Recording (F14)", self.start_recording, '#2ECC71', '#27AE60', 0, 1),
            ("Mark Time (F15)", self.mark_time, '#F39C12', '#D35400', 1, 0),
            ("Stop Recording (F16)", self.stop_recording, '#E74C3C', '#C0392B', 1, 1),
            ("Voice Note (F17)", self.mark_voice_note, '#9B59B6', '#8E44AD', 2, 1),
            ("Save Short (F18)", self.save_short, '#1ABC9C', '#16A085', 2, 0)
        ]

        for text, command, bg, hover, row, col in buttons:
            btn = StyledButton(
                button_frame, 
                text=text, 
                command=command, 
                bg_color=bg,
                hover_color=hover,
                text_color='white'
            )
            btn.grid(row=row, column=col, padx=5, pady=3, sticky='ew')  # Use sticky='ew' to expand horizontally

    def _start_keyboard_listener(self):
        """Start the keyboard listener in a separate thread."""
        def listen():
            with keyboard.Listener(on_press=self._on_press) as listener:
                listener.join()
        Thread(target=listen, daemon=True).start()

    def _on_press(self, key):
        """
        Handle key press events.
        
        Args:
            key: The key that was pressed.
        """
        try:
            if key == keyboard.Key.f13:  # Create a new file
                self.create_file()
            elif key == keyboard.Key.f14:  # Start recording
                self.start_recording()
            elif key == keyboard.Key.f15:  # Mark time
                self.mark_time()
            elif key == keyboard.Key.f16:  # Stop recording
                self.stop_recording()
            elif key == keyboard.Key.f17:  # Take a Voice Note
                self.mark_voice_note()
            elif key == keyboard.Key.f18:  # Take a Short
                self.save_short()
        except Exception as e:
            print(f"Error: {e}")

    def create_file(self):
        """Create a new file and update text viewer and filename display."""
        file_path = self.timestamp_manager.create_file()
        if file_path:
            print(f"File '{file_path}' created.")
            self.update_text_viewer()
            self._update_filename_display()  # Update the filename label

    def start_recording(self):
        """Start recording and print status."""
        self.save_changes()
        if self.timestamp_manager.start_recording():
            self.update_text_viewer()
            print("Recording started.")

    def mark_time(self):
        """Mark time and update text viewer."""
        self.save_changes()
        marked_time = self.timestamp_manager.mark_time()
        if marked_time:
            self.update_text_viewer()
            print(f"Marked time: {marked_time}")

    def stop_recording(self):
        """Stop recording and update text viewer."""
        self.save_changes()
        if self.timestamp_manager.stop_recording():
            self.update_text_viewer()
            print("Recording stopped and reset.")

    def save_short(self):
        self.save_changes()
        """Save a short and update text viewer."""
        if self.timestamp_manager.save_short():
            self.update_text_viewer()
            print("Short Taken")

    def save_changes(self):
        """Save changes from text viewer to file."""
        if self.timestamp_manager.save_changes(
            self.text_viewer.get("1.0", tk.END)
        ):
            print("Changes saved.")

    def update_text_viewer(self):
            """Update the text viewer with file contents."""
            text_content = self.timestamp_manager.read_file_content()
            
            # Clear existing content
            self.text_viewer.delete("1.0", tk.END)
            
            # Insert lines without tags
            for line in text_content.split('\n'):
                self.text_viewer.insert(tk.END, line + '\n')
            
            # Scroll to the end
            self.text_viewer.see(tk.END)


    def mark_voice_note(self):
        self.save_changes()
        self.mark_time()
        if self.timestamp_manager.mark_voice_note():
            self.update_text_viewer()
            print("Voice Note Taken")



def main():
    """Main function to run the Timestamping App."""
    root = tk.Tk()
    app = TimestampApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
