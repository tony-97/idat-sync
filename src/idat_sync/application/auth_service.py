"""Authentication service coordinating Moodle and SharePoint login."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from idat_sync.application.ports import (
    AuthenticationError,
    Credentials,
    CredentialStorePort,
    IDATSyncError,
    MoodleTokenPort,
    SharepointAuthPort,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_logger = logging.getLogger(__name__)


class AuthService:
    """Application service orchestrating login and local Credentials storage."""

    def __init__(
        self,
        moodle_auth: MoodleTokenPort,
        sharepoint_auth: SharepointAuthPort,
        credential_store: CredentialStorePort,
    ) -> None:
        """Initialize the authentication service with its required ports."""
        self.moodle_auth = moodle_auth
        self.sharepoint_auth = sharepoint_auth
        self.credential_store = credential_store

    async def login(
        self,
        username: str,
        password: str,
        get_mfa_code: Callable[[], Awaitable[str]],
    ) -> None:
        """Log in to Moodle and SharePoint concurrently and persist Credentials."""
        moodle_username = username.split("@", 1)[0]
        idat_error_group: ExceptionGroup[IDATSyncError] | None = None
        try:
            async with asyncio.TaskGroup() as tg:
                moodle_task = tg.create_task(
                    self.moodle_auth.fetch_token(moodle_username, password),
                    name="moodle_auth",
                )
                sharepoint_task = tg.create_task(
                    self.sharepoint_auth.fetch_storage_state(
                        username, password, get_mfa_code
                    ),
                    name="sharepoint_auth",
                )
        except* IDATSyncError as eg:
            idat_error_group = eg
        if idat_error_group is not None:
            raise idat_error_group.exceptions[0] from idat_error_group
        token = moodle_task.result()
        sharepoint_storage_state = sharepoint_task.result()
        if not sharepoint_storage_state or not token:
            raise AuthenticationError(
                "Authentication failed: empty token or SharePoint storage state."
            )

        await asyncio.to_thread(
            self.credential_store.save,
            Credentials(
                sharepoint_storage_state=sharepoint_storage_state,
                token=token,
            ),
        )
        _logger.info("User '%s' is now logged in.", moodle_username)

    def logout(self) -> None:
        """Clear persisted Credentials."""
        self.credential_store.clear()

    def is_authenticated(self) -> bool:
        """Return whether stored Credentials currently exist."""
        return self.get_credentials() is not None

    def get_credentials(self) -> Credentials | None:
        """Load persisted Credentials, or return None."""
        return self.credential_store.load()


__all__ = ["AuthService"]
