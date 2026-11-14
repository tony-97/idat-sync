from __future__ import annotations

import asyncio
import io
import sys
import tkinter as tk

from async_tkinter_loop import async_mainloop

from idat_sync.bootstrap import create_auth_service
from idat_sync.config import SyncConfig, load_config


class AppFrame(tk.Frame):
    def exit(self): ...


class MainApp(tk.Tk):
    def __init__(self, config: SyncConfig):
        tk.Tk.__init__(self)
        self.loop = asyncio.new_event_loop()
        self.config = config
        self.auth = create_auth_service(self.config)
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
