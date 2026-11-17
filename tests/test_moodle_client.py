"""Unit tests for MoodleClient and MoodleTokenClient."""

import asyncio
import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from idat_sync.application.ports import CredentialsExpiredError
from idat_sync.infrastructure.moodle_client import (
    REQUEST_HEADER,
    MoodleClient,
    MoodleTokenClient,
)


def _mock_async_client(mock_client_cls, *, post_return=None, stream_cm=None):
    mock_async_client = mock_client_cls.return_value
    mock_async_client.aclose = AsyncMock()
    if post_return is not None:
        mock_async_client.post = AsyncMock(return_value=post_return)
    if stream_cm is not None:
        mock_async_client.stream = MagicMock(return_value=stream_cm)
    return mock_async_client


class TestMoodleClient(unittest.IsolatedAsyncioTestCase):
    """Tests for MoodleClient WS calls, pagination, and file downloads."""

    def setUp(self):
        """Initialize shared MoodleClient test parameters."""
        self.token = "fake_test_token"  # noqa: S105
        self.base_url = "https://aulavirtual.idat.edu.pe"

    def _make_client(self, *, request_delay: float = 30.0) -> MoodleClient:
        return MoodleClient(
            token=self.token,
            base_url=self.base_url,
            request_delay=request_delay,
        )

    @patch(
        "idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock
    )
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_call_ws_success(self, mock_client_cls, mock_sleep):
        """Post WS payload with token and await request_delay on success."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "courses": [{"id": 1, "fullname": "Test Course"}]
        }
        mock_async_client = _mock_async_client(
            mock_client_cls, post_return=mock_response
        )
        client = self._make_client()

        result = await client.call_ws(
            "core_course_get_enrolled_courses", classification="all"
        )

        mock_async_client.post.assert_awaited_once()
        _, kwargs = mock_async_client.post.call_args
        payload = kwargs["data"]
        assert payload["wstoken"] == "fake_test_token"
        assert payload["wsfunction"] == "core_course_get_enrolled_courses"
        assert payload["moodlewsrestformat"] == "json"
        assert payload["classification"] == "all"
        assert result == {"courses": [{"id": 1, "fullname": "Test Course"}]}
        mock_sleep.assert_awaited_once_with(30.0)

    @patch(
        "idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock
    )
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_call_ws_moodle_exception(self, mock_client_cls, mock_sleep):
        """Raise RuntimeError/CredentialsExpiredError when Moodle returns exception."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "exception": "moodle_exception",
            "errorcode": "invalidtoken",
            "message": "Invalid token",
        }
        _mock_async_client(mock_client_cls, post_return=mock_response)
        client = self._make_client()

        with pytest.raises(CredentialsExpiredError, match="invalidtoken") as ctx:
            await client.call_ws("any_function")

        assert "Invalid token" in str(ctx.value)
        mock_sleep.assert_not_awaited()

    @patch(
        "idat_sync.infrastructure.moodle_client.MoodleClient.call_ws",
        new_callable=AsyncMock,
    )
    async def test_fetch_enrolled_courses(self, mock_call_ws):
        """Fetch raw enrolled course dicts from timeline classification WS."""
        raw_courses = [{"id": 101, "fullname": "Course 101"}]
        mock_call_ws.return_value = {"courses": raw_courses}

        async with self._make_client() as client:
            courses = await client.fetch_enrolled_courses()
        mock_call_ws.assert_awaited_once_with(
            "core_course_get_enrolled_courses_by_timeline_classification",
            classification="all",
            limit=100,
            offset=0,
        )
        assert courses == raw_courses
        assert isinstance(courses, list)
        assert isinstance(courses[0], dict)

    @patch(
        "idat_sync.infrastructure.moodle_client.MoodleClient.call_ws",
        new_callable=AsyncMock,
    )
    async def test_fetch_enrolled_courses_paginates_across_nextoffset(
        self, mock_call_ws
    ):
        """Paginate while nextoffset advances and stop on bool or non-advancing."""
        c1 = {"id": 1, "fullname": "Course 1"}
        c2 = {"id": 2, "fullname": "Course 2"}
        c3 = {"id": 3, "fullname": "Course 3"}
        mock_call_ws.side_effect = [
            {"courses": [c1, c2], "nextoffset": 2},
            {"courses": [c3], "nextoffset": 3},
            {"courses": [], "nextoffset": 3},
        ]

        async with self._make_client() as client:
            courses = await client.fetch_enrolled_courses(
                classification="all", limit=2, offset=0
            )

        assert courses == [c1, c2, c3]
        assert [c.kwargs["offset"] for c in mock_call_ws.call_args_list] == [0, 2, 3]

        mock_call_ws.reset_mock()
        mock_call_ws.side_effect = [{"courses": [c1], "nextoffset": True}]
        async with self._make_client() as client:
            courses = await client.fetch_enrolled_courses(limit=1, offset=0)
        assert courses == [c1]
        mock_call_ws.assert_awaited_once()

    @patch(
        "idat_sync.infrastructure.moodle_client.MoodleClient.call_ws",
        new_callable=AsyncMock,
    )
    async def test_fetch_assignments(self, mock_call_ws):
        """Fetch raw assignment dicts via mod_assign_get_assignments."""
        raw_assignments = [
            {"id": 101, "assignments": [{"id": 5, "cmid": 12, "name": "Tarea"}]}
        ]
        mock_call_ws.return_value = {"courses": raw_assignments}

        async with self._make_client() as client:
            result = await client.fetch_assignments()
        mock_call_ws.assert_awaited_once_with("mod_assign_get_assignments")
        assert result == raw_assignments
        assert isinstance(result, list)
        assert isinstance(result[0], dict)

    @patch(
        "idat_sync.infrastructure.moodle_client.MoodleClient.call_ws",
        new_callable=AsyncMock,
    )
    async def test_fetch_course_contents(self, mock_call_ws):
        """Fetch raw course section dicts via core_course_get_contents."""
        raw_sections = [{"id": 1, "name": "General", "modules": []}]
        mock_call_ws.return_value = raw_sections

        async with self._make_client() as client:
            result = await client.fetch_course_contents(course_id=101)
        mock_call_ws.assert_awaited_once_with("core_course_get_contents", courseid=101)
        assert result == raw_sections
        assert isinstance(result, list)
        assert isinstance(result[0], dict)

    @patch(
        "idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock
    )
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_call_ws_uses_injected_base_url_and_request_delay(
        self, mock_client_cls, mock_sleep
    ):
        """Normalize injected base_url and honor injected request_delay."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_async_client = _mock_async_client(
            mock_client_cls, post_return=mock_response
        )

        client = MoodleClient(
            token="tok",  # noqa: S106
            base_url="https://moodle.example.edu/",
            request_delay=2.5,
        )
        await client.call_ws("core_webservice_get_site_info")

        mock_async_client.post.assert_awaited_once()
        args, _ = mock_async_client.post.call_args
        assert args[0] == "https://moodle.example.edu/webservice/rest/server.php"
        mock_sleep.assert_awaited_once_with(2.5)

    @patch(
        "idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock
    )
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_download_file_streams_chunks_and_sleeps(
        self, mock_client_cls, mock_sleep
    ):
        """Stream response bytes into destination and await request_delay."""

        async def _aiter_bytes(chunk_size: int):  # noqa: ARG001  # pylint: disable=unused-argument
            for chunk in (b"chunk1-", b"", b"chunk2"):
                yield chunk

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_bytes = MagicMock(side_effect=_aiter_bytes)
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        stream_cm.__aexit__ = AsyncMock(return_value=None)
        mock_async_client = _mock_async_client(mock_client_cls, stream_cm=stream_cm)
        client = self._make_client()

        dest = io.BytesIO()
        target_url = (
            "https://aulavirtual.idat.edu.pe/pluginfile.php/123/file.pdf"
            "?forcedownload=1&id=42"
        )
        await client.download_file(target_url, dest)

        expected_url = httpx.URL(target_url).copy_merge_params(
            {"wstoken": self.token, "token": self.token}
        )
        mock_async_client.stream.assert_called_once_with(
            "GET",
            expected_url,
            follow_redirects=True,
            timeout=60,
            headers=REQUEST_HEADER,
        )
        mock_response.raise_for_status.assert_called_once_with()
        mock_response.aiter_bytes.assert_called_once_with(chunk_size=1024 * 256)
        assert dest.getvalue() == b"chunk1-chunk2"
        mock_sleep.assert_awaited_once_with(30.0)

    @patch(
        "idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock
    )
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_download_file_exits_stream_before_sleeping(
        self, mock_client_cls, mock_sleep
    ):
        """Close the HTTP stream context before sleeping for request_delay."""
        events: list[str] = []

        async def _aiter_bytes(chunk_size: int):  # noqa: ARG001  # pylint: disable=unused-argument
            yield b"payload"

        async def _on_aexit(*_args):
            events.append("exit_stream")

        async def _on_sleep(_delay):
            events.append("sleep")

        mock_sleep.side_effect = _on_sleep
        mock_response = MagicMock(status_code=200)
        mock_response.aiter_bytes = MagicMock(side_effect=_aiter_bytes)
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        stream_cm.__aexit__ = AsyncMock(side_effect=_on_aexit)
        _mock_async_client(mock_client_cls, stream_cm=stream_cm)

        client = self._make_client(request_delay=1.5)
        await client.download_file(
            "https://aulavirtual.idat.edu.pe/file.pdf", io.BytesIO()
        )

        assert events == ["exit_stream", "sleep"]

    @patch(
        "idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock
    )
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_download_file_raises_on_403(self, mock_client_cls, mock_sleep):
        """Raise CredentialsExpiredError on HTTP 403 without writing or sleeping."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        stream_cm.__aexit__ = AsyncMock(return_value=None)
        _mock_async_client(mock_client_cls, stream_cm=stream_cm)
        client = self._make_client()

        dest = io.BytesIO()
        with pytest.raises(CredentialsExpiredError, match="403 Forbidden"):
            await client.download_file(
                "https://aulavirtual.idat.edu.pe/secret.pdf", dest
            )

        assert dest.getvalue() == b""
        mock_sleep.assert_not_awaited()

    @patch(
        "idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock
    )
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_download_file_propagates_http_error_without_sleeping(
        self, mock_client_cls, mock_sleep
    ):
        """Propagate non-auth HTTPStatusError without sleeping."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=mock_response
        )
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        stream_cm.__aexit__ = AsyncMock(return_value=None)
        _mock_async_client(mock_client_cls, stream_cm=stream_cm)
        client = self._make_client()

        dest = io.BytesIO()
        with pytest.raises(httpx.HTTPStatusError):
            await client.download_file(
                "https://aulavirtual.idat.edu.pe/missing.pdf", dest
            )

        mock_sleep.assert_not_awaited()

    @patch(
        "idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock
    )
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_download_file_redacts_tokens_from_http_status_error(
        self, mock_client_cls, mock_sleep
    ):
        """Strip wstoken and token query params from raised HTTPStatusError."""
        raw_url = "https://aulavirtual.idat.edu.pe/missing.pdf?forcedownload=1"
        target_url = httpx.URL(raw_url).copy_merge_params(
            {"wstoken": "secret-tok", "token": "secret-tok"}
        )
        real_request = httpx.Request("GET", target_url)
        real_response = httpx.Response(404, request=real_request)
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=real_response)
        stream_cm.__aexit__ = AsyncMock(return_value=None)
        _mock_async_client(mock_client_cls, stream_cm=stream_cm)
        client = MoodleClient(
            token="secret-tok",  # noqa: S106
            base_url="https://aulavirtual.idat.edu.pe",
            request_delay=0.0,
        )

        dest = io.BytesIO()
        with pytest.raises(httpx.HTTPStatusError) as ctx:
            await client.download_file(raw_url, dest)

        assert "secret-tok" not in str(ctx.value)
        assert "secret-tok" not in str(ctx.value.request.url)
        assert "forcedownload=1" in str(ctx.value.request.url)
        mock_sleep.assert_not_awaited()

    @patch(
        "idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock
    )
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_call_ws_and_download_map_auth_failures(
        self, mock_client_cls, mock_sleep
    ):
        """Map 401/403 and invalidtoken to CredentialsExpiredError."""
        client = self._make_client()
        for status in (401, 403):
            mock_resp = MagicMock()
            mock_resp.status_code = status
            _mock_async_client(mock_client_cls, post_return=mock_resp)
            with pytest.raises(CredentialsExpiredError):
                await client.call_ws("core_course_get_contents")

            mock_dl = MagicMock()
            mock_dl.status_code = status
            stream_cm = MagicMock()
            stream_cm.__aenter__ = AsyncMock(return_value=mock_dl)
            stream_cm.__aexit__ = AsyncMock(return_value=None)
            _mock_async_client(mock_client_cls, stream_cm=stream_cm)
            with pytest.raises(CredentialsExpiredError):
                await client.download_file(
                    "https://aulavirtual.idat.edu.pe/f", io.BytesIO()
                )

        token_resp = MagicMock()
        token_resp.status_code = 200
        token_resp.json.return_value = {
            "exception": "moodle_exception",
            "errorcode": "invalidtoken",
            "message": "Invalid token",
        }
        _mock_async_client(mock_client_cls, post_return=token_resp)
        with pytest.raises(CredentialsExpiredError):
            await client.call_ws("core_course_get_contents")

        other_resp = MagicMock()
        other_resp.status_code = 200
        other_resp.json.return_value = {
            "exception": "moodle_exception",
            "errorcode": "dmlreadexception",
            "message": "DB error",
        }
        _mock_async_client(mock_client_cls, post_return=other_resp)
        with pytest.raises(RuntimeError, match="dmlreadexception") as ctx:
            await client.call_ws("core_course_get_contents")
        assert not isinstance(ctx.value, CredentialsExpiredError)
        mock_sleep.assert_not_awaited()

    @patch(
        "idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock
    )
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_reuses_single_shared_async_client_and_closes(
        self, mock_client_cls, mock_sleep
    ):
        """Reuse one AsyncClient across calls and close it on context exit."""

        async def _aiter_bytes(chunk_size: int):  # noqa: ARG001  # pylint: disable=unused-argument
            yield b"data"

        post_resp = MagicMock()
        post_resp.json.return_value = {"courses": []}
        dl_resp = MagicMock(status_code=200)
        dl_resp.aiter_bytes = MagicMock(side_effect=_aiter_bytes)
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=dl_resp)
        stream_cm.__aexit__ = AsyncMock(return_value=None)
        mock_async_client = _mock_async_client(
            mock_client_cls, post_return=post_resp, stream_cm=stream_cm
        )

        async with self._make_client(request_delay=0.0) as client:
            await client.call_ws("core_webservice_get_site_info")
            await client.download_file(
                "https://aulavirtual.idat.edu.pe/pluginfile.php/1/a.pdf",
                io.BytesIO(),
            )

        mock_client_cls.assert_called_once_with()
        mock_async_client.post.assert_awaited_once()
        mock_async_client.stream.assert_called_once()
        mock_async_client.aclose.assert_awaited_once_with()
        mock_sleep.assert_awaited()


class TestMoodleTokenClient(unittest.TestCase):
    """Tests for MoodleTokenClient token exchange."""

    def test_requires_base_url_and_rejects_request_delay(self):
        """Require base_url and reject unexpected request_delay kwarg."""
        with pytest.raises(TypeError, match="base_url"):
            MoodleTokenClient()  # type: ignore[call-arg]  # pylint: disable=no-value-for-parameter
        with pytest.raises(TypeError, match="request_delay"):
            MoodleTokenClient("https://lms.example.edu", request_delay=1.0)  # type: ignore[call-arg]  # pylint: disable=unexpected-keyword-arg

    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    def test_request_token_posts_mobile_service_with_request_header(
        self, mock_client_cls
    ):
        """Post credentials to login/token.php with MoodleMobile headers."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"token": "tok123"}
        mock_async_client = MagicMock()
        mock_async_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value.__aenter__ = AsyncMock(
            return_value=mock_async_client
        )
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = MoodleTokenClient(base_url="https://lms.example.edu/")
        payload = asyncio.run(client.request_token("jdoe", "pw"))

        assert payload == {"token": "tok123"}
        mock_async_client.post.assert_awaited_once_with(
            "https://lms.example.edu/login/token.php",
            data={
                "username": "jdoe",
                "password": "pw",
                "service": "moodle_mobile_app",
            },
            timeout=120,
            headers=REQUEST_HEADER,
        )
        mock_response.raise_for_status.assert_called_once_with()

    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    def test_request_token_propagates_http_error(self, mock_client_cls):
        """Propagate HTTPStatusError when token endpoint fails."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Server Error", request=MagicMock(), response=MagicMock()
        )
        mock_async_client = MagicMock()
        mock_async_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value.__aenter__ = AsyncMock(
            return_value=mock_async_client
        )
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = MoodleTokenClient(base_url="https://lms.example.edu")
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(client.request_token("jdoe", "pw"))


if __name__ == "__main__":
    unittest.main()
