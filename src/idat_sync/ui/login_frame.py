import asyncio
import sys
import tkinter as tk
from typing import cast

from async_tkinter_loop import async_handler

from idat_sync.application.auth_service import AuthService
from idat_sync.ui.app import AppFrame, MainApp


class LoginFrame(AppFrame):
    def __init__(self, master: MainApp, auth: AuthService, **kwargs):
        tk.Frame.__init__(self, master, **kwargs)
        self.auth = auth
        self.auth.logout()
        window_width = 300
        window_height = 150
        master.center_window(window_width, window_height)
        master.title("IDAT Sync Login")

        tk.Label(self, text="Username:").pack(pady=5)
        self.user_var = tk.StringVar()
        self.user_entry = tk.Entry(
            self, width=30, textvariable=self.user_var
        )
        self.user_entry.pack()
        self.user_entry.focus()

        tk.Label(self, text="Password:").pack(pady=5)
        self.pass_var = tk.StringVar()
        self.pass_entry = tk.Entry(
            self, show="*", width=30, textvariable=self.pass_var
        )
        self.pass_entry.pack()
        self.pass_entry.bind("<Return>", self.do_login)

        self.login_button = tk.Button(
            self,
            text="Login",
            command=async_handler(self.do_login),
            state="disabled",
        )
        self.is_login = tk.BooleanVar(value=False)
        self.user_var.trace_add("write", self.validate_buttons)
        self.pass_var.trace_add("write", self.validate_buttons)
        self.is_login.trace_add("write", self.validate_buttons)
        self.login_button.pack(pady=10)

    def validate_buttons(self, *args):
        has_credentials = bool(self.user_var.get().strip()) and bool(
            self.pass_var.get()
        )
        state = (
            "normal"
            if not self.is_login.get() and has_credentials
            else "disabled"
        )
        self.login_button.config(state=state)

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
        if (
            not self.is_login.get()
            and (user := self.user_var.get().strip())
            and (password := self.pass_var.get())
        ):
            self.is_login.set(True)
            try:
                login_task = asyncio.create_task(
                    self.auth.login(user, password, self.get_mfa_code)
                )
                if await login_task:
                    cast(MainApp, self.master).switch_to_sync()
                # loading_dialog.remove()
            except Exception as e:
                errors = e.exceptions if isinstance(e, ExceptionGroup) else (e,)
                for err in errors:
                    print(f"Error: {err}", file=sys.stderr)
            finally:
                self.is_login.set(False)
        else:
            ...
