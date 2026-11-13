from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from idat_sync.application.ports import (
    CredentialStorePort,
    Credentials,
    MoodleTokenPort,
    SharepointAuthPort,
)


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

    async def login(
        self,
        username: str,
        password: str,
        get_mfa_code: Callable[[], Awaitable[str]],
    ) -> bool:
        clean_username = username.split("@", 1)[0]
        token, sharepoint_storage_state = await asyncio.gather(
            asyncio.to_thread(
                self.moodle_auth.fetch_token, clean_username, password
            ),
            self.sharepoint_auth.fetch_storage_state(
                clean_username, password, get_mfa_code
            ),
        )
        if sharepoint_storage_state is None or not token:
            return False

        credentials = Credentials(
            sharepoint_storage_state=sharepoint_storage_state,
            token=token,
        )
        saved = await asyncio.to_thread(self.credential_store.save, credentials)
        if saved:
            print(f"Success: User '{clean_username}' is now logged in.")
        return saved

    def logout(self) -> None:
        self.credential_store.clear()

    def is_authenticated(self) -> bool:
        return self.get_credentials() is not None

    def get_credentials(self) -> Credentials | None:
        return self.credential_store.load()


__all__ = ["AuthService"]
