import asyncio
import json
import os

from typing import TypedDict

from idat_sync.utils import get_sharepoint_cookies, get_token

from playwright._impl._api_structures import StorageState


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
            asyncio.to_thread(get_token, username, password),
            get_sharepoint_cookies(username, password, login_flow_ended),
        )

        if sharepoint_storage_state == None:
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

    def logout(self):
        """
        Logs the user out by deleting the session file.
        """
        try:
            if os.path.exists(self.session_file):
                os.remove(self.session_file)
                print("Success: User has been logged out.")
            else:
                print("Info: Already logged out (no session file found).")
        except OSError as e:
            print(f"Error: Could not remove session file: {e}")

    def is_authenticated(self):
        """
        Checks if a user is currently logged in.

        Returns:
            bool: True if the session file exists, False otherwise.
        """
        return os.path.exists(self.session_file)

    def get_credentials(self):
        """
        Gets the username of the currently logged-in user.

        Returns:
            str or None: The username if logged in, None otherwise.
        """
        if not self.is_authenticated():
            return None

        try:
            with open(self.session_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except IOError:
            # Session file exists but is unreadable
            return None
