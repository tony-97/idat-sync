import io
import unittest
from unittest.mock import MagicMock, patch
import requests

from idat_sync.infrastructure.moodle_client import MoodleClient
from idat_sync.utils import RQ_HEADER


class TestMoodleClient(unittest.TestCase):
    def setUp(self):
        self.token = "fake_test_token"
        self.client = MoodleClient(
            token=self.token,
            base_url="https://aulavirtual.idat.edu.pe",
            request_delay=30.0,
        )

    @patch("idat_sync.infrastructure.moodle_client.time.sleep")
    @patch("idat_sync.infrastructure.moodle_client.requests.post")
    def test_call_ws_success(self, mock_post, mock_sleep):
        mock_response = MagicMock()
        mock_response.json.return_value = {"courses": [{"id": 1, "fullname": "Test Course"}]}
        mock_post.return_value = mock_response

        result = self.client.call_ws("core_course_get_enrolled_courses", classification="all")

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        payload = kwargs["data"]
        self.assertEqual(payload["wstoken"], "fake_test_token")
        self.assertEqual(payload["wsfunction"], "core_course_get_enrolled_courses")
        self.assertEqual(payload["moodlewsrestformat"], "json")
        self.assertEqual(payload["classification"], "all")
        self.assertEqual(result, {"courses": [{"id": 1, "fullname": "Test Course"}]})

    @patch("idat_sync.infrastructure.moodle_client.time.sleep")
    @patch("idat_sync.infrastructure.moodle_client.requests.post")
    def test_call_ws_moodle_exception(self, mock_post, mock_sleep):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "exception": "moodle_exception",
            "errorcode": "invalidtoken",
            "message": "Invalid token",
        }
        mock_post.return_value = mock_response

        with self.assertRaises(RuntimeError) as ctx:
            self.client.call_ws("any_function")

        self.assertIn("invalidtoken", str(ctx.exception))
        self.assertIn("Invalid token", str(ctx.exception))

    @patch("idat_sync.infrastructure.moodle_client.MoodleClient.call_ws")
    def test_fetch_enrolled_courses(self, mock_call_ws):
        raw_courses = [{"id": 101, "fullname": "Course 101"}]
        mock_call_ws.return_value = {"courses": raw_courses}

        courses = self.client.fetch_enrolled_courses()
        mock_call_ws.assert_called_once_with(
            "core_course_get_enrolled_courses_by_timeline_classification",
            classification="all",
            limit=100,
            offset=0,
        )
        self.assertEqual(courses, raw_courses)
        self.assertIsInstance(courses, list)
        self.assertIsInstance(courses[0], dict)  # Raw vendor data type

    @patch("idat_sync.infrastructure.moodle_client.MoodleClient.call_ws")
    def test_fetch_assignments(self, mock_call_ws):
        raw_assignments = [{"id": 101, "assignments": [{"id": 5, "cmid": 12, "name": "Tarea"}]}]
        mock_call_ws.return_value = {"courses": raw_assignments}

        result = self.client.fetch_assignments()
        mock_call_ws.assert_called_once_with("mod_assign_get_assignments")
        self.assertEqual(result, raw_assignments)
        self.assertIsInstance(result, list)
        self.assertIsInstance(result[0], dict)  # Raw vendor data type

    @patch("idat_sync.infrastructure.moodle_client.MoodleClient.call_ws")
    def test_fetch_course_contents(self, mock_call_ws):
        raw_sections = [{"id": 1, "name": "General", "modules": []}]
        mock_call_ws.return_value = raw_sections

        result = self.client.fetch_course_contents(course_id=101)
        mock_call_ws.assert_called_once_with("core_course_get_contents", courseid=101)
        self.assertEqual(result, raw_sections)
        self.assertIsInstance(result, list)
        self.assertIsInstance(result[0], dict)  # Raw vendor data type

    @patch("idat_sync.infrastructure.moodle_client.time.sleep")
    @patch("idat_sync.infrastructure.moodle_client.requests.post")
    def test_call_ws_uses_injected_base_url_and_request_delay(self, mock_post, mock_sleep):
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_post.return_value = mock_response

        client = MoodleClient(
            token="tok",
            base_url="https://moodle.example.edu/",
            request_delay=2.5,
        )
        client.call_ws("core_webservice_get_site_info")

        mock_post.assert_called_once()
        args, _ = mock_post.call_args
        self.assertEqual(args[0], "https://moodle.example.edu/webservice/rest/server.php")
        mock_sleep.assert_called_once_with(2.5)

    @patch("idat_sync.infrastructure.moodle_client.time.sleep")
    @patch("idat_sync.infrastructure.moodle_client.requests.get")
    def test_download_file_streams_chunks_and_sleeps(self, mock_get, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content.return_value = [b"chunk1-", b"", b"chunk2"]
        mock_get.return_value.__enter__.return_value = mock_response

        dest = io.BytesIO()
        target_url = "https://aulavirtual.idat.edu.pe/pluginfile.php/123/file.pdf"
        self.client.download_file(target_url, dest)

        mock_get.assert_called_once_with(
            target_url,
            params={"wstoken": self.token, "token": self.token},
            stream=True,
            allow_redirects=True,
            timeout=60,
            headers=RQ_HEADER,
        )
        mock_response.raise_for_status.assert_called_once_with()
        mock_response.iter_content.assert_called_once_with(chunk_size=1024 * 256)
        self.assertEqual(dest.getvalue(), b"chunk1-chunk2")
        mock_sleep.assert_called_once_with(30.0)

    @patch("idat_sync.infrastructure.moodle_client.time.sleep")
    @patch("idat_sync.infrastructure.moodle_client.requests.get")
    def test_download_file_raises_runtime_error_on_403(self, mock_get, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_get.return_value.__enter__.return_value = mock_response

        dest = io.BytesIO()
        with self.assertRaises(RuntimeError) as ctx:
            self.client.download_file("https://aulavirtual.idat.edu.pe/secret.pdf", dest)

        self.assertIn("403 Forbidden", str(ctx.exception))
        self.assertEqual(dest.getvalue(), b"")
        mock_sleep.assert_not_called()

    @patch("idat_sync.infrastructure.moodle_client.time.sleep")
    @patch("idat_sync.infrastructure.moodle_client.requests.get")
    def test_download_file_propagates_http_error_without_sleeping(self, mock_get, mock_sleep):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
        mock_get.return_value.__enter__.return_value = mock_response

        dest = io.BytesIO()
        with self.assertRaises(requests.HTTPError):
            self.client.download_file("https://aulavirtual.idat.edu.pe/missing.pdf", dest)

        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
