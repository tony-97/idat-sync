from datetime import date, datetime
from pathlib import Path
import unittest
from unittest.mock import MagicMock
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


if __name__ == "__main__":
    unittest.main()
