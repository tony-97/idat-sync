from datetime import date, datetime
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import quote

from idat_sync.domain.content import RecordingContent
from idat_sync.infrastructure.sharepoint_adapter import SharepointApiAdapter
from idat_sync.infrastructure.sharepoint_client import SharepointClient


class TestSharepointAdapter(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock(spec=SharepointClient)
        self.mock_client.properties_to_retrieve = []
        self.mock_client.client = MagicMock()
        self.mock_client.search_client = MagicMock()
        self.semester_start = date(2025, 9, 22)
        self.adapter = SharepointApiAdapter(
            client=self.mock_client,
            semester_start=self.semester_start,
            sharepoint_host="idat628.sharepoint.com",
            sharepoint_personal_host="idat628-my.sharepoint.com",
            recordings_folder_name="Grabaciones",
            week_folder_prefix="Semana",
            request_delay=30.0,
        )

    def _create_mock_search_result(self, course_name: str, site_name: str):
        search_result = MagicMock()
        row = MagicMock()
        row.Cells = {
            "Title": f"Grabaciones - {course_name}",
            "ParentLink": "https://idat.sharepoint.com/Grabaciones",
            "SiteName": site_name,
        }
        search_result.value.PrimaryQueryResult.RelevantResults.Table.Rows = [row]
        return search_result

    def _create_mock_item(
        self,
        name: str,
        relative_url: str | None,
        time_created: datetime | None,
        time_last_modified: datetime | None = None,
    ):
        item = MagicMock()
        item.file.name = name
        item.file.serverRelativeUrl = relative_url
        item.file.time_created = time_created
        item.file.time_last_modified = time_last_modified
        return item

    def test_fetch_recordings_projects_into_weekly_domain_recording_content(self):
        course_name = "Algorithms"
        site_name = "https://idat628.sharepoint.com/sites/Algorithms"

        self.mock_client.search_for_recordings.return_value = (
            self._create_mock_search_result(course_name, site_name)
        )

        created_dt = datetime(2025, 9, 29, 14, 30)  # 7 days after start -> week 2
        last_mod_dt = datetime(2025, 9, 29, 16, 0)
        rel_url = "/personal/user/Documents/Algorithms/Grabaciones/video.mp4"

        item = self._create_mock_item(
            name="video.mp4",
            relative_url=rel_url,
            time_created=created_dt,
            time_last_modified=last_mod_dt,
        )
        self.mock_client.fetch_document_library_items.return_value = [item]

        recordings = self.adapter.fetch_recordings(course_name)

        self.assertEqual(len(recordings), 1)
        rec = recordings[0]
        self.assertIsInstance(rec, RecordingContent)
        self.assertEqual(rec.path, Path("Semana 2") / "video.mp4")
        self.assertEqual(rec.last_modified, int(last_mod_dt.timestamp()))
        expected_url = (
            f"https://idat628-my.sharepoint.com/:v:/r{quote(rel_url)}?csf=1&web=1"
        )
        self.assertEqual(rec.url, expected_url)

    def test_fetch_recordings_skips_missing_timestamp_without_shifting_weeks(self):
        course_name = "Algorithms"
        site_name = "https://idat628.sharepoint.com/sites/Algorithms"

        self.mock_client.search_for_recordings.return_value = (
            self._create_mock_search_result(course_name, site_name)
        )

        item_missing_created = self._create_mock_item(
            name="broken.mp4",
            relative_url="/personal/user/Documents/Algorithms/Grabaciones/broken.mp4",
            time_created=None,
        )

        item_missing_url = self._create_mock_item(
            name="broken2.mp4",
            relative_url=None,
            time_created=datetime(2025, 9, 23, 10, 0),
        )

        # 14 days after start -> week 3
        valid_created = datetime(2025, 10, 6, 10, 0)
        valid_url = "/personal/user/Documents/Algorithms/Grabaciones/valid.mp4"
        valid_item = self._create_mock_item(
            name="valid.mp4",
            relative_url=valid_url,
            time_created=valid_created,
            time_last_modified=valid_created,
        )

        self.mock_client.fetch_document_library_items.return_value = [
            item_missing_created,
            item_missing_url,
            valid_item,
        ]

        recordings = self.adapter.fetch_recordings(course_name)

        self.assertEqual(len(recordings), 1)
        rec = recordings[0]
        self.assertEqual(rec.path, Path("Semana 3") / "valid.mp4")

    def test_custom_hosts_folder_name_and_week_prefix(self):
        custom_adapter = SharepointApiAdapter(
            client=self.mock_client,
            semester_start=self.semester_start,
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            recordings_folder_name="Videos",
            week_folder_prefix="Week",
            request_delay=1.25,
        )
        self.assertTrue(custom_adapter.can_handle("https://contoso.sharepoint.com/sites/Course"))
        self.assertFalse(custom_adapter.can_handle("https://idat628.sharepoint.com/sites/Course"))

        course_name = "Algorithms"
        search_result = MagicMock()
        row = MagicMock()
        row.Cells = {
            "Title": f"Videos - {course_name}",
            "ParentLink": "https://contoso.sharepoint.com/Videos",
            "SiteName": "https://contoso.sharepoint.com/sites/Algorithms",
        }
        search_result.value.PrimaryQueryResult.RelevantResults.Table.Rows = [row]
        self.mock_client.search_for_recordings.return_value = search_result

        created_dt = datetime(2025, 9, 29, 14, 30)  # week 2
        rel_url = "/personal/user/Documents/Algorithms/videos/lecture.mp4"
        item = self._create_mock_item(
            name="lecture.mp4",
            relative_url=rel_url,
            time_created=created_dt,
            time_last_modified=created_dt,
        )
        self.mock_client.fetch_document_library_items.return_value = [item]

        recordings = custom_adapter.fetch_recordings(course_name)

        self.assertEqual(len(recordings), 1)
        self.assertEqual(recordings[0].path, Path("Week 2") / "lecture.mp4")
        self.assertEqual(
            recordings[0].url,
            f"https://contoso-my.sharepoint.com/:v:/r{quote(rel_url)}?csf=1&web=1",
        )

    @patch("idat_sync.infrastructure.sharepoint_adapter.time.sleep")
    def test_fetch_folder_contents_uses_injected_request_delay(self, mock_sleep):
        custom_adapter = SharepointApiAdapter(
            client=self.mock_client,
            semester_start=self.semester_start,
            sharepoint_host="idat628.sharepoint.com",
            sharepoint_personal_host="idat628-my.sharepoint.com",
            recordings_folder_name="Grabaciones",
            week_folder_prefix="Semana",
            request_delay=1.25,
        )
        mock_folder = MagicMock()
        mock_folder.get_files.return_value.select.return_value.execute_query_retry.return_value = []
        mock_folder.get_folders.return_value.execute_query_retry.return_value = []

        custom_adapter.fetch_folder_contents(mock_folder)

        self.assertEqual(mock_sleep.call_count, 2)
        mock_sleep.assert_called_with(1.25)

    @patch("idat_sync.infrastructure.sharepoint_client.time.sleep")
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    def test_sharepoint_client_uses_injected_hosts_and_delay(self, mock_ctx_cls, mock_sleep):
        mock_ctx = MagicMock()
        mock_ctx.with_cookies.return_value = mock_ctx
        mock_personal_site = MagicMock()
        mock_personal_site.url = "https://contoso-my.sharepoint.com/personal/user"
        mock_ctx.web.current_user.get_personal_site.return_value.execute_query_with_incremental_retry.return_value = mock_personal_site
        mock_ctx_cls.return_value = mock_ctx

        client = SharepointClient(
            cookies=[],
            sharepoint_personal_host="contoso-my.sharepoint.com",
            academic_site_url="https://contoso.sharepoint.com/sites/ACAD",
            request_delay=2.5,
        )

        called_urls = [call.args[0] for call in mock_ctx_cls.call_args_list]
        self.assertIn("https://contoso.sharepoint.com/sites/ACAD", called_urls)
        self.assertIn("https://contoso-my.sharepoint.com", called_urls)
        self.assertEqual(client.request_delay, 2.5)
        mock_sleep.assert_called_once_with(2.5)


if __name__ == "__main__":
    unittest.main()
