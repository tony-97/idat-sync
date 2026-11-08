import asyncio
import tkinter as tk
from typing import cast

from async_tkinter_loop import async_handler

from idat_sync.infrastructure.auth import AuthProvider
from idat_sync.ui.app import AppFrame, MainApp
from idat_sync.ui.dialogs import LoadingDialog


class LoginFrame(AppFrame):
    def __init__(self, master: MainApp, auth: AuthProvider, **kwargs):
        tk.Frame.__init__(self, master, **kwargs)
        self.auth = auth
        self.auth.logout()
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

        login_button = tk.Button(
            self, text="Login", command=async_handler(self.do_login)
        )
        self.is_login = tk.BooleanVar(value=False)
        self.is_login.trace(
            "w",
            lambda *args: login_button.config(
                state="disabled" if self.is_login.get() else "normal"
            ),
        )
        login_button.pack(pady=10)

    # TODO add loading dialog until MFA auth
    async def do_login(self, event=None):
        self.is_login.set(True)
        if (user := self.user_entry.get()) and (
            password := self.pass_entry.get()
        ):
            try:
                login_flow_ended = asyncio.Event()
                login_task = asyncio.create_task(
                    self.auth.login(user, password, login_flow_ended)
                )
                await login_flow_ended.wait()
                loading_dialog = LoadingDialog(self.master)
                if await login_task:
                    cast(MainApp, self.master).switch_to_sync()
                loading_dialog.remove()
                self.is_login.set(False)
            except Exception:
                ...
        else:
            ...
