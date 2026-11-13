from __future__ import annotations

import asyncio
from collections.abc import Callable
import io
import sys
import tkinter as tk
from typing import Any

from async_tkinter_loop import async_mainloop

from idat_sync.config import SyncConfig, load_config
from idat_sync.infrastructure.auth import AuthProvider


class ProgressInterceptor:
    def __init__(self, log_func: Callable[[str], Any]) -> None:
        self.original_stdout = sys.stdout
        self.log_func = log_func

    def write(self, text: str):
        self.original_stdout.write(text)
        self.log_func(text)

    def flush(self):
        self.original_stdout.flush()


class AppFrame(tk.Frame):
    def exit(self): ...


class MainApp(tk.Tk):
    def __init__(self, config: SyncConfig):
        tk.Tk.__init__(self)
        self.loop = asyncio.new_event_loop()
        self.config = config
        self.auth = AuthProvider(
            session_file=self.config.session_file,
            moodle_base_url=self.config.moodle_base_url,
            sharepoint_host=self.config.sharepoint_host,
            sharepoint_personal_host=self.config.sharepoint_personal_host,
            email_domain=self.config.email_domain,
        )
        self.__frame: AppFrame | None = None
        if self.auth.is_authenticated():
            self.switch_to_sync()
        else:
            self.switch_to_login()

    def run(self):
        async_mainloop(self, self.loop)

    def center_window(self, window_width: int, window_height: int):
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        center_x = int(screen_width / 2 - window_width / 2)
        center_y = int(screen_height / 2 - window_height / 2)
        self.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")

    def __change(self, frame: type[AppFrame], **kwargs):
        if self.__frame:
            self.__frame.pack_forget()
            self.__frame.exit()
        self.__frame = frame(self, **kwargs)
        self.__frame.pack()

    def switch_to_sync(self):
        from idat_sync.ui.sync_frame import SyncFrame

        if credentials := self.auth.get_credentials():
            self.__change(SyncFrame, credentials=credentials, config=self.config)

    def switch_to_login(self):
        from idat_sync.ui.login_frame import LoginFrame

        self.__change(LoginFrame, auth=self.auth)


def main():
    config = load_config()
    sys.stdout = io.StringIO()
    sys.stderr = open(config.log_file, mode="+w", encoding="utf-8")
    app = MainApp(config=config)
    app.run()
    sys.stderr.close()
