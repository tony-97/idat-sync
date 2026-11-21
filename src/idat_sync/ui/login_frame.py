"""Pure-Python login component controller handling credentials and inline MFA."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from idat_sync.application.ports import CredentialStoreError, IDATSyncError
from idat_sync.ui.base import AppFrame

if TYPE_CHECKING:
    from idat_sync.application.auth_service import AuthService
    from idat_sync.ui.app import MainApp

_logger = logging.getLogger(__name__)


class LoginFrame(AppFrame):
    """Component controller for interactive user login and async MFA entry."""

    def __init__(self, app: MainApp, auth: AuthService) -> None:
        """Initialize login state and clear any existing stored credentials."""
        super().__init__()
        self.app = app
        self.auth = auth
        self.username = ""
        self.password = ""
        self.error = ""
        self.is_login = False
        self._mfa_future: asyncio.Future[str] | None = None
        try:
            self.auth.logout()
        except CredentialStoreError as exc:
            self.error = str(exc)

    def _notify(self) -> None:
        if hasattr(self.app, "push_state"):
            self.app.push_state()

    def _set_is_login(self, value: bool) -> None:  # noqa: FBT001
        self.is_login = value
        self._notify()

    @property
    def can_login(self) -> bool:
        """Return True only when trimmed username, password, and idle state hold."""
        return bool(self.username.strip()) and bool(self.password) and not self.is_login

    @property
    def mfa_open(self) -> bool:
        """Return True while an MFA code prompt is awaiting user input."""
        return self._mfa_future is not None and not self._mfa_future.done()

    def to_dict(self) -> dict[str, Any]:
        """Serialize login state for the WebView frontend."""
        return {
            "view": "login",
            "username": self.username,
            "password": self.password,
            "error": self.error,
            "is_login": self.is_login,
            "can_login": self.can_login,
            "mfa_open": self.mfa_open,
        }

    async def get_mfa_code(self) -> str:
        """Open the inline MFA prompt and await the submitted code."""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._mfa_future = future
        self._notify()
        try:
            return await future
        finally:
            if self._mfa_future is future:
                self._mfa_future = None
            self._notify()

    def submit_mfa(self, code: str) -> None:
        """Resolve the pending MFA prompt with ``code``."""
        if self._mfa_future is not None and not self._mfa_future.done():
            self._mfa_future.set_result(code)
            self._notify()

    def cancel_mfa(self) -> None:
        """Dismiss the pending MFA prompt with an empty code."""
        self.submit_mfa("")

    async def aclose(self) -> None:
        """Cancel active login task and dismiss any open MFA prompt."""
        await super().aclose()
        self.cancel_mfa()

    async def do_login(
        self,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        """Attempt login with current inputs and switch to SyncFrame on success."""
        if username is not None:
            self.username = username
        if password is not None:
            self.password = password
        if (
            self.is_login
            or not (user := self.username.strip())
            or not (pwd := self.password)
        ):
            return
        self.error = ""
        async with self._busy(self._set_is_login):
            try:
                await self.auth.login(user, pwd, self.get_mfa_code)
            except IDATSyncError as exc:
                _logger.error("Login error: %s", exc)  # noqa: TRY400
                self.error = str(exc)
            else:
                self.app.switch_to_sync()
