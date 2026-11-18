"""Unit tests for SharepointApiAdapter and SharepointClient."""
# pylint: disable=too-many-lines

import ast
import asyncio
import io
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
import pytest
from office365.runtime.client_request_exception import ClientRequestException
from office365.runtime.client_runtime_context import ClientRuntimeContext
from office365.runtime.paths.resource_path import ResourcePath
from office365.sharepoint.files.file import File
from office365.sharepoint.folders.folder import Folder

from idat_sync.application.ports import CredentialsExpiredError, RemoteServiceError
from idat_sync.domain.content import RecordingContent, SharepointContent
from idat_sync.infrastructure.sharepoint_adapter import SharepointApiAdapter
from idat_sync.infrastructure.sharepoint_client import SharepointClient


def _make_sharepoint_ctx(personal_site_url: str) -> MagicMock:
    ctx = MagicMock()
    ctx.with_cookies.return_value = ctx
    ctx.with_auto_retry.return_value = ctx
    personal_site = MagicMock(url=personal_site_url)
    ctx.web.current_user.get_personal_site.return_value.execute_query_async = AsyncMock(
        return_value=personal_site
    )
    return ctx


class TestSharepointAdapter(unittest.IsolatedAsyncioTestCase):
    """Tests for SharepointApiAdapter URL resolution, recordings, and sanitization."""

    def setUp(self):
        """Initialize SharepointApiAdapter with a mocked SharepointClient."""
        self.mock_client = MagicMock(spec=SharepointClient)
        self.semester_start = date(2025, 9, 22)
        self.adapter = SharepointApiAdapter(
            client=self.mock_client,
            semester_start=self.semester_start,
            sharepoint_host="idat628.sharepoint.com",
            sharepoint_personal_host="idat628-my.sharepoint.com",
            recordings_folder_name="Grabaciones",
            week_folder_prefix="Semana",
            timezone="America/Lima",
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
        item.file.server_relative_url = relative_url
        item.file.time_created = time_created
        item.file.time_last_modified = time_last_modified
        return item

    async def test_fetch_recordings_projects_into_weekly_domain_recording_content(self):
        """Map SharePoint recording search results into weekly RecordingContent."""
        course_name = "Algorithms"
        site_name = "https://idat628.sharepoint.com/sites/Algorithms"

        self.mock_client.search_for_recordings.return_value = (
            self._create_mock_search_result(course_name, site_name)
        )

        created_dt = datetime(2025, 9, 29, 14, 30)  # noqa: DTZ001
        last_mod_dt = datetime(2025, 9, 29, 16, 0)  # noqa: DTZ001
        rel_url = "/personal/user/Documents/Algorithms/Grabaciones/video.mp4"

        item = self._create_mock_item(
            name="video.mp4",
            relative_url=rel_url,
            time_created=created_dt,
            time_last_modified=last_mod_dt,
        )
        self.mock_client.fetch_document_library_items.return_value = [item]

        recordings = await self.adapter.fetch_recordings(course_name)

        assert len(recordings) == 1
        rec = recordings[0]
        assert isinstance(rec, RecordingContent)
        assert rec.path == Path("Semana 2") / "video.mp4"
        assert rec.last_modified == int(last_mod_dt.timestamp())
        expected_url = (
            f"https://idat628-my.sharepoint.com/:v:/r{quote(rel_url)}?csf=1&web=1"
        )
        assert rec.url == expected_url

    async def test_fetch_recordings_skips_missing_timestamp_without_shifting_weeks(
        self,
    ):
        """Skip recordings missing creation date or URL without shifting weeks."""
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
            time_created=datetime(2025, 9, 23, 10, 0),  # noqa: DTZ001
        )

        # 14 days after start -> week 3
        valid_created = datetime(2025, 10, 6, 10, 0)  # noqa: DTZ001
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

        recordings = await self.adapter.fetch_recordings(course_name)

        assert len(recordings) == 1
        rec = recordings[0]
        assert rec.path == Path("Semana 3") / "valid.mp4"

    async def test_custom_hosts_folder_name_and_week_prefix(self):
        """Honor custom SharePoint hosts, recordings folder name, and week prefix."""
        custom_adapter = SharepointApiAdapter(
            client=self.mock_client,
            semester_start=self.semester_start,
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            recordings_folder_name="Videos",
            week_folder_prefix="Week",
            timezone="America/Lima",
        )
        assert custom_adapter.can_handle("https://contoso.sharepoint.com/sites/Course")
        assert not custom_adapter.can_handle(
            "https://idat628.sharepoint.com/sites/Course"
        )

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

        created_dt = datetime(2025, 9, 29, 14, 30)  # noqa: DTZ001
        rel_url = "/personal/user/Documents/Algorithms/videos/lecture.mp4"
        item = self._create_mock_item(
            name="lecture.mp4",
            relative_url=rel_url,
            time_created=created_dt,
            time_last_modified=created_dt,
        )
        self.mock_client.fetch_document_library_items.return_value = [item]

        recordings = await custom_adapter.fetch_recordings(course_name)

        assert len(recordings) == 1
        assert recordings[0].path == Path("Week 2") / "lecture.mp4"
        assert (
            recordings[0].url
            == f"https://contoso-my.sharepoint.com/:v:/r{quote(rel_url)}?csf=1&web=1"
        )

    async def test_fetch_recordings_matches_course_name_at_boundaries(self):
        """Match course names at word boundaries and omit legacy aliases."""
        assert not hasattr(SharepointApiAdapter, "_SharepointApiAdapter__get_name")
        assert not hasattr(
            SharepointApiAdapter, "_SharepointApiAdapter__get_last_modified"
        )

        course_name = "Matemática I"
        search_result = MagicMock()
        row_ii = MagicMock()
        row_ii.Cells = {
            "Title": "Matemática II - Grabaciones",
            "ParentLink": "https://idat628.sharepoint.com/Grabaciones",
            "SiteName": "https://idat628.sharepoint.com/sites/MatII",
        }
        row_i = MagicMock()
        row_i.Cells = {
            "Title": "Matemática I - Grabaciones",
            "ParentLink": "https://idat628.sharepoint.com/Grabaciones",
            "SiteName": "https://idat628.sharepoint.com/sites/MatI",
        }
        search_result.value.PrimaryQueryResult.RelevantResults.Table.Rows = [
            row_ii,
            row_i,
        ]
        self.mock_client.search_for_recordings.return_value = search_result

        created_dt = datetime(2025, 9, 22, 10, 0)  # noqa: DTZ001  # noqa: DTZ001
        item_ii = self._create_mock_item(
            name="mat2.mp4",
            relative_url="/personal/u/Documents/Matemática II/Grabaciones/mat2.mp4",
            time_created=created_dt,
            time_last_modified=created_dt,
        )
        item_i_encoded = self._create_mock_item(
            name="mat1.mp4",
            relative_url=(
                "/personal/u/Documents/Matem%C3%A1tica%20I/Grabaciones/mat1.mp4"
            ),
            time_created=created_dt,
            time_last_modified=created_dt,
        )
        self.mock_client.fetch_document_library_items.return_value = [
            item_ii,
            item_i_encoded,
        ]

        recordings = await self.adapter.fetch_recordings(course_name)

        self.mock_client.fetch_document_library_items.assert_awaited_once_with(
            "https://idat628.sharepoint.com/sites/MatI"
        )
        assert len(recordings) == 1
        assert recordings[0].path.name == "mat1.mp4"

    async def test_fetch_recordings_preserves_hash_and_question_mark_in_paths(self):
        """Preserve '#' while stripping '?' from recording paths."""
        course_name = "Curso #1"
        search_result = MagicMock()
        row = MagicMock()
        row.Cells = {
            "Title": "Curso #1 - Grabaciones",
            "ParentLink": "https://idat628.sharepoint.com/Grabaciones",
            "SiteName": "https://idat628.sharepoint.com/sites/Curso1",
        }
        search_result.value.PrimaryQueryResult.RelevantResults.Table.Rows = [row]
        self.mock_client.search_for_recordings.return_value = search_result

        created_dt = datetime(2025, 9, 22, 10, 0)  # noqa: DTZ001
        item = self._create_mock_item(
            name="Clase #2?.mp4",
            relative_url="/personal/u/Documents/Curso #1/Grabaciones/Clase #2?.mp4",
            time_created=created_dt,
            time_last_modified=created_dt,
        )
        self.mock_client.fetch_document_library_items.return_value = [item]

        recordings = await self.adapter.fetch_recordings(course_name)

        assert len(recordings) == 1
        assert recordings[0].path.name == "Clase #2.mp4"

    def test_adapter_does_not_accept_request_delay_or_import_time_or_client_context(
        self,
    ):
        """Ensure SharepointApiAdapter stays decoupled from transport/time."""
        with pytest.raises(TypeError, match="request_delay"):
            SharepointApiAdapter(  # pylint: disable=unexpected-keyword-arg
                client=self.mock_client,
                semester_start=self.semester_start,
                sharepoint_host="idat628.sharepoint.com",
                sharepoint_personal_host="idat628-my.sharepoint.com",
                recordings_folder_name="Grabaciones",
                week_folder_prefix="Semana",
                timezone="America/Lima",
                request_delay=1.25,  # type: ignore[call-arg]
            )

        for attr in (
            "request_delay",
            "properties_to_retrieve",
            "client",
            "search_client",
        ):
            assert not hasattr(self.adapter, attr), f"Unexpected attribute {attr}"

        adapter_path = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "idat_sync"
            / "infrastructure"
            / "sharepoint_adapter.py"
        )
        tree = ast.parse(
            adapter_path.read_text(encoding="utf-8"), filename=str(adapter_path)
        )
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module)
                imported.update(alias.name for alias in node.names)
        assert "time" not in imported
        assert "ClientContext" not in imported
        assert "office365.sharepoint.client_context" not in imported

    async def test_fetch_folder_contents_delegates_files_then_subfolders_to_client(
        self,
    ):
        """Fetch files and recurse subfolders via SharepointClient."""
        root_folder = MagicMock()
        sub_folder = MagicMock()
        sub_folder.name = "Week 1"

        root_file = MagicMock()
        root_file.name = "syllabus.pdf"
        root_file.time_last_modified = None

        nested_file = MagicMock()
        nested_file.name = "notes.docx"
        nested_file.time_last_modified = None

        call_order: list[str] = []

        async def fake_files(folder):
            call_order.append("files")
            return [root_file] if folder is root_folder else [nested_file]

        async def fake_subfolders(folder):
            call_order.append("subfolders")
            return [sub_folder] if folder is root_folder else []

        self.mock_client.fetch_folder_files.side_effect = fake_files
        self.mock_client.fetch_subfolders.side_effect = fake_subfolders

        contents = await self.adapter.fetch_folder_contents(root_folder)

        assert call_order == ["files", "subfolders", "files", "subfolders"]
        assert len(contents) == 2
        assert all(isinstance(c, SharepointContent) for c in contents)
        assert [c.path for c in contents] == [
            Path("syllabus.pdf"),
            Path("Week 1") / "notes.docx",
        ]

    async def test_requires_timezone_and_converts_aware_timestamps(self):
        """Require timezone and convert aware datetimes before week calculation."""
        with pytest.raises(TypeError, match="timezone"):
            SharepointApiAdapter(  # type: ignore[call-arg]  # pylint: disable=no-value-for-parameter
                client=self.mock_client,
                semester_start=self.semester_start,
                sharepoint_host="idat628.sharepoint.com",
                sharepoint_personal_host="idat628-my.sharepoint.com",
                recordings_folder_name="Grabaciones",
                week_folder_prefix="Semana",
            )

        assert self.adapter.timezone == ZoneInfo("America/Lima")

        course_name = "Algorithms"
        self.mock_client.search_for_recordings.return_value = (
            self._create_mock_search_result(
                course_name, "https://idat628.sharepoint.com/sites/Algorithms"
            )
        )
        # 2025-09-29 03:00 UTC is 2025-09-28 22:00 in America/Lima (Sunday -> Semana 1)
        aware_utc = datetime(2025, 9, 29, 3, 0, tzinfo=UTC)
        naive_local = datetime(2025, 9, 29, 14, 30)  # noqa: DTZ001
        item_aware = self._create_mock_item(
            name="sunday_night.mp4",
            relative_url=(
                "/personal/user/Documents/Algorithms/Grabaciones/sunday_night.mp4"
            ),
            time_created=aware_utc,
            time_last_modified=aware_utc,
        )
        item_naive = self._create_mock_item(
            name="monday_afternoon.mp4",
            relative_url=(
                "/personal/user/Documents/Algorithms/Grabaciones/monday_afternoon.mp4"
            ),
            time_created=naive_local,
            time_last_modified=naive_local,
        )
        self.mock_client.fetch_document_library_items.return_value = [
            item_aware,
            item_naive,
        ]

        recordings = await self.adapter.fetch_recordings(course_name)

        assert len(recordings) == 2
        assert recordings[0].path == Path("Semana 1") / "sunday_night.mp4"
        assert recordings[1].path == Path("Semana 2") / "monday_afternoon.mp4"

    async def test_guards_datetime_min_on_real_office365_file_and_folder(self):
        """Guard against datetime.min sentinels on Office365 File and Folder."""
        ctx = MagicMock(spec=ClientRuntimeContext)
        real_folder = Folder(ctx, ResourcePath("folder"))
        real_file = File(ctx, ResourcePath("file"))
        real_file.set_property("Name", "recording.mp4", False)  # noqa: FBT003
        real_file.set_property(
            "ServerRelativeUrl",
            "/personal/user/Documents/Algorithms/Grabaciones/recording.mp4",
            False,  # noqa: FBT003
        )

        assert real_folder.time_last_modified == datetime.min  # noqa: DTZ901
        assert real_file.time_last_modified == datetime.min  # noqa: DTZ901
        assert real_file.time_created == datetime.min  # noqa: DTZ901
        assert self.adapter.get_last_modified(real_folder, 42) == 42
        assert self.adapter.get_last_modified(real_file, 42) == 42

        course_name = "Algorithms"
        self.mock_client.search_for_recordings.return_value = (
            self._create_mock_search_result(
                course_name, "https://idat628.sharepoint.com/sites/Algorithms"
            )
        )
        item = MagicMock()
        item.file = real_file
        self.mock_client.fetch_document_library_items.return_value = [item]

        recordings = await self.adapter.fetch_recordings(course_name)
        assert recordings == []

    @patch(
        "idat_sync.infrastructure.sharepoint_client.asyncio.sleep",
        new_callable=AsyncMock,
    )
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    async def test_sharepoint_client_uses_injected_hosts_and_delay(
        self, mock_ctx_cls, mock_sleep
    ):
        """Use injected SharePoint hosts and request_delay on create()."""
        mock_ctx_cls.return_value = _make_sharepoint_ctx(
            "https://contoso-my.sharepoint.com/personal/user"
        )

        client = await SharepointClient.create(
            cookies=[],
            sharepoint_personal_host="contoso-my.sharepoint.com",
            academic_site_url="https://contoso.sharepoint.com/sites/ACAD",
            request_delay=2.5,
        )

        called_urls = [call.args[0] for call in mock_ctx_cls.call_args_list]
        assert "https://contoso.sharepoint.com/sites/ACAD" in called_urls
        assert "https://contoso-my.sharepoint.com" in called_urls
        assert client.request_delay == 2.5
        mock_sleep.assert_awaited_once_with(2.5)

    @patch(
        "idat_sync.infrastructure.sharepoint_client.asyncio.sleep",
        new_callable=AsyncMock,
    )
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    async def test_single_label_host_does_not_wildcard_match_unrelated_cookies(
        self, mock_ctx_cls, mock_sleep
    ):
        """Prevent single-label hosts from matching wildcard domain cookies."""
        mock_ctx_cls.return_value = _make_sharepoint_ctx(
            "https://intranet/personal/user"
        )

        cookies = [
            {"name": "FedAuth", "value": "foreign", "domain": ".sharepoint.com"},
            {"name": "rtFa", "value": "exact", "domain": "intranet"},
            {
                "name": "FedAuth",
                "value": "evil",
                "domain": "evil.sharepoint.com.attacker.test",
            },
        ]
        client = await SharepointClient.create(
            cookies=cookies,  # type: ignore[arg-type]
            sharepoint_personal_host="intranet",
            academic_site_url="https://intranet/sites/ACAD",
            request_delay=0.0,
        )

        assert not hasattr(client, "sharepoint_cookies")
        assert client.cookies_for_host("intranet") == {"rtFa": "exact"}
        mock_sleep.assert_awaited_once_with(0.0)

    @patch(
        "idat_sync.infrastructure.sharepoint_client.asyncio.sleep",
        new_callable=AsyncMock,
    )
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    async def test_cookies_for_host_isolates_hosts_and_prefers_exact_match(
        self, mock_ctx_cls, mock_sleep
    ):
        """Isolate cookies by host and prefer exact host matches over wildcards."""
        captured_callbacks = []

        def build_ctx(site_url):
            ctx = MagicMock()
            ctx.with_cookies.side_effect = lambda cb: (
                captured_callbacks.append((site_url, cb)),
                ctx,
            )[1]
            ctx.with_auto_retry.return_value = ctx
            personal_site = MagicMock()
            personal_site.url = "https://contoso-my.sharepoint.com/personal/user"
            ctx.web.current_user.get_personal_site.return_value.execute_query_async = (
                AsyncMock(return_value=personal_site)
            )
            return ctx

        mock_ctx_cls.side_effect = build_ctx

        cookies = [
            {
                "name": "FedAuth",
                "value": "exact-acad-first",
                "domain": "Contoso.SharePoint.com",
            },
            {"name": "FedAuth", "value": "wildcard", "domain": ".sharepoint.com"},
            {
                "name": "rtFa",
                "value": "personal-rtfa",
                "domain": "contoso-my.sharepoint.com",
            },
            {
                "name": "FedAuth",
                "value": "personal-fedauth",
                "domain": "contoso-my.sharepoint.com",
            },
            {
                "name": "SPOIDCRL",
                "value": "evil",
                "domain": "evil.sharepoint.com.attacker.test",
            },
            {
                "name": "rtFa",
                "value": "exact-acad-last",
                "domain": "contoso.sharepoint.com",
            },
        ]
        client = await SharepointClient.create(
            cookies=cookies,  # type: ignore[arg-type]
            sharepoint_personal_host="contoso-my.sharepoint.com",
            academic_site_url="https://contoso.sharepoint.com/sites/ACAD",
            request_delay=0.0,
        )

        acad_cookies = client.cookies_for_host("contoso.sharepoint.com")
        assert acad_cookies == {
            "FedAuth": "exact-acad-first",
            "rtFa": "exact-acad-last",
        }
        personal_cookies = client.cookies_for_host("contoso-my.sharepoint.com")
        assert personal_cookies == {
            "FedAuth": "personal-fedauth",
            "rtFa": "personal-rtfa",
        }
        assert client.cookies_for_host("other.sharepoint.com") == {
            "FedAuth": "wildcard"
        }
        assert client.cookies_for_host("evil.sharepoint.com.attacker.test") == {
            "SPOIDCRL": "evil"
        }

        acad_cb = next(
            cb
            for url, cb in captured_callbacks
            if url == "https://contoso.sharepoint.com/sites/ACAD"
        )
        personal_cb = next(
            cb
            for url, cb in captured_callbacks
            if url == "https://contoso-my.sharepoint.com"
        )
        assert acad_cb() == {"FedAuth": "exact-acad-first", "rtFa": "exact-acad-last"}
        assert personal_cb() == {"FedAuth": "personal-fedauth", "rtFa": "personal-rtfa"}
        mock_sleep.assert_awaited_once_with(0.0)

    @patch(
        "idat_sync.infrastructure.sharepoint_client.asyncio.sleep",
        new_callable=AsyncMock,
    )
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    async def test_sharepoint_client_sdk_queries_and_download_sleep(
        self, mock_ctx_cls, mock_sleep
    ):
        """Sleep request_delay after each SharePoint SDK query and download."""
        mock_ctx = _make_sharepoint_ctx(
            "https://contoso-my.sharepoint.com/personal/user"
        )
        mock_ctx.execute_query_with_incremental_retry_async = AsyncMock()
        mock_ctx_cls.return_value = mock_ctx

        client = await SharepointClient.create(
            cookies=[],
            sharepoint_personal_host="contoso-my.sharepoint.com",
            academic_site_url="https://contoso.sharepoint.com/sites/ACAD",
            request_delay=1.75,
        )
        mock_sleep.reset_mock()

        mock_folder = MagicMock()
        mock_ctx.web.get_folder_by_guest_url.return_value = mock_folder
        folder_url = "https://contoso.sharepoint.com/:f:/s/Course/folder"
        assert await client.fetch_folder_by_guest_url(folder_url) is mock_folder
        mock_ctx.web.get_folder_by_guest_url.assert_called_once_with(folder_url)
        mock_ctx.load.assert_called_with(mock_folder, client.properties_to_retrieve)

        mock_guest_file = MagicMock()
        mock_ctx.web.get_file_by_guest_url.return_value = mock_guest_file
        file_url = "https://contoso.sharepoint.com/:b:/s/Course/file"
        assert await client.fetch_file_by_guest_url(file_url) is mock_guest_file
        mock_ctx.web.get_file_by_guest_url.assert_called_once_with(file_url)

        mock_ctx.web.get_file_by_id.return_value = mock_guest_file
        direct_url = (
            "https://contoso.sharepoint.com/sites/Course/doc.aspx"
            "?sourcedoc=%7BABC-123%7D"
        )
        assert await client.fetch_file_by_id(direct_url, "ABC-123") is mock_guest_file
        mock_ctx.web.get_file_by_id.assert_called_once_with("ABC-123")

        mock_folder.get_files.return_value.select.return_value.execute_query_async = (
            AsyncMock(return_value=[mock_guest_file])
        )
        assert await client.fetch_folder_files(mock_folder) == [mock_guest_file]
        mock_folder.get_files.return_value.select.assert_called_once_with(
            client.properties_to_retrieve
        )

        mock_folder.get_folders.return_value.execute_query_async = AsyncMock(
            return_value=[mock_folder]
        )
        assert await client.fetch_subfolders(mock_folder) == [mock_folder]

        dest = io.BytesIO()
        mock_guest_file.download_session_async = AsyncMock()
        await client.download_file(mock_guest_file, dest)
        mock_guest_file.download_session_async.assert_awaited_once_with(
            dest, use_path=False
        )
        assert mock_sleep.call_count == 6
        mock_sleep.assert_called_with(1.75)

    @patch(
        "idat_sync.infrastructure.sharepoint_client.asyncio.sleep",
        new_callable=AsyncMock,
    )
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    async def test_fetch_document_library_items_selects_and_expands(
        self, mock_ctx_cls, mock_sleep
    ):
        """Select File metadata columns and expand File in document library query."""
        mock_ctx = _make_sharepoint_ctx(
            "https://contoso-my.sharepoint.com/personal/user"
        )
        mock_ctx_cls.return_value = mock_ctx
        client = await SharepointClient.create(
            cookies=[],
            sharepoint_personal_host="contoso-my.sharepoint.com",
            academic_site_url="https://contoso.sharepoint.com/sites/ACAD",
            request_delay=1.75,
        )
        mock_sleep.reset_mock()

        mock_doc_lib_item = MagicMock()
        items_query = mock_ctx.web.default_document_library.return_value.items
        items_query.select.return_value.expand.return_value.get_all_async = AsyncMock(
            return_value=[mock_doc_lib_item]
        )
        assert await client.fetch_document_library_items(
            "https://contoso.sharepoint.com/sites/Course"
        ) == [mock_doc_lib_item]
        items_query.select.assert_called_once_with(
            [
                "File/Name",
                "File/ServerRelativeUrl",
                "File/TimeCreated",
                "File/TimeLastModified",
            ]
        )
        items_query.select.return_value.expand.assert_called_once_with(["File"])
        get_all = items_query.select.return_value.expand.return_value.get_all_async
        get_all.assert_awaited_once_with(page_size=50)
        mock_sleep.assert_awaited_once_with(1.75)

    async def test_list_url_content_matches_guest_urls_case_insensitively(self):
        """Match SharePoint guest URLs and hosts case-insensitively."""
        custom_adapter = SharepointApiAdapter(
            client=self.mock_client,
            semester_start=self.semester_start,
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            recordings_folder_name="Videos",
            week_folder_prefix="Week",
            timezone="America/Lima",
        )
        mock_file = MagicMock()
        mock_file.name = "slides.pdf"
        mock_file.time_last_modified = None
        self.mock_client.fetch_file_by_guest_url.return_value = mock_file

        guest_url = "https://CONTOSO.SHAREPOINT.COM/:b:/s/Course/abc"
        assert custom_adapter.can_handle(guest_url)
        items = await custom_adapter.list_url_content(guest_url, "slides.pdf", 100)

        assert len(items) == 1
        assert items[0].path == Path("slides.pdf")
        self.mock_client.fetch_file_by_guest_url.assert_called_once_with(guest_url)

        mock_folder = MagicMock()
        self.mock_client.fetch_folder_by_guest_url.return_value = mock_folder
        self.mock_client.fetch_folder_files.return_value = []
        self.mock_client.fetch_subfolders.return_value = []

        folder_url = "https://CONTOSO.SHAREPOINT.COM/:f:/s/Course/folder"
        await custom_adapter.list_url_content(folder_url, "folder", 100)
        self.mock_client.fetch_folder_by_guest_url.assert_called_once_with(folder_url)

    async def test_list_url_content_direct_links_extract_id_or_skip(self):
        """Extract uniqueid/sourcedoc from direct links or return [] when absent."""
        mock_file = MagicMock()
        mock_file.name = "handout.docx"
        mock_file.time_last_modified = None
        self.mock_client.fetch_file_by_id.return_value = mock_file

        uniqueid_url = (
            "https://idat628.sharepoint.com/sites/C/Doc.aspx?UniqueId=%7B111-222%7D"
        )
        items_uid = await self.adapter.list_url_content(
            uniqueid_url, "fallback.docx", 50
        )
        assert len(items_uid) == 1
        assert items_uid[0].path == Path("handout.docx")
        self.mock_client.fetch_file_by_id.assert_called_once_with(
            uniqueid_url, "111-222"
        )

        self.mock_client.fetch_file_by_id.reset_mock()
        sourcedoc_url = (
            "https://idat628.sharepoint.com/sites/C/Doc.aspx?sourcedoc=%7B333-444%7D"
        )
        items_src = await self.adapter.list_url_content(
            sourcedoc_url, "fallback.docx", 50
        )
        assert len(items_src) == 1
        self.mock_client.fetch_file_by_id.assert_called_once_with(
            sourcedoc_url, "333-444"
        )

        self.mock_client.fetch_file_by_id.reset_mock()
        no_id_url = "https://idat628.sharepoint.com/sites/C/SitePages/Home.aspx"
        assert await self.adapter.fetch_file_from_direct_link(no_id_url) is None
        assert await self.adapter.list_url_content(no_id_url, "Home.aspx", 50) == []
        self.mock_client.fetch_file_by_id.assert_not_called()

    async def test_sanitizes_unsafe_sharepoint_folder_file_and_recording_segments(self):
        """Sanitize unsafe folder, file, and recording names while keeping UTF-8."""
        # Shared folder + file sanitization preserving UTF-8
        root_folder = MagicMock()
        sub_folder = MagicMock()
        sub_folder.name = "CON"
        sub_folder.server_relative_url = "/sites/C/CON"
        shared_file = MagicMock()
        shared_file.name = "Sesión: 01/Parte?.pdf"
        shared_file.server_relative_url = "/sites/C/CON/Sesión.pdf"
        shared_file.time_last_modified = None

        self.mock_client.fetch_folder_by_guest_url.return_value = root_folder
        self.mock_client.fetch_folder_files.side_effect = [[], [shared_file]]
        self.mock_client.fetch_subfolders.side_effect = [[sub_folder], []]

        folder_url = "https://idat628.sharepoint.com/:f:/s/Course/folder"
        items = await self.adapter.list_url_content(folder_url, "folder", 100)
        assert items[0].path == Path("_CON") / "Sesión 01 Parte.pdf"

        # Recording filename sanitization
        row = MagicMock()
        row.Cells = {
            "Title": "Algebra",
            "ParentLink": "https://idat628.sharepoint.com/sites/A/Grabaciones",
            "SiteName": "https://idat628.sharepoint.com/sites/A",
        }
        search_res = MagicMock()
        search_res.value.PrimaryQueryResult.RelevantResults.Table.Rows = [row]
        self.mock_client.search_for_recordings.return_value = search_res

        rec_item = MagicMock()
        rec_item.file.server_relative_url = "/sites/Algebra/Grabaciones/video.mp4"
        rec_item.file.time_created = datetime(2025, 9, 22, 10, 0, 0)  # noqa: DTZ001
        rec_item.file.time_last_modified = datetime(2025, 9, 22, 11, 0, 0)  # noqa: DTZ001
        rec_item.file.name = "Sesión: 01/Parte?.mp4"
        self.mock_client.fetch_document_library_items.return_value = [rec_item]

        recs = await self.adapter.fetch_recordings("Algebra")
        assert recs[0].path == Path("Semana 1") / "Sesión 01 Parte.mp4"

    async def test_personal_host_can_handle_path_dispatch_and_empty_odata_null_skip(
        self,
    ):
        """Handle personal host URLs and return [] for empty odata.null entities."""
        personal_file_url = (
            "https://idat628-my.sharepoint.com/:b:/g/personal/user_idat_pe/Ea7D?e=1"
        )
        personal_folder_url = (
            "https://idat628-my.sharepoint.com/:f:/g/personal/user_idat_pe/IgCR?e=1"
        )
        assert self.adapter.can_handle(personal_file_url)
        assert self.adapter.can_handle(personal_folder_url)
        assert not self.adapter.can_handle("https://example.com/file.pdf")

        mock_personal_file = MagicMock()
        mock_personal_file.name = "CLASE1.pdf"
        mock_personal_file.server_relative_url = "/personal/user/Documents/CLASE1.pdf"
        mock_personal_file.time_last_modified = None
        self.mock_client.fetch_file_by_guest_url.return_value = mock_personal_file

        items = await self.adapter.list_url_content(personal_file_url, "Clase 1", 100)
        assert len(items) == 1
        assert items[0].path == Path("CLASE1.pdf")
        self.mock_client.fetch_file_by_guest_url.assert_called_once_with(
            personal_file_url
        )

        # Dead/expired odata.null entities -> []
        dead_entity = MagicMock()
        dead_entity.name = None
        dead_entity.server_relative_url = None
        self.mock_client.fetch_file_by_guest_url.return_value = dead_entity
        assert (
            await self.adapter.list_url_content(personal_file_url, "fallback.pdf", 100)
            == []
        )

        self.mock_client.fetch_folder_by_guest_url.return_value = dead_entity
        assert (
            await self.adapter.list_url_content(personal_folder_url, "fallback", 100)
            == []
        )
        self.mock_client.fetch_folder_files.assert_not_called()

        self.mock_client.fetch_file_by_id.return_value = dead_entity
        embed_url = (
            "https://idat628.sharepoint.com/sites/C/_layouts/15/embed.aspx?UniqueId=111"
        )
        assert await self.adapter.list_url_content(embed_url, "fallback.mp4", 100) == []


class TestSharepointClient(unittest.IsolatedAsyncioTestCase):
    """Tests for SharepointClient creation, routing, auth errors, and cleanup."""

    @patch(
        "idat_sync.infrastructure.sharepoint_client.asyncio.sleep",
        new_callable=AsyncMock,
    )
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    async def test_maps_empty_personal_site_and_http_401_403_to_auth_error(
        self, mock_ctx_cls, mock_sleep
    ):
        """Map empty personal_site.url and HTTP 401/403 to CredentialsExpiredError."""
        mock_ctx_cls.return_value = _make_sharepoint_ctx("")

        with pytest.raises(CredentialsExpiredError):
            await SharepointClient.create(
                cookies=[],
                sharepoint_personal_host="idat628-my.sharepoint.com",
                academic_site_url="https://idat628.sharepoint.com/sites/Academic",
                request_delay=0.0,
            )

        ctx_ok = _make_sharepoint_ctx("https://idat628-my.sharepoint.com/personal/user")
        mock_ctx_cls.return_value = ctx_ok
        client = await SharepointClient.create(
            cookies=[],
            sharepoint_personal_host="idat628-my.sharepoint.com",
            academic_site_url="https://idat628.sharepoint.com/sites/Academic",
            request_delay=0.0,
        )

        for status in (401, 403):
            err = ClientRequestException(response=MagicMock(status_code=status))
            ctx_ok.search.query.return_value.execute_query_async = AsyncMock(
                side_effect=err
            )
            with pytest.raises(CredentialsExpiredError):
                await client.search_for_recordings("Algebra")
            assert ctx_ok.search.query.return_value.execute_query_async.call_count == 1

        err_404 = ClientRequestException(response=MagicMock(status_code=404))
        ctx_ok.search.query.return_value.execute_query_async = AsyncMock(
            side_effect=err_404
        )
        with pytest.raises(RemoteServiceError) as ctx:
            await client.search_for_recordings("Algebra")
        assert not isinstance(ctx.value, CredentialsExpiredError)
        assert isinstance(ctx.value.__cause__, ClientRequestException)
        assert ctx_ok.search.query.return_value.execute_query_async.call_count == 1

        http_err = httpx.ConnectError("sp timeout", request=MagicMock())
        ctx_ok.search.query.return_value.execute_query_async = AsyncMock(
            side_effect=http_err
        )
        with pytest.raises(
            RemoteServiceError, match="SharePoint request failed"
        ) as hctx:
            await client.search_for_recordings("Algebra")
        assert isinstance(hctx.value.__cause__, httpx.HTTPError)

        client.search_client = None
        with pytest.raises(CredentialsExpiredError):
            await client.search_for_recordings("Algebra")
        mock_sleep.assert_awaited()

    @patch(
        "idat_sync.infrastructure.sharepoint_client.asyncio.sleep",
        new_callable=AsyncMock,
    )
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    async def test_get_client_from_url_and_load_entity_odata_null(
        self, mock_ctx_cls, mock_sleep
    ):
        """Route personal/sites URLs and handle odata.null TypeError in load_entity."""
        ctx_ok = _make_sharepoint_ctx("https://idat628-my.sharepoint.com/personal/user")
        mock_ctx_cls.return_value = ctx_ok

        client = await SharepointClient.create(
            cookies=[],
            sharepoint_personal_host="idat628-my.sharepoint.com",
            academic_site_url="https://idat628.sharepoint.com/sites/Academic",
            request_delay=1.25,
        )
        mock_ctx_cls.reset_mock()
        mock_sleep.reset_mock()

        client.get_client_from_url(
            "https://idat628-my.sharepoint.com/:b:/g/personal/fjcanari_idat_pe/Ea7D?e=1"
        )
        mock_ctx_cls.assert_called_once_with(
            "https://idat628-my.sharepoint.com/personal/fjcanari_idat_pe"
        )

        mock_ctx_cls.reset_mock()
        client.get_client_from_url(
            "https://idat628.sharepoint.com/:b:/S/MATERIALESTED/Ea7D?e=1"
        )
        mock_ctx_cls.assert_called_once_with(
            "https://idat628.sharepoint.com/sites/MATERIALESTED"
        )

        # odata.null leaves entity.resource_url=None -> raises TypeError
        ctx_ok.execute_query_with_incremental_retry_async = AsyncMock(
            side_effect=TypeError(
                "unsupported operand type(s) for +: 'NoneType' and 'str'"
            )
        )
        await client.load_entity(MagicMock(), ctx_ok)
        mock_sleep.assert_awaited_once_with(1.25)

        ctx_ok.execute_query_with_incremental_retry_async = AsyncMock(
            side_effect=ClientRequestException(response=MagicMock(status_code=401))
        )
        with pytest.raises(CredentialsExpiredError):
            await client.load_entity(MagicMock(), ctx_ok)

    @patch(
        "idat_sync.infrastructure.sharepoint_client.asyncio.sleep",
        new_callable=AsyncMock,
    )
    @patch("idat_sync.infrastructure.sharepoint_client.HttpxTransport")
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    async def test_init_is_non_blocking_and_configures_transport(
        self, mock_ctx_cls, mock_transport_cls, mock_sleep
    ):
        """Ensure __init__ performs no I/O and sets up auto-retry + HttpxTransport."""
        mock_ctx = MagicMock()
        mock_ctx.with_cookies.return_value = mock_ctx
        mock_ctx.with_auto_retry.return_value = mock_ctx
        mock_ctx_cls.return_value = mock_ctx

        client = SharepointClient(
            cookies=[],
            sharepoint_personal_host="idat628-my.sharepoint.com",
            academic_site_url="https://idat628.sharepoint.com/sites/Academic",
            request_delay=2.0,
        )

        assert client.search_client is None
        assert not hasattr(client, "onedrive_cookies")
        mock_sleep.assert_not_called()
        mock_ctx.web.current_user.get_personal_site.assert_not_called()
        mock_transport_cls.assert_called_once_with()
        assert mock_ctx.with_auto_retry.call_count == 2
        assert (
            mock_ctx.pending_request.return_value.with_async_transport.call_count == 2
        )
        mock_ctx.pending_request.return_value.with_async_transport.assert_called_with(
            mock_transport_cls.return_value
        )

        mock_transport_cls.return_value.aclose = AsyncMock()
        await client.aclose()
        mock_transport_cls.return_value.aclose.assert_awaited_once_with()

    @patch(
        "idat_sync.infrastructure.sharepoint_client.asyncio.sleep",
        new_callable=AsyncMock,
    )
    @patch("idat_sync.infrastructure.sharepoint_client.HttpxTransport")
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    async def test_failed_create_closes_shared_transport(
        self, mock_ctx_cls, mock_transport_cls, mock_sleep
    ):
        """Close shared HttpxTransport when SharepointClient.create() fails."""
        mock_transport_cls.return_value.aclose = AsyncMock()
        mock_ctx_cls.return_value = _make_sharepoint_ctx("")

        with pytest.raises(CredentialsExpiredError):
            await SharepointClient.create(
                cookies=[],
                sharepoint_personal_host="idat628-my.sharepoint.com",
                academic_site_url="https://idat628.sharepoint.com/sites/Academic",
                request_delay=0.0,
            )

        mock_transport_cls.return_value.aclose.assert_awaited_once_with()
        mock_sleep.assert_awaited_once_with(0.0)

    @patch("idat_sync.infrastructure.sharepoint_client.HttpxTransport")
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    async def test_cancelled_create_closes_shared_transport(
        self, mock_ctx_cls, mock_transport_cls
    ):
        """Close shared HttpxTransport when SharepointClient.create() is cancelled."""
        mock_transport_cls.return_value.aclose = AsyncMock()
        mock_ctx = MagicMock()
        mock_ctx.with_cookies.return_value = mock_ctx
        mock_ctx.with_auto_retry.return_value = mock_ctx
        mock_ctx.web.current_user.get_personal_site.return_value.execute_query_async = (
            AsyncMock(side_effect=asyncio.CancelledError())
        )
        mock_ctx_cls.return_value = mock_ctx

        with pytest.raises(asyncio.CancelledError):
            await SharepointClient.create(
                cookies=[],
                sharepoint_personal_host="idat628-my.sharepoint.com",
                academic_site_url="https://idat628.sharepoint.com/sites/Academic",
                request_delay=0.0,
            )

        mock_transport_cls.return_value.aclose.assert_awaited_once_with()

    @patch("idat_sync.infrastructure.sharepoint_client.HttpxTransport")
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    async def test_async_context_manager_closes_shared_transport(
        self, mock_ctx_cls, mock_transport_cls
    ):
        """Close shared HttpxTransport on async context manager exit."""
        mock_ctx = MagicMock()
        mock_ctx.with_cookies.return_value = mock_ctx
        mock_ctx.with_auto_retry.return_value = mock_ctx
        mock_ctx_cls.return_value = mock_ctx
        mock_transport_cls.return_value.aclose = AsyncMock()

        async with SharepointClient(
            cookies=[],
            sharepoint_personal_host="idat628-my.sharepoint.com",
            academic_site_url="https://idat628.sharepoint.com/sites/Academic",
            request_delay=0.0,
        ) as client:
            assert isinstance(client, SharepointClient)

        mock_transport_cls.return_value.aclose.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
