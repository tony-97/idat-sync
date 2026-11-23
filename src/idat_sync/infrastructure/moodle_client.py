"""Low-level HTTP clients for Moodle token exchange and Web Services API."""

from http import HTTPStatus
from typing import Any, BinaryIO, Self

import httpx

from idat_sync.application.ports import (
    CredentialsExpiredError,
    ProgressCallback,
    RemoteServiceError,
)
from idat_sync.infrastructure.rate_limiter import AsyncRateLimiter

REQUEST_HEADER = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 7.1.1; Moto G Play Build/NPIS26.48-43-2; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/71.0.3578.99 "
        "Mobile Safari/537.36 MoodleMobile"
    ),
    "Content-Type": "application/x-www-form-urlencoded",
}


class MoodleTokenClient:  # pylint: disable=too-few-public-methods
    """Low-level HTTP client for obtaining raw Moodle mobile login payloads."""

    def __init__(self, base_url: str) -> None:
        """Initialize the token client for ``base_url``."""
        self.base_url = base_url.rstrip("/")

    async def request_token(self, username: str, password: str) -> dict[str, Any]:
        """Request a Moodle mobile token payload for ``username``."""
        payload = {
            "username": username,
            "password": password,
            "service": "moodle_mobile_app",
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/login/token.php",
                    data=payload,
                    timeout=120,
                    headers=REQUEST_HEADER,
                )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise RemoteServiceError(
                f"HTTP {exc.response.status_code} from Moodle login endpoint.",
                service="Moodle",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            raise RemoteServiceError(
                f"Could not connect to Moodle: {exc}",
                service="Moodle",
                is_network=True,
            ) from exc
        except ValueError as exc:
            raise RemoteServiceError(
                "Invalid JSON response from Moodle login endpoint.",
                service="Moodle",
            ) from exc


class MoodleClient:
    """Low-level client communicating directly with the Moodle Web Services API.

    This client handles HTTP transport, authentication token passing, rate-limiting,
    and returns raw vendor JSON payloads without leaking domain logic.
    """

    def __init__(
        self,
        token: str,
        base_url: str,
        rate_limiter: AsyncRateLimiter,
    ) -> None:
        """Initialize the Moodle API client with token, base URL, and limiter."""
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.rate_limiter = rate_limiter
        self._client = httpx.AsyncClient()

    async def aclose(self) -> None:
        """Close the underlying HTTPX client."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        """Enter the async context manager."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit the async context manager and close the client."""
        await self.aclose()

    async def call_ws(self, function: str, **kwargs: Any) -> Any:  # noqa: ANN401
        """Call a Moodle web service endpoint with the provided parameters."""
        url = f"{self.base_url}/webservice/rest/server.php"
        payload = {
            "wstoken": self.token,
            "wsfunction": function,
            "moodlewsrestformat": "json",
            **kwargs,
        }
        async with self.rate_limiter:
            try:
                response = await self._client.post(url, data=payload, timeout=45)
                if response.status_code in (401, 403):
                    raise CredentialsExpiredError(
                        f"HTTP {response.status_code}: Moodle credentials expired.",
                        service="Moodle",
                    )
                response.raise_for_status()
                result = response.json()
            except httpx.HTTPStatusError as exc:
                raise RemoteServiceError(
                    f"HTTP {exc.response.status_code} "
                    f"from Moodle web service ({function}).",
                    service="Moodle",
                    status_code=exc.response.status_code,
                ) from exc
            except httpx.RequestError as exc:
                raise RemoteServiceError(
                    f"Moodle web service request failed ({function}): {exc}",
                    service="Moodle",
                    is_network=True,
                ) from exc
            except ValueError as exc:
                raise RemoteServiceError(
                    f"Invalid JSON response from Moodle web service ({function}).",
                    service="Moodle",
                ) from exc

            if isinstance(result, dict) and result.get("exception"):
                error_code = result.get("errorcode")
                msg = f"{error_code}: {result.get('message')}"
                if error_code == "invalidtoken":
                    raise CredentialsExpiredError(msg, service="Moodle")
                raise RemoteServiceError(msg, service="Moodle")
            return result

    async def fetch_enrolled_courses(
        self, classification: str = "all", limit: int = 100, offset: int = 0
    ) -> list[dict]:
        """Fetch raw enrolled courses via Moodle's timeline classification WS."""
        courses: list[dict] = []
        current_offset = offset
        while True:
            result = await self.call_ws(
                "core_course_get_enrolled_courses_by_timeline_classification",
                classification=classification,
                limit=limit,
                offset=current_offset,
            )
            if not isinstance(result, dict):
                break
            page = result.get("courses", [])
            if not page:
                break
            courses.extend(page)
            next_offset = result.get("nextoffset")
            if (
                isinstance(next_offset, bool)
                or not isinstance(next_offset, int)
                or next_offset <= current_offset
            ):
                break
            current_offset = next_offset
        return courses

    async def fetch_assignments(self) -> list[dict]:
        """Fetch raw assignments for enrolled courses via mod_assign_get_assignments."""
        result = await self.call_ws("mod_assign_get_assignments")
        if isinstance(result, dict):
            return result.get("courses", [])
        return []

    async def fetch_course_contents(self, course_id: int) -> list[dict]:
        """Fetch raw course contents/sections via core_course_get_contents."""
        result = await self.call_ws(
            "core_course_get_contents",
            courseid=course_id,
        )
        if isinstance(result, list):
            return result
        return []

    @staticmethod
    def _parse_content_length(headers: Any) -> int | None:  # noqa: ANN401
        raw_len = headers.get("Content-Length")
        if isinstance(raw_len, str) and raw_len.strip().isdigit():
            val = int(raw_len)
            return val if val > 0 else None
        return None

    async def download_file(
        self,
        url: str,
        dest: BinaryIO,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        """Stream an authenticated Moodle file into dest under the rate limiter."""
        target_url = httpx.URL(url).copy_merge_params(
            {"wstoken": self.token, "token": self.token}
        )
        safe_url = target_url.copy_remove_param("wstoken").copy_remove_param("token")
        async with self.rate_limiter:
            try:
                async with self._client.stream(
                    "GET",
                    target_url,
                    follow_redirects=True,
                    timeout=60,
                    headers=REQUEST_HEADER,
                ) as response:
                    if response.status_code == HTTPStatus.UNAUTHORIZED:
                        raise CredentialsExpiredError(
                            "401 Unauthorized: your session cookie/token "
                            "is missing/expired.",
                            service="Moodle",
                        )
                    if response.status_code == HTTPStatus.FORBIDDEN:
                        raise CredentialsExpiredError(
                            "403 Forbidden: your session cookie is missing/expired.",
                            service="Moodle",
                        )
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        err_url = (
                            (
                                exc.request.url
                                if isinstance(exc.request.url, httpx.URL)
                                else target_url
                            )
                            .copy_remove_param("wstoken")
                            .copy_remove_param("token")
                        )
                        raise RemoteServiceError(
                            f"HTTP {response.status_code} for url '{err_url}'",
                            service="Moodle",
                            status_code=response.status_code,
                        ) from None
                    total = self._parse_content_length(response.headers)
                    downloaded = 0
                    async for part in response.aiter_bytes(chunk_size=1024 * 16):
                        if part:
                            dest.write(part)
                            downloaded += len(part)
                            if on_progress is not None:
                                on_progress(downloaded, total)
            except httpx.RequestError:
                raise RemoteServiceError(
                    f"Network error downloading '{safe_url}'",
                    service="Moodle",
                    is_network=True,
                ) from None
