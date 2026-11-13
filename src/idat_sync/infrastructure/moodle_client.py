import time
from typing import Any
import requests


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
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict) and result.get("exception"):
            raise RuntimeError(f"{result.get('errorcode')}: {result.get('message')}")
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
