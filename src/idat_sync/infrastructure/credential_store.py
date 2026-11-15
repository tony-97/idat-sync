import json
import logging
import os
from pathlib import Path
from typing import cast

from idat_sync.application.ports import Credentials

_logger = logging.getLogger(__name__)


class JsonCredentialStore:
    """JSON file store for persisted user Credentials."""

    def __init__(self, credentials_file: str | Path) -> None:
        self.credentials_file = Path(credentials_file)

    def load(self) -> Credentials | None:
        try:
            with open(self.credentials_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

        if not isinstance(data, dict):
            return None
        token = data.get("token")
        storage_state = data.get("sharepoint_storage_state")
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(storage_state, dict)
        ):
            return None
        return cast(Credentials, data)

    def save(self, credentials: Credentials) -> bool:
        try:
            self.credentials_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.credentials_file, "w", encoding="utf-8") as f:
                json.dump(credentials, f)
            os.chmod(self.credentials_file, 0o600)
            return True
        except OSError as e:
            _logger.error("Could not create credentials file: %s", e)
            return False

    def clear(self) -> None:
        try:
            if self.credentials_file.exists():
                self.credentials_file.unlink()
                _logger.info("User has been logged out.")
            else:
                _logger.info("Already logged out (no credentials file found).")
        except OSError as e:
            _logger.error("Could not remove credentials file: %s", e)
