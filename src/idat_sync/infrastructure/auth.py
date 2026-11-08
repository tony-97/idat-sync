import os
import json
import asyncio
import requests
from typing import TypedDict

from playwright._impl._api_structures import StorageState

from idat_sync.utils import get_sharepoint_cookies, MOODLE_IDAT, RQ_HEADER


class Credentials(TypedDict):
    sharepoint_storage_state: StorageState
    token: str


class AuthProvider:
    def __init__(self, session_file="./session.json"):
        self.session_file = session_file

    # TODO: Handle exceptions from sharepoint and moodle
    async def login(
        self, username: str, password: str, login_flow_ended: asyncio.Event
    ):
        username = username.split("@", 1)[0]
        token, sharepoint_storage_state = await asyncio.gather(
            asyncio.to_thread(self.__get_moodle_token, username, password),
            get_sharepoint_cookies(username, password, login_flow_ended),
        )

        if sharepoint_storage_state is None:
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
        response = requests.get(
            f"{MOODLE_IDAT}/login/token.php",
            params={
                "username": user,
                "password": password,
                "service": "moodle_mobile_app",
            },
            timeout=60,
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
