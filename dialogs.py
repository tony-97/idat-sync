import tkinter as tk
from tkinter import ttk


class LoadingDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Login in...")
        self.geometry("250x100")
        self.lift()
        self.attributes("-topmost", True)
        self.attributes("-topmost", False)
        # Center on parent
        parent_x = parent.winfo_x()
        parent_y = parent.winfo_y()
        parent_width = parent.winfo_width()
        parent_height = parent.winfo_height()
        x = parent_x + (parent_width // 2) - (300 // 2)
        y = parent_y + (parent_height // 2) - (120 // 2)
        self.geometry(f"+{x}+{y}")

        # Make it modal
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)  # Prevent closing

        ttk.Label(self, text="Processing data, please wait...").pack(pady=20)

        self.progress = ttk.Progressbar(self, mode="indeterminate", length=200)
        self.progress.pack(pady=10)
        self.progress.start(15)
        self.update()
