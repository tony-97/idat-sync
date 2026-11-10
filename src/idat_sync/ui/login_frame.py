import asyncio
import sys
import tkinter as tk
from typing import cast

from async_tkinter_loop import async_handler

from idat_sync.infrastructure.auth import AuthProvider
from idat_sync.ui.app import AppFrame, MainApp


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

    async def get_mfa_code_ui(self):
        """Creates a Tkinter popup that pauses the async function until submitted."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        # Create the popup window
        popup = tk.Toplevel(self.master)
        popup.title("MFA Required")
        popup.geometry("250x150")

        tk.Label(popup, text="Enter your 6-digit MFA code:").pack(pady=10)

        entry = tk.Entry(popup)
        entry.pack(pady=5)

        # The button callback simply fulfills the Future
        def on_submit():
            code = entry.get()
            future.set_result(code)  # This resumes the async function!
            popup.destroy()  # Close the popup

        tk.Button(popup, text="Submit", command=on_submit).pack(pady=10)

        # Pause the async execution here until the user clicks Submit
        return await future

    async def get_mfa_code(self):
        code = await self.get_mfa_code_ui()
        return code

    # TODO add loading dialog until MFA auth
    async def do_login(self, event=None):
        self.is_login.set(True)
        if (user := self.user_entry.get()) and (
            password := self.pass_entry.get()
        ):
            try:
                login_flow_ended = asyncio.Event()
                login_task = asyncio.create_task(
                    self.auth.login(
                        user, password, login_flow_ended, self.get_mfa_code
                    )
                )
                # loading_dialog = LoadingDialog(self.master)
                # await login_flow_ended.wait()
                if await login_task:
                    cast(MainApp, self.master).switch_to_sync()
                # loading_dialog.remove()
                self.is_login.set(False)
            except Exception as e:
                print(f"Error: {e}", file=sys.stderr)
        else:
            ...
