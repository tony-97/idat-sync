import asyncio
from contextlib import redirect_stdout
import os
import tkinter as tk
from tkinter import filedialog
from typing import cast

from async_tkinter_loop import async_handler

from idat_sync.application.ports import Credentials
from idat_sync.application.sync_service import IDATSync
from idat_sync.bootstrap import create_sync_service
from idat_sync.config import SyncConfig
from idat_sync.ui.app import AppFrame, MainApp, ProgressInterceptor


class SyncFrame(AppFrame):
    def __init__(
        self,
        master: MainApp,
        credentials: Credentials,
        config: SyncConfig,
        **kwargs,
    ):
        tk.Frame.__init__(self, master, **kwargs)
        self.config = config
        window_width = 700
        window_height = 500
        master.center_window(window_width, window_height)
        master.title("IDAT Sync")

        self.idat_sync: None | IDATSync = None

        # Frame for folder selection
        folder_frame = tk.Frame(self, padx=10, pady=10)
        folder_frame.pack(fill=tk.X)

        tk.Label(folder_frame, text="Sync Folder:").pack(
            side=tk.LEFT, padx=(0, 5)
        )
        self.folder_path = tk.StringVar()
        folder_entry = tk.Entry(
            folder_frame, textvariable=self.folder_path, state="readonly"
        )
        folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.browse_button = tk.Button(
            folder_frame, text="Browse...", command=self.browse_folder
        )
        self.browse_button.pack(side=tk.LEFT, padx=(5, 0))

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
        self.logout_button = tk.Button(
            button_frame,
            text="Logout",
            command=lambda: cast(MainApp, self.master).switch_to_login(),
        )
        self.logout_button.pack(side=tk.LEFT)
        self.sync_button = tk.Button(
            button_frame,
            text="Sync",
            command=async_handler(self.do_sync),
            state="disabled",
        )
        self.sync_button.pack(side=tk.RIGHT)
        self.is_syncing = tk.BooleanVar(value=False)
        self.is_syncing.trace("w", self.validate_buttons)
        self.folder_path.trace("w", self.validate_buttons)

        self.after_idle(
            lambda: master.loop.create_task(self.load_idat_sync(credentials))
        )
        self.progress_queue: asyncio.Queue[str] = asyncio.Queue()
        self.progress_interceptor = ProgressInterceptor(
            lambda text: master.loop.call_soon_threadsafe(
                self.progress_queue.put_nowait, text
            )
        )
        master.loop.create_task(self.update_progress())

    def exit(self):
        self.progress_queue.put_nowait(None)  # type: ignore

    def validate_buttons(self, *args):
        is_syncing = self.is_syncing.get()
        folder_is_selected = bool(self.folder_path.get().strip())

        action_state = "disabled" if is_syncing else "normal"
        sync_state = (
            "normal" if not is_syncing and folder_is_selected else "disabled"
        )

        self.browse_button.config(state=action_state)
        self.logout_button.config(state=action_state)
        self.sync_button.config(state=sync_state)

    async def load_idat_sync(self, credentials: Credentials):
        self.is_syncing.set(True)
        self.idat_sync = await asyncio.to_thread(
            create_sync_service, credentials, self.config
        )
        self.is_syncing.set(False)

    async def update_progress(self):
        while True:
            text = await self.progress_queue.get()
            if text is None:
                return
            self.output_text.config(state="normal")
            if text.startswith("\r"):
                last_insert_begin, last_insert_end = (
                    self.output_text.tag_ranges("last_insert")
                )
                self.output_text.delete(last_insert_begin, last_insert_end)
            self.output_text.tag_remove("last_insert", "1.0", tk.END)
            self.output_text.insert(tk.END, text, "last_insert")
            self.output_text.see(tk.END)
            self.output_text.config(state="disabled")
            self.progress_queue.task_done()

    async def do_sync(self):
        if path := self.folder_path.get():
            self.is_syncing.set(True)
            self.output_text.config(state="normal")
            self.output_text.delete("1.0", tk.END)
            self.output_text.config(state="disabled")

            def sync_task():
                with redirect_stdout(self.progress_interceptor):  # type: ignore
                    self.idat_sync.sync_courses(path)  # type: ignore

            await asyncio.to_thread(sync_task)
            await self.progress_queue.put("Sync done...")
            self.is_syncing.set(False)

    def browse_folder(self):
        folder_selected = filedialog.askdirectory(
            initialdir=os.path.expanduser("~"),
            parent=self.master,
            title="Select Sync Folder",
        )
        if folder_selected:
            self.folder_path.set(folder_selected)
