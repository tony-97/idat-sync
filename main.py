import os
import sys
from typing import cast
from contextlib import redirect_stdout
from collections.abc import Callable
import threading

import tkinter as tk
from tkinter import filedialog

from idat_sync import IDATSync
from auth import AuthProvider


class ProgressInterceptor:
    def __init__(self, log_func: Callable[[str], None]) -> None:
        self.original_stdout = sys.stdout
        self.log_func = log_func

    def write(self, text: str):
        self.original_stdout.write(text)
        self.log_func(text)

    def flush(self):
        self.original_stdout.flush()


class MainApp(tk.Tk):
    def __init__(self):
        tk.Tk.__init__(self)
        self.auth = AuthProvider()
        self.__frame = None
        if self.auth.is_authenticated():
            self.switch_to_sync()
        else:
            self.switch_to_login()

    def center_window(self, window_width, window_height):
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        center_x = int(screen_width / 2 - window_width / 2)
        center_y = int(screen_height / 2 - window_height / 2)
        self.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")

    def __change(self, frame: type[tk.Frame], **kwargs):
        if self.__frame:
            self.__frame.pack_forget()  # delete currrent frame
        self.__frame = frame(self, **kwargs)
        self.__frame.pack()  # make new frame

    def switch_to_sync(self):
        if credentials := self.auth.get_credentials():
            idat_sync = IDATSync(credentials)
            self.__change(SyncFrame, idat_sync=idat_sync)

    def switch_to_login(self):
        self.__change(LoginFrame, auth=self.auth)


class LoginFrame(tk.Frame):
    def __init__(self, master: MainApp, auth: AuthProvider, **kwargs):
        tk.Frame.__init__(self, master, **kwargs)
        self.auth = auth
        self.auth.logout()
        # Center the window
        window_width = 300
        window_height = 150
        master.center_window(window_width, window_height)
        master.title("IDAT Sync Login")

        tk.Label(self, text="Username:").pack(pady=5)
        self.user_entry = tk.Entry(self, width=30)
        self.user_entry.pack()
        self.user_entry.focus()

        tk.Label(self, text="Password:").pack(pady=5)
        self.pass_entry = tk.Entry(self, show="*", width=30)
        self.pass_entry.pack()
        self.pass_entry.bind("<Return>", self.do_login)

        login_button = tk.Button(self, text="Login", command=self.do_login)
        login_button.pack(pady=10)

    def do_login(self, event=None):
        if (user := self.user_entry.get()) and (password := self.pass_entry.get()):
            try:
                if self.auth.login(user, password):
                    cast(MainApp, self.master).switch_to_sync()
            except:
                ...
        else:
            ...


class SyncFrame(tk.Frame):
    def __init__(self, master: MainApp, idat_sync: IDATSync, **kwargs):
        tk.Frame.__init__(self, master, **kwargs)
        window_width = 700
        window_height = 500
        master.center_window(window_width, window_height)
        master.title("IDAT Sync")

        self.idat_sync = idat_sync

        # Frame for folder selection
        folder_frame = tk.Frame(self, padx=10, pady=10)
        folder_frame.pack(fill=tk.X)

        tk.Label(folder_frame, text="Sync Folder:").pack(side=tk.LEFT, padx=(0, 5))
        self.folder_path = tk.StringVar()
        folder_entry = tk.Entry(
            folder_frame, textvariable=self.folder_path, state="readonly"
        )
        folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        browse_button = tk.Button(
            folder_frame, text="Browse...", command=self.browse_folder
        )
        browse_button.pack(side=tk.LEFT, padx=(5, 0))

        # Frame for progress output
        output_frame = tk.Frame(self, padx=10)
        output_frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(output_frame, text="Progress:").pack(anchor="w")
        self.output_text = tk.Text(output_frame, state="disabled", wrap="word")
        scrollbar = tk.Scrollbar(output_frame, command=self.output_text.yview)
        self.output_text.config(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Frame for action buttons
        button_frame = tk.Frame(self, padx=10, pady=10)
        button_frame.pack(fill=tk.X)
        tk.Button(
            button_frame,
            text="Logout",
            command=lambda: cast(MainApp, self.master).switch_to_login(),
        ).pack(side=tk.LEFT)
        self.sync_button = tk.Button(button_frame, text="Sync", command=self.do_sync)
        self.sync_button.pack(side=tk.RIGHT)

        self.progress_interceptor = ProgressInterceptor(
            lambda text: self.update_progress(text)
        )

    def update_progress(self, text):
        self.output_text.config(state="normal")
        self.output_text.insert(tk.END, text)
        self.output_text.see(tk.END)
        self.output_text.config(state="disabled")
        self.update_idletasks()

    def do_sync(self):
        if path := self.folder_path.get():
            self.sync_button.config(state="disabled")
            self.output_text.config(state="normal")
            self.output_text.delete("1.0", tk.END)
            self.output_text.config(state="disabled")

            def sync_task():
                with redirect_stdout(self.progress_interceptor):  # type: ignore
                    self.idat_sync.sync_courses(path)
                self.sync_button.config(state="normal")

            threading.Thread(target=sync_task, daemon=True).start()

    def browse_folder(self):
        folder_selected = filedialog.askdirectory(
            initialdir=os.path.expanduser("~"),
            parent=self.master,
            title="Select Sync Folder",
        )
        if folder_selected:
            self.folder_path.set(folder_selected)


if __name__ == "__main__":
    app = MainApp()
    app.mainloop()
