import time
from typing import Any, BinaryIO
import requests

from idat_sync.application.ports import CredentialsExpiredError

REQUEST_HEADER = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 7.1.1; Moto G Play Build/NPIS26.48-43-2; wv) AppleWebKit/537.36"
        + " (KHTML, like Gecko) Version/4.0 Chrome/71.0.3578.99 Mobile Safari/537.36 MoodleMobile"
    ),
    "Content-Type": "application/x-www-form-urlencoded",
}


class MoodleTokenClient:
    """Low-level HTTP client for obtaining raw Moodle mobile login payloads."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def request_token(self, username: str, password: str) -> dict[str, Any]:
        payload = {
            "username": username,
            "password": password,
            "service": "moodle_mobile_app",
        }
        response = requests.post(
            f"{self.base_url}/login/token.php",
            data=payload,
            timeout=120,
            headers=REQUEST_HEADER,
        )
        response.raise_for_status()
        return response.json()


class MoodleClient:
    """Low-level client communicating directly with the Moodle Web Services API.

    This client handles HTTP transport, authentication token passing, rate-limiting,
    and returns raw vendor JSON payloads without leaking domain logic.
    """

    def __init__(
        self,
        token: str,
        base_url: str,
        request_delay: float,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.request_delay = request_delay

    def call_ws(self, function: str, **kwargs: Any) -> Any:
        """Call a Moodle web service endpoint with the provided parameters."""
        url = f"{self.base_url}/webservice/rest/server.php"
        payload = {
            "wstoken": self.token,
            "wsfunction": function,
            "moodlewsrestformat": "json",
            **kwargs,
        }
        response = requests.post(url, data=payload, timeout=45)
        if response.status_code in (401, 403):
            raise CredentialsExpiredError(
                f"HTTP {response.status_code}: Moodle credentials expired."
            )
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict) and result.get("exception"):
            error_code = result.get("errorcode")
            msg = f"{error_code}: {result.get('message')}"
            if error_code == "invalidtoken":
                raise CredentialsExpiredError(msg)
            raise RuntimeError(msg)
        time.sleep(self.request_delay)
        return result

    def fetch_enrolled_courses(
        self, classification: str = "all", limit: int = 100, offset: int = 0
    ) -> list[dict]:
        """Fetch raw enrolled courses via core_course_get_enrolled_courses_by_timeline_classification."""
        result = self.call_ws(
            "core_course_get_enrolled_courses_by_timeline_classification",
            classification=classification,
            limit=limit,
            offset=offset,
        )
        if isinstance(result, dict):
            return result.get("courses", [])
        return []

    def fetch_assignments(self) -> list[dict]:
        """Fetch raw assignments for all enrolled courses via mod_assign_get_assignments."""
        result = self.call_ws("mod_assign_get_assignments")
        if isinstance(result, dict):
            return result.get("courses", [])
        return []

    def fetch_course_contents(self, course_id: int) -> list[dict]:
        """Fetch raw course contents/sections via core_course_get_contents."""
        result = self.call_ws(
            "core_course_get_contents",
            courseid=course_id,
        )
        if isinstance(result, list):
            return result
        return []

    def download_file(self, url: str, dest: BinaryIO) -> None:
        """Stream an authenticated Moodle file into dest and sleep request_delay."""
        with requests.get(
            url,
            params={"wstoken": self.token, "token": self.token},
            stream=True,
            allow_redirects=True,
            timeout=60,
            headers=REQUEST_HEADER,
        ) as response:
            if response.status_code == 401:
                raise CredentialsExpiredError(
                    "401 Unauthorized: your session cookie/token is missing/expired."
                )
            if response.status_code == 403:
                raise CredentialsExpiredError(
                    "403 Forbidden: your session cookie is missing/expired."
                )
            response.raise_for_status()
            for part in response.iter_content(chunk_size=1024 * 256):
                if part:
                    dest.write(part)
            time.sleep(self.request_delay)
