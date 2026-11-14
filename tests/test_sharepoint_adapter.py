import ast
from datetime import date, datetime
import io
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import quote

from idat_sync.domain.content import RecordingContent, SharepointContent
from idat_sync.infrastructure.sharepoint_adapter import SharepointApiAdapter
from idat_sync.infrastructure.sharepoint_client import SharepointClient


class TestSharepointAdapter(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock(spec=SharepointClient)
        self.semester_start = date(2025, 9, 22)
        self.adapter = SharepointApiAdapter(
            client=self.mock_client,
            semester_start=self.semester_start,
            sharepoint_host="idat628.sharepoint.com",
            sharepoint_personal_host="idat628-my.sharepoint.com",
            recordings_folder_name="Grabaciones",
            week_folder_prefix="Semana",
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

    def test_adapter_does_not_accept_request_delay_or_import_time_or_client_context(self):
        with self.assertRaises(TypeError):
            SharepointApiAdapter(
                client=self.mock_client,
                semester_start=self.semester_start,
                sharepoint_host="idat628.sharepoint.com",
                sharepoint_personal_host="idat628-my.sharepoint.com",
                recordings_folder_name="Grabaciones",
                week_folder_prefix="Semana",
                request_delay=1.25,  # type: ignore[call-arg]
            )

        for attr in ("request_delay", "properties_to_retrieve", "client", "search_client"):
            self.assertFalse(hasattr(self.adapter, attr), f"Unexpected attribute {attr}")

        adapter_path = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "idat_sync"
            / "infrastructure"
            / "sharepoint_adapter.py"
        )
        tree = ast.parse(adapter_path.read_text(encoding="utf-8"), filename=str(adapter_path))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module)
                imported.update(alias.name for alias in node.names)
        self.assertNotIn("time", imported)
        self.assertNotIn("ClientContext", imported)
        self.assertNotIn("office365.sharepoint.client_context", imported)

    def test_fetch_folder_contents_delegates_files_then_subfolders_to_client(self):
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

        def fake_files(folder):
            call_order.append("files")
            return [root_file] if folder is root_folder else [nested_file]

        def fake_subfolders(folder):
            call_order.append("subfolders")
            return [sub_folder] if folder is root_folder else []

        self.mock_client.fetch_folder_files.side_effect = fake_files
        self.mock_client.fetch_subfolders.side_effect = fake_subfolders

        contents = self.adapter.fetch_folder_contents(root_folder)

        self.assertEqual(call_order, ["files", "subfolders", "files", "subfolders"])
        self.assertEqual(len(contents), 2)
        self.assertTrue(all(isinstance(c, SharepointContent) for c in contents))
        self.assertEqual([c.path for c in contents], [Path("syllabus.pdf"), Path("Week 1") / "notes.docx"])

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

    @patch("idat_sync.infrastructure.sharepoint_client.time.sleep")
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    def test_single_label_host_does_not_wildcard_match_unrelated_cookies(self, mock_ctx_cls, mock_sleep):
        mock_ctx = MagicMock()
        mock_ctx.with_cookies.return_value = mock_ctx
        mock_personal_site = MagicMock()
        mock_personal_site.url = "https://intranet/personal/user"
        mock_ctx.web.current_user.get_personal_site.return_value.execute_query_with_incremental_retry.return_value = mock_personal_site
        mock_ctx_cls.return_value = mock_ctx

        cookies = [
            {"name": "FedAuth", "value": "foreign", "domain": ".sharepoint.com"},
            {"name": "rtFa", "value": "exact", "domain": "intranet"},
        ]
        client = SharepointClient(
            cookies=cookies,  # type: ignore[arg-type]
            sharepoint_personal_host="intranet",
            academic_site_url="https://intranet/sites/ACAD",
            request_delay=0.0,
        )

        self.assertEqual(client.sharepoint_cookies, {"rtFa": "exact"})

    @patch("idat_sync.infrastructure.sharepoint_client.time.sleep")
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    def test_sharepoint_client_sdk_queries_and_download_file_sleep_request_delay(
        self, mock_ctx_cls, mock_sleep
    ):
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
            request_delay=1.75,
        )
        mock_sleep.reset_mock()

        mock_folder = MagicMock()
        mock_ctx.web.get_folder_by_guest_url.return_value = mock_folder
        folder_url = "https://contoso.sharepoint.com/:f:/s/Course/folder"
        self.assertIs(client.fetch_folder_by_guest_url(folder_url), mock_folder)
        mock_ctx.web.get_folder_by_guest_url.assert_called_once_with(folder_url)
        mock_ctx.load.assert_called_with(mock_folder, client.properties_to_retrieve)

        mock_guest_file = MagicMock()
        mock_ctx.web.get_file_by_guest_url.return_value = mock_guest_file
        file_url = "https://contoso.sharepoint.com/:b:/s/Course/file"
        self.assertIs(client.fetch_file_by_guest_url(file_url), mock_guest_file)
        mock_ctx.web.get_file_by_guest_url.assert_called_once_with(file_url)

        mock_id_file = MagicMock()
        mock_ctx.web.get_file_by_id.return_value = mock_id_file
        direct_url = "https://contoso.sharepoint.com/sites/Course/doc.aspx?sourcedoc=%7BABC-123%7D"
        self.assertIs(client.fetch_file_by_id(direct_url, "ABC-123"), mock_id_file)
        mock_ctx.web.get_file_by_id.assert_called_once_with("ABC-123")

        child_file = MagicMock()
        mock_folder.get_files.return_value.select.return_value.execute_query_retry.return_value = [child_file]
        self.assertEqual(client.fetch_folder_files(mock_folder), [child_file])
        mock_folder.get_files.return_value.select.assert_called_once_with(client.properties_to_retrieve)

        sub_folder = MagicMock()
        mock_folder.get_folders.return_value.execute_query_retry.return_value = [sub_folder]
        self.assertEqual(client.fetch_subfolders(mock_folder), [sub_folder])

        dest = io.BytesIO()
        client.download_file(mock_guest_file, dest)
        mock_guest_file.download_session.assert_called_once_with(dest)
        mock_guest_file.download_session.return_value.execute_query_with_incremental_retry.assert_called_once_with()

        self.assertEqual(mock_sleep.call_count, 6)
        mock_sleep.assert_called_with(1.75)

    def test_list_url_content_matches_guest_urls_case_insensitively(self):
        custom_adapter = SharepointApiAdapter(
            client=self.mock_client,
            semester_start=self.semester_start,
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            recordings_folder_name="Videos",
            week_folder_prefix="Week",
        )
        mock_file = MagicMock()
        mock_file.name = "slides.pdf"
        mock_file.time_last_modified = None
        self.mock_client.fetch_file_by_guest_url.return_value = mock_file

        guest_url = "https://CONTOSO.SHAREPOINT.COM/:b:/s/Course/abc"
        self.assertTrue(custom_adapter.can_handle(guest_url))
        items = custom_adapter.list_url_content(guest_url, "slides.pdf", 100)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].path, Path("slides.pdf"))
        self.mock_client.fetch_file_by_guest_url.assert_called_once_with(guest_url)

        mock_folder = MagicMock()
        self.mock_client.fetch_folder_by_guest_url.return_value = mock_folder
        self.mock_client.fetch_folder_files.return_value = []
        self.mock_client.fetch_subfolders.return_value = []

        folder_url = "https://CONTOSO.SHAREPOINT.COM/:f:/s/Course/folder"
        custom_adapter.list_url_content(folder_url, "folder", 100)
        self.mock_client.fetch_folder_by_guest_url.assert_called_once_with(folder_url)

    def test_list_url_content_direct_links_extract_id_or_skip_without_calling_client(self):
        mock_file = MagicMock()
        mock_file.name = "handout.docx"
        mock_file.time_last_modified = None
        self.mock_client.fetch_file_by_id.return_value = mock_file

        uniqueid_url = "https://idat628.sharepoint.com/sites/C/Doc.aspx?UniqueId=%7B111-222%7D"
        items_uid = self.adapter.list_url_content(uniqueid_url, "fallback.docx", 50)
        self.assertEqual(len(items_uid), 1)
        self.assertEqual(items_uid[0].path, Path("handout.docx"))
        self.mock_client.fetch_file_by_id.assert_called_once_with(uniqueid_url, "111-222")

        self.mock_client.fetch_file_by_id.reset_mock()
        sourcedoc_url = "https://idat628.sharepoint.com/sites/C/Doc.aspx?sourcedoc=%7B333-444%7D"
        items_src = self.adapter.list_url_content(sourcedoc_url, "fallback.docx", 50)
        self.assertEqual(len(items_src), 1)
        self.mock_client.fetch_file_by_id.assert_called_once_with(sourcedoc_url, "333-444")

        self.mock_client.fetch_file_by_id.reset_mock()
        no_id_url = "https://idat628.sharepoint.com/sites/C/SitePages/Home.aspx"
        self.assertIsNone(self.adapter.fetch_file_from_direct_link(no_id_url))
        self.assertEqual(self.adapter.list_url_content(no_id_url, "Home.aspx", 50), [])
        self.mock_client.fetch_file_by_id.assert_not_called()

    def test_sanitizes_unsafe_sharepoint_folder_file_and_recording_segments(self):
        from datetime import datetime

        # Shared folder + file sanitization preserving UTF-8
        root_folder = MagicMock()
        sub_folder = MagicMock()
        sub_folder.name = "CON"
        sub_folder.serverRelativeUrl = "/sites/C/CON"
        shared_file = MagicMock()
        shared_file.name = "Sesión: 01/Parte?.pdf"
        shared_file.serverRelativeUrl = "/sites/C/CON/Sesión.pdf"
        shared_file.time_last_modified = None

        self.mock_client.fetch_folder_by_guest_url.return_value = root_folder
        self.mock_client.fetch_folder_files.side_effect = [[], [shared_file]]
        self.mock_client.fetch_subfolders.side_effect = [[sub_folder], []]

        folder_url = "https://idat628.sharepoint.com/:f:/s/Course/folder"
        items = self.adapter.list_url_content(folder_url, "folder", 100)
        self.assertEqual(items[0].path, Path("_CON") / "Sesión 01 Parte.pdf")

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
        rec_item.file.serverRelativeUrl = "/sites/Algebra/Grabaciones/video.mp4"
        rec_item.file.time_created = datetime(2025, 9, 22, 10, 0, 0)
        rec_item.file.time_last_modified = datetime(2025, 9, 22, 11, 0, 0)
        rec_item.file.name = "Sesión: 01/Parte?.mp4"
        self.mock_client.fetch_document_library_items.return_value = [rec_item]

        recs = self.adapter.fetch_recordings("Algebra")
        self.assertEqual(recs[0].path, Path("Semana 1") / "Sesión 01 Parte.mp4")

    def test_personal_host_can_handle_path_dispatch_and_empty_odata_null_skip(self):
        personal_file_url = (
            "https://idat628-my.sharepoint.com/:b:/g/personal/user_idat_pe/Ea7D?e=1"
        )
        personal_folder_url = (
            "https://idat628-my.sharepoint.com/:f:/g/personal/user_idat_pe/IgCR?e=1"
        )
        self.assertTrue(self.adapter.can_handle(personal_file_url))
        self.assertTrue(self.adapter.can_handle(personal_folder_url))
        self.assertFalse(self.adapter.can_handle("https://example.com/file.pdf"))

        mock_personal_file = MagicMock()
        mock_personal_file.name = "CLASE1.pdf"
        mock_personal_file.serverRelativeUrl = "/personal/user/Documents/CLASE1.pdf"
        mock_personal_file.time_last_modified = None
        self.mock_client.fetch_file_by_guest_url.return_value = mock_personal_file

        items = self.adapter.list_url_content(personal_file_url, "Clase 1", 100)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].path, Path("CLASE1.pdf"))
        self.mock_client.fetch_file_by_guest_url.assert_called_once_with(
            personal_file_url
        )

        # Dead/expired odata.null entities (both name and serverRelativeUrl falsy) -> []
        dead_entity = MagicMock()
        dead_entity.name = None
        dead_entity.serverRelativeUrl = None
        self.mock_client.fetch_file_by_guest_url.return_value = dead_entity
        self.assertEqual(
            self.adapter.list_url_content(personal_file_url, "fallback.pdf", 100),
            [],
        )

        self.mock_client.fetch_folder_by_guest_url.return_value = dead_entity
        self.assertEqual(
            self.adapter.list_url_content(personal_folder_url, "fallback", 100),
            [],
        )
        self.mock_client.fetch_folder_files.assert_not_called()

        self.mock_client.fetch_file_by_id.return_value = dead_entity
        self.assertEqual(
            self.adapter.list_url_content(
                "https://idat628.sharepoint.com/sites/C/_layouts/15/embed.aspx?UniqueId=111",
                "fallback.mp4",
                100,
            ),
            [],
        )


class TestSharepointClient(unittest.TestCase):
    @patch("idat_sync.infrastructure.sharepoint_client.time.sleep")
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    def test_sharepoint_client_maps_empty_personal_site_and_http_401_403_to_credentials_expired(
        self, mock_ctx_cls, _mock_sleep
    ):
        from office365.runtime.client_request_exception import ClientRequestException
        from idat_sync.application.ports import CredentialsExpiredError
        from idat_sync.infrastructure.sharepoint_client import SharepointClient

        # Empty personal_site.url -> CredentialsExpiredError
        personal_site_empty = MagicMock()
        personal_site_empty.url = ""
        ctx_empty = MagicMock()
        ctx_empty.web.current_user.get_personal_site.return_value.execute_query_with_incremental_retry.return_value = personal_site_empty
        mock_ctx_cls.return_value.with_cookies.return_value = ctx_empty

        with self.assertRaises(CredentialsExpiredError):
            SharepointClient(
                cookies=[],
                sharepoint_personal_host="idat628-my.sharepoint.com",
                academic_site_url="https://idat628.sharepoint.com/sites/Academic",
                request_delay=0.0,
            )

        # Valid init for query tests
        personal_site_ok = MagicMock()
        personal_site_ok.url = "https://idat628-my.sharepoint.com/personal/user"
        ctx_ok = MagicMock()
        ctx_ok.web.current_user.get_personal_site.return_value.execute_query_with_incremental_retry.return_value = personal_site_ok
        mock_ctx_cls.return_value.with_cookies.return_value = ctx_ok

        client = SharepointClient(
            cookies=[],
            sharepoint_personal_host="idat628-my.sharepoint.com",
            academic_site_url="https://idat628.sharepoint.com/sites/Academic",
            request_delay=0.0,
        )

        for status in (401, 403):
            resp = MagicMock(status_code=status)
            err = ClientRequestException(response=resp)
            ctx_ok.search.query.return_value.execute_query_retry.side_effect = err
            with self.assertRaises(CredentialsExpiredError):
                client.search_for_recordings("Algebra")

        resp_404 = MagicMock(status_code=404)
        err_404 = ClientRequestException(response=resp_404)
        ctx_ok.search.query.return_value.execute_query_retry.side_effect = err_404
        with self.assertRaises(ClientRequestException) as ctx:
            client.search_for_recordings("Algebra")
        self.assertNotIsInstance(ctx.exception, CredentialsExpiredError)

    @patch("idat_sync.infrastructure.sharepoint_client.time.sleep")
    @patch("idat_sync.infrastructure.sharepoint_client.ClientContext")
    def test_get_client_from_url_routes_personal_and_load_entity_handles_odata_null_type_error(
        self, mock_ctx_cls, mock_sleep
    ):
        personal_site_ok = MagicMock()
        personal_site_ok.url = "https://idat628-my.sharepoint.com/personal/user"
        ctx_ok = MagicMock()
        ctx_ok.web.current_user.get_personal_site.return_value.execute_query_with_incremental_retry.return_value = personal_site_ok
        mock_ctx_cls.return_value.with_cookies.return_value = ctx_ok

        client = SharepointClient(
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

        # odata.null leaves entity.resource_url=None -> execute_query raises TypeError
        ctx_ok.execute_query_with_incremental_retry.side_effect = TypeError(
            "unsupported operand type(s) for +: 'NoneType' and 'str'"
        )
        client.load_entity(MagicMock(), ctx_ok)
        mock_sleep.assert_called_once_with(1.25)

        # CredentialsExpiredError must still propagate
        from office365.runtime.client_request_exception import ClientRequestException
        from idat_sync.application.ports import CredentialsExpiredError

        ctx_ok.execute_query_with_incremental_retry.side_effect = ClientRequestException(
            response=MagicMock(status_code=401)
        )
        with self.assertRaises(CredentialsExpiredError):
            client.load_entity(MagicMock(), ctx_ok)


if __name__ == "__main__":
    unittest.main()
