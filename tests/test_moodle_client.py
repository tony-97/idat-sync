import unittest
from unittest.mock import MagicMock, patch
import requests

from idat_sync.infrastructure.moodle_client import MoodleClient


class TestMoodleClient(unittest.TestCase):
    def setUp(self):
        self.token = "fake_test_token"
        self.client = MoodleClient(token=self.token)

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


if __name__ == "__main__":
    unittest.main()
