"""Login frame handling Moodle/SharePoint credentials and MFA popup."""

from __future__ import annotations

import asyncio
import logging
import tkinter as tk
from typing import TYPE_CHECKING, Any, cast

from async_tkinter_loop import async_handler

from idat_sync.ui.base import AppFrame

if TYPE_CHECKING:
    from idat_sync.application.auth_service import AuthService
    from idat_sync.ui.app import MainApp

_logger = logging.getLogger(__name__)


class LoginFrame(AppFrame):  # pylint: disable=too-many-ancestors,too-many-instance-attributes
    """Tkinter frame for interactive user login and MFA entry."""

    def __init__(
        self,
        master: MainApp,
        auth: AuthService,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        """Initialize the login form widgets and input validation traces."""
        super().__init__(master, **kwargs)
        self.auth = auth
        self.auth.logout()
        window_width = 300
        window_height = 185
        master.center_window(window_width, window_height)
        master.title("IDAT Sync Login")

        tk.Label(self, text="Username:").pack(pady=5)
        self.user_var = tk.StringVar()
        self.user_entry = tk.Entry(self, width=30, textvariable=self.user_var)
        self.user_entry.pack()
        self.user_entry.focus()

        tk.Label(self, text="Password:").pack(pady=5)
        self.pass_var = tk.StringVar()
        self.pass_entry = tk.Entry(self, show="*", width=30, textvariable=self.pass_var)
        self.pass_entry.pack()
        self.pass_entry.bind("<Return>", async_handler(self.do_login))

        self.error_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.error_var, fg="red", wraplength=270).pack(
            pady=(5, 0)
        )

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

    def validate_buttons(self, *_args: object) -> None:
        """Enable the login button only when inputs are present and idle."""
        if not self.winfo_exists():
            return
        has_login_inputs = bool(self.user_var.get().strip()) and bool(
            self.pass_var.get()
        )
        state = "normal" if not self.is_login.get() and has_login_inputs else "disabled"
        self.login_button.config(state=state)

    async def get_mfa_code(self) -> str:
        """Display a modal MFA popup and await the submitted 6-digit code."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        popup = tk.Toplevel(self.master)
        popup.title("MFA Required")
        popup.geometry("250x150")

        tk.Label(popup, text="Enter your 6-digit MFA code:").pack(pady=10)

        entry = tk.Entry(popup)
        entry.pack(pady=5)

        def on_submit() -> None:
            if not future.done():
                future.set_result(entry.get())
            popup.destroy()

        def on_close() -> None:
            if not future.done():
                future.set_result("")
            popup.destroy()

        entry.bind("<Return>", lambda _e: on_submit())
        popup.protocol("WM_DELETE_WINDOW", on_close)
        tk.Button(popup, text="Submit", command=on_submit).pack(pady=10)

        try:
            return await future
        finally:
            if popup.winfo_exists():
                popup.destroy()

    async def do_login(self, _event: object = None) -> None:
        """Attempt login with current form inputs and switch to SyncFrame."""
        if (
            not self.is_login.get()
            and (user := self.user_var.get().strip())
            and (password := self.pass_var.get())
        ):
            self.error_var.set("")
            self.is_login.set(True)
            try:
                login_task = asyncio.create_task(
                    self.auth.login(user, password, self.get_mfa_code)
                )
                self._active_task = login_task
                if await login_task:
                    cast("MainApp", self.master).switch_to_sync()
                else:
                    self.error_var.set(
                        "Login failed. Check your credentials or MFA and try again."
                    )
            except Exception as exc:
                errors = exc.exceptions if isinstance(exc, ExceptionGroup) else (exc,)
                for err in errors:
                    _logger.error("Login error: %s", err)  # noqa: TRY400
                self.error_var.set(
                    "; ".join(str(err) for err in errors) or "Login failed."
                )
            finally:
                self._active_task = None
                if self.winfo_exists():
                    self.is_login.set(False)
