import asyncio
import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from idat_sync.infrastructure.moodle_client import (
    REQUEST_HEADER,
    MoodleClient,
    MoodleTokenClient,
)


def _mock_async_client(mock_client_cls, *, post_return=None, stream_cm=None):
    mock_async_client = MagicMock()
    if post_return is not None:
        mock_async_client.post = AsyncMock(return_value=post_return)
    if stream_cm is not None:
        mock_async_client.stream = MagicMock(return_value=stream_cm)
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_async_client


class TestMoodleClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.token = "fake_test_token"
        self.client = MoodleClient(
            token=self.token,
            base_url="https://aulavirtual.idat.edu.pe",
            request_delay=30.0,
        )

    @patch("idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock)
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_call_ws_success(self, mock_client_cls, mock_sleep):
        mock_response = MagicMock()
        mock_response.json.return_value = {"courses": [{"id": 1, "fullname": "Test Course"}]}
        mock_async_client = _mock_async_client(mock_client_cls, post_return=mock_response)

        result = await self.client.call_ws("core_course_get_enrolled_courses", classification="all")

        mock_async_client.post.assert_awaited_once()
        _, kwargs = mock_async_client.post.call_args
        payload = kwargs["data"]
        self.assertEqual(payload["wstoken"], "fake_test_token")
        self.assertEqual(payload["wsfunction"], "core_course_get_enrolled_courses")
        self.assertEqual(payload["moodlewsrestformat"], "json")
        self.assertEqual(payload["classification"], "all")
        self.assertEqual(result, {"courses": [{"id": 1, "fullname": "Test Course"}]})
        mock_sleep.assert_awaited_once_with(30.0)

    @patch("idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock)
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_call_ws_moodle_exception(self, mock_client_cls, mock_sleep):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "exception": "moodle_exception",
            "errorcode": "invalidtoken",
            "message": "Invalid token",
        }
        _mock_async_client(mock_client_cls, post_return=mock_response)

        with self.assertRaises(RuntimeError) as ctx:
            await self.client.call_ws("any_function")

        self.assertIn("invalidtoken", str(ctx.exception))
        self.assertIn("Invalid token", str(ctx.exception))

    @patch("idat_sync.infrastructure.moodle_client.MoodleClient.call_ws", new_callable=AsyncMock)
    async def test_fetch_enrolled_courses(self, mock_call_ws):
        raw_courses = [{"id": 101, "fullname": "Course 101"}]
        mock_call_ws.return_value = {"courses": raw_courses}

        courses = await self.client.fetch_enrolled_courses()
        mock_call_ws.assert_awaited_once_with(
            "core_course_get_enrolled_courses_by_timeline_classification",
            classification="all",
            limit=100,
            offset=0,
        )
        self.assertEqual(courses, raw_courses)
        self.assertIsInstance(courses, list)
        self.assertIsInstance(courses[0], dict)  # Raw vendor data type

    @patch("idat_sync.infrastructure.moodle_client.MoodleClient.call_ws", new_callable=AsyncMock)
    async def test_fetch_assignments(self, mock_call_ws):
        raw_assignments = [{"id": 101, "assignments": [{"id": 5, "cmid": 12, "name": "Tarea"}]}]
        mock_call_ws.return_value = {"courses": raw_assignments}

        result = await self.client.fetch_assignments()
        mock_call_ws.assert_awaited_once_with("mod_assign_get_assignments")
        self.assertEqual(result, raw_assignments)
        self.assertIsInstance(result, list)
        self.assertIsInstance(result[0], dict)  # Raw vendor data type

    @patch("idat_sync.infrastructure.moodle_client.MoodleClient.call_ws", new_callable=AsyncMock)
    async def test_fetch_course_contents(self, mock_call_ws):
        raw_sections = [{"id": 1, "name": "General", "modules": []}]
        mock_call_ws.return_value = raw_sections

        result = await self.client.fetch_course_contents(course_id=101)
        mock_call_ws.assert_awaited_once_with("core_course_get_contents", courseid=101)
        self.assertEqual(result, raw_sections)
        self.assertIsInstance(result, list)
        self.assertIsInstance(result[0], dict)  # Raw vendor data type

    @patch("idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock)
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_call_ws_uses_injected_base_url_and_request_delay(self, mock_client_cls, mock_sleep):
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_async_client = _mock_async_client(mock_client_cls, post_return=mock_response)

        client = MoodleClient(
            token="tok",
            base_url="https://moodle.example.edu/",
            request_delay=2.5,
        )
        await client.call_ws("core_webservice_get_site_info")

        mock_async_client.post.assert_awaited_once()
        args, _ = mock_async_client.post.call_args
        self.assertEqual(args[0], "https://moodle.example.edu/webservice/rest/server.php")
        mock_sleep.assert_awaited_once_with(2.5)

    @patch("idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock)
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_download_file_streams_chunks_and_sleeps(self, mock_client_cls, mock_sleep):
        async def _aiter_bytes(chunk_size: int):
            for chunk in (b"chunk1-", b"", b"chunk2"):
                yield chunk

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_bytes = MagicMock(side_effect=_aiter_bytes)
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        stream_cm.__aexit__ = AsyncMock(return_value=None)
        mock_async_client = _mock_async_client(mock_client_cls, stream_cm=stream_cm)

        dest = io.BytesIO()
        target_url = "https://aulavirtual.idat.edu.pe/pluginfile.php/123/file.pdf"
        await self.client.download_file(target_url, dest)

        mock_async_client.stream.assert_called_once_with(
            "GET",
            target_url,
            params={"wstoken": self.token, "token": self.token},
            follow_redirects=True,
            timeout=60,
            headers=REQUEST_HEADER,
        )
        mock_response.raise_for_status.assert_called_once_with()
        mock_response.aiter_bytes.assert_called_once_with(chunk_size=1024 * 256)
        self.assertEqual(dest.getvalue(), b"chunk1-chunk2")
        mock_sleep.assert_awaited_once_with(30.0)

    @patch("idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock)
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_download_file_raises_runtime_error_on_403(self, mock_client_cls, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 403
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        stream_cm.__aexit__ = AsyncMock(return_value=None)
        _mock_async_client(mock_client_cls, stream_cm=stream_cm)

        dest = io.BytesIO()
        with self.assertRaises(RuntimeError) as ctx:
            await self.client.download_file("https://aulavirtual.idat.edu.pe/secret.pdf", dest)

        self.assertIn("403 Forbidden", str(ctx.exception))
        self.assertEqual(dest.getvalue(), b"")
        mock_sleep.assert_not_awaited()

    @patch("idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock)
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_download_file_propagates_http_error_without_sleeping(self, mock_client_cls, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=mock_response
        )
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        stream_cm.__aexit__ = AsyncMock(return_value=None)
        _mock_async_client(mock_client_cls, stream_cm=stream_cm)

        dest = io.BytesIO()
        with self.assertRaises(httpx.HTTPStatusError):
            await self.client.download_file("https://aulavirtual.idat.edu.pe/missing.pdf", dest)

        mock_sleep.assert_not_awaited()

    @patch("idat_sync.infrastructure.moodle_client.asyncio.sleep", new_callable=AsyncMock)
    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    async def test_call_ws_and_download_file_map_auth_failures_to_credentials_expired_error(
        self, mock_client_cls, mock_sleep
    ):
        from idat_sync.application.ports import CredentialsExpiredError

        for status in (401, 403):
            mock_resp = MagicMock()
            mock_resp.status_code = status
            _mock_async_client(mock_client_cls, post_return=mock_resp)
            with self.assertRaises(CredentialsExpiredError):
                await self.client.call_ws("core_course_get_contents")

            mock_dl = MagicMock()
            mock_dl.status_code = status
            stream_cm = MagicMock()
            stream_cm.__aenter__ = AsyncMock(return_value=mock_dl)
            stream_cm.__aexit__ = AsyncMock(return_value=None)
            _mock_async_client(mock_client_cls, stream_cm=stream_cm)
            with self.assertRaises(CredentialsExpiredError):
                await self.client.download_file("https://aulavirtual.idat.edu.pe/f", io.BytesIO())

        token_resp = MagicMock()
        token_resp.status_code = 200
        token_resp.json.return_value = {
            "exception": "moodle_exception",
            "errorcode": "invalidtoken",
            "message": "Invalid token",
        }
        _mock_async_client(mock_client_cls, post_return=token_resp)
        with self.assertRaises(CredentialsExpiredError):
            await self.client.call_ws("core_course_get_contents")

        other_resp = MagicMock()
        other_resp.status_code = 200
        other_resp.json.return_value = {
            "exception": "moodle_exception",
            "errorcode": "dmlreadexception",
            "message": "DB error",
        }
        _mock_async_client(mock_client_cls, post_return=other_resp)
        with self.assertRaises(RuntimeError) as ctx:
            await self.client.call_ws("core_course_get_contents")
        self.assertNotIsInstance(ctx.exception, CredentialsExpiredError)

        mock_sleep.assert_not_awaited()


class TestMoodleTokenClient(unittest.TestCase):
    def test_requires_base_url_and_rejects_request_delay(self):
        with self.assertRaises(TypeError):
            MoodleTokenClient()  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            MoodleTokenClient("https://lms.example.edu", request_delay=1.0)  # type: ignore[call-arg]

    @patch("idat_sync.infrastructure.moodle_client.httpx.AsyncClient")
    def test_request_token_posts_mobile_service_with_request_header(self, mock_client_cls):
        import asyncio
        from unittest.mock import AsyncMock

        mock_response = MagicMock()
        mock_response.json.return_value = {"token": "tok123"}
        mock_async_client = MagicMock()
        mock_async_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = MoodleTokenClient(base_url="https://lms.example.edu/")
        payload = asyncio.run(client.request_token("jdoe", "pw"))

        self.assertEqual(payload, {"token": "tok123"})
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
        import asyncio
        from unittest.mock import AsyncMock
        import httpx

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Server Error", request=MagicMock(), response=MagicMock()
        )
        mock_async_client = MagicMock()
        mock_async_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_async_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        client = MoodleTokenClient(base_url="https://lms.example.edu")
        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(client.request_token("jdoe", "pw"))


if __name__ == "__main__":
    unittest.main()
