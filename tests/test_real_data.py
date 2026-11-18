"""Integration and developer-script tests using real fixture data."""

import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

import pytest

from idat_sync.application.course_repository import CourseRepository
from idat_sync.application.ports import (
    AuthenticationError,
    RecordingGateway,
    RemoteServiceError,
)
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
from idat_sync.infrastructure.rate_limiter import AsyncRateLimiter
from idat_sync.infrastructure.sharepoint_adapter import SharepointApiAdapter
from idat_sync.infrastructure.sharepoint_client import SharepointClient

REPO_ROOT = Path(__file__).resolve().parent.parent
COURSES_JSON_PATH = Path("./all_courses_contents.json")
CREDENTIALS_JSON_PATH = Path("./session.json")


def _load_script_module(rel_path: str, name: str) -> object:
    script_path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_moodle_json_module(name: str) -> object:
    return _load_script_module("scripts/moodle_json.py", name)


class TestDeveloperScriptsAndGitignore(unittest.IsolatedAsyncioTestCase):
    """Tests for developer CLI scripts and .gitignore rules."""

    def test_scripts_location_gitignore_and_credentials_vocabulary(self):
        """Verify scripts live under scripts/ and sensitive artifacts are ignored."""
        assert not (REPO_ROOT / "login_cli.py").exists()
        assert not (REPO_ROOT / "moodle_json.py").exists()

        login_cli = REPO_ROOT / "scripts" / "login_cli.py"
        moodle_json = REPO_ROOT / "scripts" / "moodle_json.py"
        assert login_cli.is_file()
        assert moodle_json.is_file()
        assert "Credentials" in login_cli.read_text(encoding="utf-8")

        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "all_courses_contents.json" in gitignore
        assert "session*.json" in gitignore

    async def test_moodle_json_script_writes_courses_contents_and_assignments(self):
        """Run moodle_json.main() and verify JSON output without stdout leaks."""
        module = _load_moodle_json_module("moodle_json")
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
            orig_cwd = Path.cwd()
            os.chdir(tmpdir)
            try:
                stdout = io.StringIO()
                mock_store = MagicMock()
                mock_store.load.return_value = None
                with (
                    patch("builtins.input", return_value="jdoe@idat.pe"),
                    patch.object(module.getpass, "getpass", return_value="secret"),
                    patch.object(
                        module, "JsonCredentialStore", return_value=mock_store
                    ),
                    patch.object(
                        module, "MoodleTokenAdapter", return_value=mock_token_adapter
                    ),
                    patch.object(module, "MoodleClient", return_value=mock_client),
                    patch("sys.stdout", stdout),
                ):
                    rc = await module.main()
            finally:
                os.chdir(orig_cwd)

            assert not rc
            out_file = Path(tmpdir) / "all_courses_contents.json"
            assert out_file.is_file()
            payload = json.loads(out_file.read_text(encoding="utf-8"))
            assert payload == [
                {
                    "course": {"id": 10, "fullname": "Algorithms"},
                    "contents": [{"id": 1, "name": "Week 1", "modules": []}],
                    "assignments": [{"id": 501, "cmid": 201, "name": "Lab 1"}],
                }
            ]
            assert '"Algorithms"' not in stdout.getvalue()

    async def test_moodle_json_script_reuses_existing_credentials(self):
        """Reuse saved Credentials without prompting for username or password."""
        module = _load_moodle_json_module("moodle_json_reuse")
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
            orig_cwd = Path.cwd()
            os.chdir(tmpdir)
            try:
                with (
                    patch(
                        "builtins.input",
                        side_effect=AssertionError("should not prompt"),
                    ),
                    patch.object(
                        module.getpass,
                        "getpass",
                        side_effect=AssertionError("should not prompt"),
                    ),
                    patch.object(
                        module, "JsonCredentialStore", return_value=mock_store
                    ),
                    patch.object(
                        module, "MoodleClient", return_value=mock_client
                    ) as mock_client_cls,
                ):
                    rc = await module.main()
            finally:
                os.chdir(orig_cwd)

            assert not rc
            assert mock_client_cls.call_args.args[0] == "saved-tok"
            rate_limiter = mock_client_cls.call_args.kwargs["rate_limiter"]
            assert isinstance(rate_limiter, AsyncRateLimiter)
            assert rate_limiter.delay == load_config().request_delay
            assert (Path(tmpdir) / "all_courses_contents.json").is_file()

    async def test_moodle_json_script_returns_one_on_authentication_error(self):
        """Return 1 and print stderr when MoodleTokenAdapter raises IDATSyncError."""
        module = _load_moodle_json_module("moodle_json_auth_err")
        mock_store = MagicMock()
        mock_store.load.return_value = None

        for err, expected in [
            (AuthenticationError("Invalid login"), "Login failed: Invalid login"),
            (RemoteServiceError("HTTP 500"), "Login failed: HTTP 500"),
        ]:
            mock_token_adapter = MagicMock()
            mock_token_adapter.fetch_token = AsyncMock(side_effect=err)
            stderr = io.StringIO()
            with (
                patch("builtins.input", return_value="jdoe@idat.pe"),
                patch.object(module.getpass, "getpass", return_value="bad"),
                patch.object(module, "JsonCredentialStore", return_value=mock_store),
                patch.object(
                    module, "MoodleTokenAdapter", return_value=mock_token_adapter
                ),
                patch.object(module, "MoodleClient") as mock_client_cls,
                patch("sys.stderr", stderr),
            ):
                rc = await module.main()

            assert rc == 1
            assert expected in stderr.getvalue()
            mock_client_cls.assert_not_called()

    def test_login_cli_script_catches_idat_sync_error_and_returns_one(self):
        """Catch IDATSyncError in login_cli.main(), print to stderr, and return 1."""
        module = _load_script_module("scripts/login_cli.py", "login_cli")
        mock_auth = MagicMock()
        mock_auth.login = AsyncMock(
            side_effect=AuthenticationError("MFA code is required")
        )
        stderr = io.StringIO()
        with (
            patch("builtins.input", return_value="jdoe@idat.pe"),
            patch.object(module.getpass, "getpass", return_value="secret"),
            patch.object(module, "load_config", return_value=load_config()),
            patch.object(module, "create_auth_service", return_value=mock_auth),
            patch("sys.stderr", stderr),
        ):
            rc = module.main()
        assert rc == 1
        assert "Login failed: MFA code is required" in stderr.getvalue()

        mock_auth.login = AsyncMock(return_value=None)
        stdout = io.StringIO()
        with (
            patch("builtins.input", return_value="jdoe@idat.pe"),
            patch.object(module.getpass, "getpass", return_value="secret"),
            patch.object(module, "load_config", return_value=load_config()),
            patch.object(module, "create_auth_service", return_value=mock_auth),
            patch("sys.stdout", stdout),
        ):
            rc_ok = module.main()
        assert not rc_ok
        assert "Credentials saved to" in stdout.getvalue()

    async def test_moodle_json_script_closes_client_on_completion_and_failure(self):
        """Ensure MoodleClient async context manager closes on success and error."""
        module = _load_moodle_json_module("moodle_json_close")
        mock_store = MagicMock()
        mock_store.load.return_value = {
            "token": "saved-tok",
            "sharepoint_storage_state": {"cookies": []},
        }

        ok_client = MagicMock()
        ok_client.__aenter__ = AsyncMock(return_value=ok_client)
        ok_client.__aexit__ = AsyncMock(return_value=False)
        ok_client.fetch_enrolled_courses = AsyncMock(return_value=[])
        ok_client.fetch_assignments = AsyncMock(return_value=[])

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = Path.cwd()
            os.chdir(tmpdir)
            try:
                with (
                    patch.object(
                        module, "JsonCredentialStore", return_value=mock_store
                    ),
                    patch.object(module, "MoodleClient", return_value=ok_client),
                ):
                    rc = await module.main()
            finally:
                os.chdir(orig_cwd)
        assert not rc
        ok_client.__aenter__.assert_awaited_once_with()
        ok_client.__aexit__.assert_awaited_once()

        fail_client = MagicMock()
        fail_client.__aenter__ = AsyncMock(return_value=fail_client)
        fail_client.__aexit__ = AsyncMock(return_value=False)
        fail_client.fetch_enrolled_courses = AsyncMock(
            side_effect=RuntimeError("ws boom")
        )

        with (
            patch.object(module, "JsonCredentialStore", return_value=mock_store),
            patch.object(module, "MoodleClient", return_value=fail_client),
            pytest.raises(RuntimeError, match="ws boom"),
        ):
            await module.main()
        fail_client.__aenter__.assert_awaited_once_with()
        fail_client.__aexit__.assert_awaited_once()


@unittest.skipUnless(
    COURSES_JSON_PATH.is_file(), "Requires ./all_courses_contents.json"
)
class TestRealCourseContentsFixture(unittest.IsolatedAsyncioTestCase):
    """Tests exercising adapters and repository against real course JSON fixture."""

    @classmethod
    def setUpClass(cls):
        """Load the real courses JSON fixture and config once per class."""
        cls.raw_data = json.loads(COURSES_JSON_PATH.read_text(encoding="utf-8"))
        cls.config = load_config()

    def _build_moodle_adapter(self) -> MoodleAdapter:
        mock_moodle_client = MagicMock(spec=MoodleClient)
        mock_moodle_client.fetch_enrolled_courses.return_value = [
            entry["course"] for entry in self.raw_data
        ]
        contents_by_id = {
            int(entry["course"]["id"]): entry.get("contents", [])
            for entry in self.raw_data
            if entry.get("course", {}).get("id") is not None
        }
        mock_moodle_client.fetch_course_contents.side_effect = lambda cid: (
            contents_by_id.get(cid, [])
        )
        mock_moodle_client.fetch_assignments.return_value = [
            {
                "id": entry["course"]["id"],
                "assignments": entry.get("assignments", []),
            }
            for entry in self.raw_data
            if entry.get("course", {}).get("id") is not None
        ]
        return MoodleAdapter(
            client=mock_moodle_client,
            moodle_base_url=self.config.moodle_base_url,
        )

    def _build_sharepoint_adapter(self) -> SharepointApiAdapter:
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
        return SharepointApiAdapter(
            client=mock_sp_client,
            semester_start=self.config.semester_start,
            sharepoint_host=self.config.sharepoint_host,
            sharepoint_personal_host=self.config.sharepoint_personal_host,
            recordings_folder_name=self.config.recordings_folder_name,
            week_folder_prefix=self.config.week_folder_prefix,
            timezone=self.config.timezone,
        )

    def _assert_sharepoint_routing(self, sp_adapter: SharepointApiAdapter) -> None:
        all_urls = [
            c.get("fileurl", "")
            for entry in self.raw_data
            for sec in entry.get("contents", [])
            for mod in sec.get("modules", [])
            for c in mod.get("contents", [])
        ]
        for host in (self.config.sharepoint_host, self.config.sharepoint_personal_host):
            matching = [u for u in all_urls if host in u]
            if matching:
                assert any(sp_adapter.can_handle(u) for u in matching)

    def _assert_assignments_mapped(self, all_remote_contents: list[Content]) -> None:
        expected_assign_ids = {
            int(a["id"])
            for entry in self.raw_data
            for a in entry.get("assignments", [])
            if isinstance(a, dict) and a.get("id") is not None
        }
        if not expected_assign_ids:
            return
        intro_names = [
            item.path.name
            for item in all_remote_contents
            if isinstance(item, StringContent)
            and item.path.name.endswith("-intro.html")
        ]
        assert len(intro_names) > 0

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
            assert expected_att_names.issubset(emitted_names), (
                f"Missing introattachments: {expected_att_names - emitted_names}"
            )

    async def test_moodle_adapter_and_repository_map_real_courses(self):
        """Map real course JSON fixture through adapters, repository, and SyncDiff."""
        moodle_adapter = self._build_moodle_adapter()
        sp_adapter = self._build_sharepoint_adapter()
        repo = CourseRepository(
            course_gateway=moodle_adapter,
            recording_gateway=MagicMock(spec=RecordingGateway),
            url_strategies=[moodle_adapter, sp_adapter],
        )

        courses = await repo.list_courses()
        assert len(courses) == len(self.raw_data)
        assert get_in_progress_course_ids(courses)

        all_remote_contents: list[Content] = []
        for course in courses:
            await repo.populate_course_sections(course)
            course_dir = Path(safe_filename(course.name))
            items = await repo.get_course_content(course, course_dir)
            for item in items:
                assert isinstance(
                    item, (StringContent, MoodleContent, SharepointContent)
                )
                assert not item.path.is_absolute()
                assert item.path.is_relative_to(course_dir)
            all_remote_contents.extend(items)

        assert len(all_remote_contents) > 0
        self._assert_sharepoint_routing(sp_adapter)
        self._assert_assignments_mapped(all_remote_contents)

        diff = SyncDiff.from_trees(all_remote_contents, {})
        assert diff.missing_paths == {item.path for item in all_remote_contents}
        assert diff.synced_paths == set()
        assert diff.outdated_paths == set()


@unittest.skipUnless(
    COURSES_JSON_PATH.is_file() and CREDENTIALS_JSON_PATH.is_file(),
    "Requires ./all_courses_contents.json and ./session.json",
)
class TestLiveCredentialsIntegration(unittest.IsolatedAsyncioTestCase):
    """Live integration tests using local session.json and course fixture."""

    @classmethod
    def setUpClass(cls):
        """Load live credentials and real course fixture."""
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

    def _collect_candidates(self) -> dict[str, list[tuple[str, str, int]]]:
        moodle_host = (urlparse(self.config.moodle_base_url).hostname or "").lower()
        sp_host = self.config.sharepoint_host.lower()
        sp_personal_host = self.config.sharepoint_personal_host.lower()
        buckets: dict[str, list[tuple[str, str, int]]] = {
            "moodle": [],
            "sp_b": [],
            "sp_f": [],
            "sp_direct": [],
            "personal_b": [],
        }
        for filename, url, timemodified in self._iter_module_contents():
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            item = (filename, url, timemodified)
            if host == moodle_host and "/pluginfile.php/" in parsed.path.lower():
                buckets["moodle"].append(item)
            elif host == sp_host and parsed.path.lower().startswith("/:b:/"):
                buckets["sp_b"].append(item)
            elif host == sp_host and parsed.path.lower().startswith("/:f:/"):
                buckets["sp_f"].append(item)
            elif host == sp_host and (
                "uniqueid=" in parsed.query.lower()
                or "sourcedoc=" in parsed.query.lower()
            ):
                buckets["sp_direct"].append(item)
            elif host == sp_personal_host and parsed.path.lower().startswith("/:b:/"):
                buckets["personal_b"].append(item)
        return buckets

    async def _verify_live_moodle_download(
        self, candidates: list[tuple[str, str, int]]
    ) -> None:
        assert candidates, "Expected at least one Moodle file URL"
        assert self.credentials is not None
        moodle_client = MoodleClient(
            self.credentials["token"],
            base_url=self.config.moodle_base_url,
            rate_limiter=AsyncRateLimiter(0.0),
        )
        fn, url, ts = candidates[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / Path(fn).name
            await MoodleDownloader(moodle_client).download(
                MoodleContent(target, ts, url)
            )
            assert target.is_file()
            assert target.stat().st_size > 0

    async def test_live_moodle_download_and_sharepoint_url_resolution(self):
        """Verify live Moodle download and SharePoint URL resolution."""
        buckets = self._collect_candidates()
        await self._verify_live_moodle_download(buckets["moodle"])
        assert self.credentials is not None

        sp_client = await SharepointClient.create(
            self.credentials.get("sharepoint_storage_state", {}).get("cookies", []),
            sharepoint_personal_host=self.config.sharepoint_personal_host,
            academic_site_url=self.config.sharepoint_academic_site_url,
            rate_limiter=AsyncRateLimiter(0.0),
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
        for label, key in [
            ("sharepoint :b:", "sp_b"),
            ("sharepoint :f:", "sp_f"),
            ("sharepoint direct", "sp_direct"),
            ("personal :b:", "personal_b"),
        ]:
            with self.subTest(category=label):
                candidates = buckets[key]
                assert candidates, f"Expected at least one {label} URL"
                resolved_any = False
                for c_fn, c_url, c_ts in candidates[:5]:
                    assert sp_adapter.can_handle(c_url)
                    items = await sp_adapter.list_url_content(c_url, c_fn, c_ts)
                    if items:
                        assert all(isinstance(i, SharepointContent) for i in items)
                        resolved_any = True
                        break
                assert resolved_any, (
                    f"No live {label} candidate resolved to non-empty SharepointContent"
                )
