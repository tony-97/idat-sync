from datetime import date
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from urllib.parse import urlparse

from idat_sync.application.course_repository import CourseRepository
from idat_sync.application.ports import RecordingGateway
from idat_sync.config import load_config
from idat_sync.domain.content import (
    Content,
    MoodleContent,
    SharepointContent,
    StringContent,
)
from idat_sync.domain.course import get_in_progress_course_ids
from idat_sync.domain.path_utils import safe_filename, safe_path_segment
from idat_sync.domain.sync import SyncDiff
from idat_sync.infrastructure.credential_store import JsonCredentialStore
from idat_sync.infrastructure.downloaders import MoodleDownloader
from idat_sync.infrastructure.moodle_adapter import MoodleAdapter
from idat_sync.infrastructure.moodle_client import MoodleClient
from idat_sync.infrastructure.sharepoint_adapter import SharepointApiAdapter
from idat_sync.infrastructure.sharepoint_client import SharepointClient

REPO_ROOT = Path(__file__).resolve().parent.parent
COURSES_JSON_PATH = Path("./all_courses_contents.json")
CREDENTIALS_JSON_PATH = Path("./session.json")


class TestDeveloperScriptsAndGitignore(unittest.IsolatedAsyncioTestCase):
    def test_scripts_location_gitignore_and_credentials_vocabulary(self):
        self.assertFalse((REPO_ROOT / "login_cli.py").exists())
        self.assertFalse((REPO_ROOT / "moodle_json.py").exists())

        login_cli = REPO_ROOT / "scripts" / "login_cli.py"
        moodle_json = REPO_ROOT / "scripts" / "moodle_json.py"
        self.assertTrue(login_cli.is_file())
        self.assertTrue(moodle_json.is_file())
        self.assertIn("Credentials", login_cli.read_text(encoding="utf-8"))

        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("all_courses_contents.json", gitignore)
        self.assertIn("session*.json", gitignore)

    async def test_moodle_json_script_writes_courses_contents_and_assignments(self):
        script_path = REPO_ROOT / "scripts" / "moodle_json.py"
        spec = importlib.util.spec_from_file_location("moodle_json", script_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        mock_token_adapter = MagicMock()
        mock_token_adapter.fetch_token = AsyncMock(return_value="tok-123")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.fetch_enrolled_courses = AsyncMock(
            return_value=[{"id": 10, "fullname": "Algorithms"}]
        )
        mock_client.fetch_assignments = AsyncMock(
            return_value=[
                {"id": 10, "assignments": [{"id": 501, "cmid": 201, "name": "Lab 1"}]}
            ]
        )
        mock_client.fetch_course_contents = AsyncMock(
            return_value=[{"id": 1, "name": "Week 1", "modules": []}]
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                stdout = io.StringIO()
                mock_store = MagicMock()
                mock_store.load.return_value = None
                with (
                    patch("builtins.input", return_value="jdoe@idat.pe"),
                    patch.object(module.getpass, "getpass", return_value="secret"),
                    patch.object(module, "JsonCredentialStore", return_value=mock_store),
                    patch.object(module, "MoodleTokenAdapter", return_value=mock_token_adapter),
                    patch.object(module, "MoodleClient", return_value=mock_client),
                    patch("sys.stdout", stdout),
                ):
                    rc = await module.main()
            finally:
                os.chdir(orig_cwd)

            self.assertEqual(rc, 0)
            out_file = Path(tmpdir) / "all_courses_contents.json"
            self.assertTrue(out_file.is_file())
            payload = json.loads(out_file.read_text(encoding="utf-8"))
            self.assertEqual(
                payload,
                [
                    {
                        "course": {"id": 10, "fullname": "Algorithms"},
                        "contents": [{"id": 1, "name": "Week 1", "modules": []}],
                        "assignments": [{"id": 501, "cmid": 201, "name": "Lab 1"}],
                    }
                ],
            )
            self.assertNotIn('"Algorithms"', stdout.getvalue())

    async def test_moodle_json_script_reuses_existing_credentials_without_prompting(self):
        script_path = REPO_ROOT / "scripts" / "moodle_json.py"
        spec = importlib.util.spec_from_file_location("moodle_json_reuse", script_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        mock_store = MagicMock()
        mock_store.load.return_value = {
            "token": "saved-tok",
            "sharepoint_storage_state": {"cookies": []},
        }
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.fetch_enrolled_courses = AsyncMock(return_value=[])
        mock_client.fetch_assignments = AsyncMock(return_value=[])

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                with (
                    patch("builtins.input", side_effect=AssertionError("should not prompt")),
                    patch.object(module.getpass, "getpass", side_effect=AssertionError("should not prompt")),
                    patch.object(module, "JsonCredentialStore", return_value=mock_store),
                    patch.object(module, "MoodleClient", return_value=mock_client) as mock_client_cls,
                ):
                    rc = await module.main()
            finally:
                os.chdir(orig_cwd)

            self.assertEqual(rc, 0)
            self.assertEqual(mock_client_cls.call_args.args[0], "saved-tok")
            self.assertTrue((Path(tmpdir) / "all_courses_contents.json").is_file())

    async def test_moodle_json_script_returns_one_on_authentication_error(self):
        from idat_sync.application.ports import AuthenticationError

        script_path = REPO_ROOT / "scripts" / "moodle_json.py"
        spec = importlib.util.spec_from_file_location("moodle_json_auth_err", script_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        mock_store = MagicMock()
        mock_store.load.return_value = None
        mock_token_adapter = MagicMock()
        mock_token_adapter.fetch_token = AsyncMock(
            side_effect=AuthenticationError("Invalid login")
        )

        with (
            patch("builtins.input", return_value="jdoe@idat.pe"),
            patch.object(module.getpass, "getpass", return_value="bad"),
            patch.object(module, "JsonCredentialStore", return_value=mock_store),
            patch.object(module, "MoodleTokenAdapter", return_value=mock_token_adapter),
            patch.object(module, "MoodleClient") as mock_client_cls,
        ):
            rc = await module.main()

        self.assertEqual(rc, 1)
        mock_client_cls.assert_not_called()

    async def test_moodle_json_script_closes_client_on_completion_and_failure(self):
        script_path = REPO_ROOT / "scripts" / "moodle_json.py"
        spec = importlib.util.spec_from_file_location("moodle_json_close", script_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        mock_store = MagicMock()
        mock_store.load.return_value = {
            "token": "saved-tok",
            "sharepoint_storage_state": {"cookies": []},
        }

        # Completion case
        ok_client = MagicMock()
        ok_client.__aenter__ = AsyncMock(return_value=ok_client)
        ok_client.__aexit__ = AsyncMock(return_value=False)
        ok_client.fetch_enrolled_courses = AsyncMock(return_value=[])
        ok_client.fetch_assignments = AsyncMock(return_value=[])

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                with (
                    patch.object(module, "JsonCredentialStore", return_value=mock_store),
                    patch.object(module, "MoodleClient", return_value=ok_client),
                ):
                    rc = await module.main()
            finally:
                os.chdir(orig_cwd)
        self.assertEqual(rc, 0)
        ok_client.__aenter__.assert_awaited_once_with()
        ok_client.__aexit__.assert_awaited_once()

        # Failure case
        fail_client = MagicMock()
        fail_client.__aenter__ = AsyncMock(return_value=fail_client)
        fail_client.__aexit__ = AsyncMock(return_value=False)
        fail_client.fetch_enrolled_courses = AsyncMock(side_effect=RuntimeError("ws boom"))

        with (
            patch.object(module, "JsonCredentialStore", return_value=mock_store),
            patch.object(module, "MoodleClient", return_value=fail_client),
        ):
            with self.assertRaisesRegex(RuntimeError, "ws boom"):
                await module.main()
        fail_client.__aenter__.assert_awaited_once_with()
        fail_client.__aexit__.assert_awaited_once()


@unittest.skipUnless(
    COURSES_JSON_PATH.is_file(), "Requires ./all_courses_contents.json"
)
class TestRealCourseContentsFixture(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_data = json.loads(COURSES_JSON_PATH.read_text(encoding="utf-8"))
        cls.config = load_config()

    async def test_moodle_adapter_and_repository_map_real_courses_and_assignments(self):
        mock_moodle_client = MagicMock(spec=MoodleClient)
        mock_moodle_client.fetch_enrolled_courses.return_value = [
            entry["course"] for entry in self.raw_data
        ]
        contents_by_id = {
            int(entry["course"]["id"]): entry.get("contents", [])
            for entry in self.raw_data
            if entry.get("course", {}).get("id") is not None
        }
        mock_moodle_client.fetch_course_contents.side_effect = (
            lambda cid: contents_by_id.get(cid, [])
        )
        mock_moodle_client.fetch_assignments.return_value = [
            {
                "id": entry["course"]["id"],
                "assignments": entry.get("assignments", []),
            }
            for entry in self.raw_data
            if entry.get("course", {}).get("id") is not None
        ]

        moodle_adapter = MoodleAdapter(
            client=mock_moodle_client,
            moodle_base_url=self.config.moodle_base_url,
        )

        mock_sp_client = MagicMock(spec=SharepointClient)
        mock_sp_file = MagicMock()
        mock_sp_file.name = "sp_item.pdf"
        mock_sp_file.serverRelativeUrl = "/sites/Course/sp_item.pdf"
        mock_sp_file.time_last_modified = None
        mock_sp_folder = MagicMock()
        mock_sp_folder.name = "sp_folder"
        mock_sp_folder.serverRelativeUrl = "/sites/Course/sp_folder"
        mock_sp_client.fetch_file_by_guest_url.return_value = mock_sp_file
        mock_sp_client.fetch_folder_by_guest_url.return_value = mock_sp_folder
        mock_sp_client.fetch_file_by_id.return_value = mock_sp_file
        mock_sp_client.fetch_folder_files.return_value = [mock_sp_file]
        mock_sp_client.fetch_subfolders.return_value = []

        sp_adapter = SharepointApiAdapter(
            client=mock_sp_client,
            semester_start=self.config.semester_start,
            sharepoint_host=self.config.sharepoint_host,
            sharepoint_personal_host=self.config.sharepoint_personal_host,
            recordings_folder_name=self.config.recordings_folder_name,
            week_folder_prefix=self.config.week_folder_prefix,
            timezone=self.config.timezone,
        )

        repo = CourseRepository(
            course_gateway=moodle_adapter,
            recording_gateway=MagicMock(spec=RecordingGateway),
            url_strategies=[moodle_adapter, sp_adapter],
        )

        courses = await repo.list_courses()
        self.assertEqual(len(courses), len(self.raw_data))
        self.assertTrue(get_in_progress_course_ids(courses))

        all_remote_contents: list[Content] = []
        for course in courses:
            await repo.populate_course_sections(course)
            course_dir = Path(safe_filename(course.name))
            items = await repo.get_course_content(course, course_dir)
            for item in items:
                self.assertIsInstance(
                    item, (StringContent, MoodleContent, SharepointContent)
                )
                self.assertFalse(item.path.is_absolute())
                self.assertTrue(item.path.is_relative_to(course_dir))
            all_remote_contents.extend(items)

        self.assertGreater(len(all_remote_contents), 0)

        # Verify both SharePoint hosts are routed to SharepointApiAdapter
        all_urls = [
            c.get("fileurl", "")
            for entry in self.raw_data
            for sec in entry.get("contents", [])
            for mod in sec.get("modules", [])
            for c in mod.get("contents", [])
        ]
        if any(self.config.sharepoint_host in u for u in all_urls):
            self.assertTrue(
                any(
                    sp_adapter.can_handle(u)
                    for u in all_urls
                    if self.config.sharepoint_host in u
                )
            )
        if any(self.config.sharepoint_personal_host in u for u in all_urls):
            self.assertTrue(
                any(
                    sp_adapter.can_handle(u)
                    for u in all_urls
                    if self.config.sharepoint_personal_host in u
                )
            )

        # Verify assignment intro HTML and introattachments mapping when assignments are present
        expected_assign_ids = {
            int(a["id"])
            for entry in self.raw_data
            for a in entry.get("assignments", [])
            if isinstance(a, dict) and a.get("id") is not None
        }
        if expected_assign_ids:
            intro_names = [
                item.path.name
                for item in all_remote_contents
                if isinstance(item, StringContent) and item.path.name.endswith("-intro.html")
            ]
            self.assertGreater(len(intro_names), 0)

            expected_att_names = {
                safe_path_segment(att["filename"])
                for entry in self.raw_data
                for a in entry.get("assignments", [])
                if isinstance(a, dict)
                for att in a.get("introattachments", [])
                if isinstance(att, dict) and att.get("filename")
            }
            if expected_att_names:
                emitted_names = {item.path.name for item in all_remote_contents}
                self.assertTrue(
                    expected_att_names.issubset(emitted_names),
                    f"Missing introattachments: {expected_att_names - emitted_names}",
                )

        diff = SyncDiff.from_trees(all_remote_contents, {})
        self.assertEqual(
            diff.missing_paths, {item.path for item in all_remote_contents}
        )
        self.assertEqual(diff.synced_paths, set())
        self.assertEqual(diff.outdated_paths, set())


@unittest.skipUnless(
    COURSES_JSON_PATH.is_file() and CREDENTIALS_JSON_PATH.is_file(),
    "Requires ./all_courses_contents.json and ./session.json",
)
class TestLiveCredentialsIntegration(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config()
        cls.credentials = JsonCredentialStore(CREDENTIALS_JSON_PATH).load()
        if cls.credentials is None:
            raise unittest.SkipTest("Credentials file could not be loaded")
        cls.raw_data = json.loads(COURSES_JSON_PATH.read_text(encoding="utf-8"))

    def _iter_module_contents(self):
        for entry in self.raw_data:
            for sec in entry.get("contents", []):
                for mod in sec.get("modules", []):
                    for item in mod.get("contents", []):
                        url = item.get("fileurl", "")
                        filename = item.get("filename", "")
                        if url and filename:
                            yield filename, url, int(item.get("timemodified") or 0)

    async def test_live_moodle_download_and_sharepoint_url_resolution(self):
        moodle_host = (urlparse(self.config.moodle_base_url).hostname or "").lower()
        sp_host = self.config.sharepoint_host.lower()
        sp_personal_host = self.config.sharepoint_personal_host.lower()

        moodle_candidates = []
        sp_b_candidates = []
        sp_f_candidates = []
        sp_direct_candidates = []
        sp_personal_b_candidates = []

        for filename, url, timemodified in self._iter_module_contents():
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            path_lower = parsed.path.lower()
            query_lower = parsed.query.lower()

            if host == moodle_host and "/pluginfile.php/" in path_lower:
                moodle_candidates.append((filename, url, timemodified))
            elif host == sp_host and path_lower.startswith("/:b:/"):
                sp_b_candidates.append((filename, url, timemodified))
            elif host == sp_host and path_lower.startswith("/:f:/"):
                sp_f_candidates.append((filename, url, timemodified))
            elif host == sp_host and ("uniqueid=" in query_lower or "sourcedoc=" in query_lower):
                sp_direct_candidates.append((filename, url, timemodified))
            elif host == sp_personal_host and path_lower.startswith("/:b:/"):
                sp_personal_b_candidates.append((filename, url, timemodified))

        # 1. Live Moodle file download
        self.assertGreater(len(moodle_candidates), 0, "Expected at least one Moodle file URL")
        moodle_client = MoodleClient(
            self.credentials["token"],
            base_url=self.config.moodle_base_url,
            request_delay=0.0,
        )
        downloader = MoodleDownloader(moodle_client)
        fn, url, ts = moodle_candidates[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / Path(fn).name
            await downloader.download(MoodleContent(target, ts, url))
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 0)

        # 2. Live SharePoint URL resolution across host & personal_host
        cookies = self.credentials.get("sharepoint_storage_state", {}).get("cookies", [])
        sp_client = await SharepointClient.create(
            cookies,
            sharepoint_personal_host=self.config.sharepoint_personal_host,
            academic_site_url=self.config.sharepoint_academic_site_url,
            request_delay=0.0,
        )
        sp_adapter = SharepointApiAdapter(
            sp_client,
            semester_start=self.config.semester_start,
            sharepoint_host=self.config.sharepoint_host,
            sharepoint_personal_host=self.config.sharepoint_personal_host,
            recordings_folder_name=self.config.recordings_folder_name,
            week_folder_prefix=self.config.week_folder_prefix,
            timezone=self.config.timezone,
        )

        for label, candidates in [
            ("sharepoint :b:", sp_b_candidates),
            ("sharepoint :f:", sp_f_candidates),
            ("sharepoint direct", sp_direct_candidates),
            ("personal :b:", sp_personal_b_candidates),
        ]:
            with self.subTest(category=label):
                self.assertGreater(len(candidates), 0, f"Expected at least one {label} URL")
                resolved_any = False
                for fn, url, ts in candidates[:5]:
                    self.assertTrue(sp_adapter.can_handle(url))
                    items = await sp_adapter.list_url_content(url, fn, ts)
                    if items:
                        self.assertTrue(
                            all(isinstance(i, SharepointContent) for i in items)
                        )
                        resolved_any = True
                        break
                self.assertTrue(
                    resolved_any,
                    f"No live {label} candidate resolved to non-empty SharepointContent",
                )
