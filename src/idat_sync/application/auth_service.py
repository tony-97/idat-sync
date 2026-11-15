from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging

from idat_sync.application.ports import (
    CredentialStorePort,
    Credentials,
    MoodleTokenPort,
    SharepointAuthPort,
)

_logger = logging.getLogger(__name__)


class AuthService:
    """Application service orchestrating login and local Credentials storage."""

    def __init__(
        self,
        moodle_auth: MoodleTokenPort,
        sharepoint_auth: SharepointAuthPort,
        credential_store: CredentialStorePort,
    ) -> None:
        self.moodle_auth = moodle_auth
        self.sharepoint_auth = sharepoint_auth
        self.credential_store = credential_store

    async def authenticate(self, username: str, password: str) -> str | None:
        moodle_username = username.split("@", 1)[0]
        return await self.moodle_auth.fetch_token(moodle_username, password)

    async def login(
        self,
        username: str,
        password: str,
        get_mfa_code: Callable[[], Awaitable[str]],
    ) -> bool:
        async with asyncio.TaskGroup() as tg:
            moodle_task = tg.create_task(
                self.authenticate(username, password),
                name="moodle_auth",
            )
            sharepoint_task = tg.create_task(
                self.sharepoint_auth.fetch_storage_state(
                    username, password, get_mfa_code
                ),
                name="sharepoint_auth",
            )
        token = moodle_task.result()
        sharepoint_storage_state = sharepoint_task.result()
        if not sharepoint_storage_state or not token:
            return False

        credentials = Credentials(
            sharepoint_storage_state=sharepoint_storage_state,
            token=token,
        )
        saved = await asyncio.to_thread(self.credential_store.save, credentials)
        if saved:
            _logger.info("User '%s' is now logged in.", username.split("@", 1)[0])
        return saved

    def logout(self) -> None:
        self.credential_store.clear()

    def is_authenticated(self) -> bool:
        return self.get_credentials() is not None

    def get_credentials(self) -> Credentials | None:
        return self.credential_store.load()


__all__ = ["AuthService"]
