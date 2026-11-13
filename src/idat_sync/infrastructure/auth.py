import os
import json
import asyncio
from pathlib import Path
import requests
from typing import Awaitable, Callable, TypedDict

from playwright._impl._api_structures import StorageState

from idat_sync.utils import get_sharepoint_cookies, RQ_HEADER


class Credentials(TypedDict):
    sharepoint_storage_state: StorageState
    token: str


class AuthProvider:
    def __init__(
        self,
        session_file: str | Path,
        moodle_base_url: str,
        sharepoint_host: str,
        sharepoint_personal_host: str,
        email_domain: str,
    ):
        self.session_file = session_file
        self.moodle_base_url = moodle_base_url.rstrip("/")
        self.sharepoint_host = sharepoint_host
        self.sharepoint_personal_host = sharepoint_personal_host
        self.email_domain = email_domain

    # TODO: Handle exceptions from sharepoint and moodle
    async def login(
        self,
        username: str,
        password: str,
        login_flow_ended: asyncio.Event,
        get_mfa_code: Callable[[], Awaitable[str]],
    ):
        username = username.split("@", 1)[0]
        token, sharepoint_storage_state = await asyncio.gather(
            asyncio.to_thread(self.__get_moodle_token, username, password),
            get_sharepoint_cookies(
                username,
                password,
                login_flow_ended,
                get_mfa_code,
                sharepoint_host=self.sharepoint_host,
                sharepoint_personal_host=self.sharepoint_personal_host,
                email_domain=self.email_domain,
            ),
        )

        if sharepoint_storage_state is None or not token:
            return False
        # --- Success ---
        # Create the session file to mark the user as authenticated
        credentials = Credentials(
            sharepoint_storage_state=sharepoint_storage_state, token=token
        )

        def save_credentials():
            try:
                with open(self.session_file, "w", encoding="utf-8") as f:
                    json.dump(credentials, f)
                print(f"Success: User '{username}' is now logged in.")
                return True
            except IOError as e:
                print(f"Error: Could not create session file: {e}")
                return False

        return await asyncio.to_thread(save_credentials)

    # TODO: raise exceptions
    def __get_moodle_token(self, user: str, password: str) -> str:
        payload = {
            "username": user,
            "password": password,
            "service": "moodle_mobile_app",
        }
        response = requests.post(
            f"{self.moodle_base_url}/login/token.php",
            data=payload,
            timeout=120,
            headers=RQ_HEADER,
        )
        response.raise_for_status()
        login = response.json()
        if "error" in login:
            print(f"[!] Token error: {login.get('error')}")
        token = login.get("token")
        if not token:
            print(
                "[!] Could not obtain token. Check URL/credentials or if mobile service is enabled."
            )
        return token

    def logout(self):
        """Logs the user out by deleting the session file."""
        try:
            if os.path.exists(self.session_file):
                os.remove(self.session_file)
                print("Success: User has been logged out.")
            else:
                print("Info: Already logged out (no session file found).")
        except OSError as e:
            print(f"Error: Could not remove session file: {e}")

    def is_authenticated(self):
        """Checks if a user is currently logged in."""
        return os.path.exists(self.session_file)

    def get_credentials(self):
        """Gets the stored credentials of the currently logged-in user."""
        if not self.is_authenticated():
            return None

        try:
            with open(self.session_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except IOError:
            return None
