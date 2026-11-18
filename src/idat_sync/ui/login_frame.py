"""Login frame handling Moodle/SharePoint credentials and MFA popup."""

from __future__ import annotations

import logging
import tkinter as tk
from typing import TYPE_CHECKING, Any

from async_tkinter_loop import async_handler

from idat_sync.application.ports import CredentialStoreError, IDATSyncError
from idat_sync.ui.base import AppFrame
from idat_sync.ui.mfa_dialog import ask_mfa_code

if TYPE_CHECKING:
    from idat_sync.application.auth_service import AuthService
    from idat_sync.ui.app import MainApp

_logger = logging.getLogger(__name__)

_WINDOW_WIDTH = 300
_WINDOW_HEIGHT = 185
_ENTRY_WIDTH = 30
_ERROR_WRAP_LENGTH = 270


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
        self.app = master
        self.auth = auth
        self.error_var = tk.StringVar(value="")
        try:
            self.auth.logout()
        except CredentialStoreError as exc:
            self.error_var.set(str(exc))
        self.app.center_window(_WINDOW_WIDTH, _WINDOW_HEIGHT)
        self.app.title("IDAT Sync Login")

        tk.Label(self, text="Username:").pack(pady=5)
        self.user_var = tk.StringVar()
        self.user_entry = tk.Entry(self, width=_ENTRY_WIDTH, textvariable=self.user_var)
        self.user_entry.pack()
        self.user_entry.focus()

        tk.Label(self, text="Password:").pack(pady=5)
        self.pass_var = tk.StringVar()
        self.pass_entry = tk.Entry(
            self, show="*", width=_ENTRY_WIDTH, textvariable=self.pass_var
        )
        self.pass_entry.pack()
        self.pass_entry.bind("<Return>", async_handler(self.do_login))

        tk.Label(
            self, textvariable=self.error_var, fg="red", wraplength=_ERROR_WRAP_LENGTH
        ).pack(pady=(5, 0))

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

    def validate_buttons(self, *_: object) -> None:
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
        return await ask_mfa_code(self.master)

    async def do_login(self, *_: object) -> None:
        """Attempt login with current form inputs and switch to SyncFrame."""
        if (
            self.is_login.get()
            or not (user := self.user_var.get().strip())
            or not (password := self.pass_var.get())
        ):
            return
        self.error_var.set("")
        async with self._busy(self.is_login):
            try:
                await self.auth.login(user, password, self.get_mfa_code)
            except IDATSyncError as exc:
                _logger.error("Login error: %s", exc)  # noqa: TRY400
                self.error_var.set(str(exc))
            else:
                self.app.switch_to_sync()
