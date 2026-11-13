from pathlib import Path
import tempfile
import os
import unittest
from unittest.mock import MagicMock, patch

from idat_sync.application.download_manager import DownloadManager
from idat_sync.application.task_orchestrator import TaskOrchestrator
from idat_sync.domain.content import (
    MoodleContent,
    RecordingContent,
    SharepointContent,
    StringContent,
)
from idat_sync.domain.sync import SyncDiff, SyncStatus
from idat_sync.infrastructure.filesystem import LocalFileSystemAdapter
from idat_sync.infrastructure.downloaders import (
    Downloader,
    MoodleDownloader,
    RecordingDownloader,
    SharepointDownloader,
    StringDownloader,
)


class TestSyncPipeline(unittest.TestCase):
    def setUp(self):
        self.moodle_downloader = MoodleDownloader(
            client=MagicMock(), request_delay=30.0
        )
        self.string_downloader = StringDownloader()
        self.sharepoint_downloader = SharepointDownloader(
            client=MagicMock(), request_delay=30.0
        )
        self.recording_downloader = RecordingDownloader(
            cookies=[], sharepoint_personal_host="idat628-my.sharepoint.com"
        )

        self.groups = [
            [self.moodle_downloader],
            [self.string_downloader],
            [self.sharepoint_downloader, self.recording_downloader],
        ]
        self.downloaders = [d for g in self.groups for d in g]
        self.fs = LocalFileSystemAdapter()
        self.manager = DownloadManager(self.downloaders, file_system=self.fs)
        self.orchestrator = TaskOrchestrator(self.groups)

    def test_prepare_download_tasks_creates_and_filters_existing_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            existing_file = tmp_path / "already_synced.pdf"
            existing_file.write_bytes(b"data")
            os.utime(existing_file, (2000000000, 2000000000))

            outdated_file = tmp_path / "outdated.pdf"
            outdated_file.write_bytes(b"old")
            os.utime(outdated_file, (1500000000, 1500000000))

            missing_file = tmp_path / "new_file.pdf"
            empty_summary = tmp_path / "empty.html"
            valid_summary = tmp_path / "summary.html"

            contents = [
                MoodleContent(existing_file, 1900000000, "https://aulavirtual/1"),
                MoodleContent(outdated_file, 1800000000, "https://aulavirtual/2"),
                SharepointContent(missing_file, 1800000000, MagicMock()),
                StringContent(empty_summary, 0, ["   "]),
                StringContent(valid_summary, 0, ["<p>Hello</p>"]),
            ]

            tasks = self.manager.prepare_download_tasks(contents)

            task_paths = [Path(t.path) for t in tasks]
            self.assertNotIn(existing_file, task_paths)
            self.assertNotIn(empty_summary, task_paths)
            self.assertIn(outdated_file, task_paths)
            self.assertIn(missing_file, task_paths)
            self.assertIn(valid_summary, task_paths)

    def test_task_orchestrator_runs_groups_and_writes_string_tasks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target_html = Path(tmpdir) / "Semana 01" / "resumen.html"
            content = StringContent(target_html, 0, ["<h1>Week 1</h1>"])

            tasks = self.manager.prepare_download_tasks([content])
            self.orchestrator.run(tasks)

            self.assertTrue(target_html.exists())
            self.assertEqual(
                target_html.read_text(encoding="utf-8"), "<h1>Week 1</h1>"
            )

    def test_recording_content_creates_recording_task(self):
        rec = RecordingContent(
            Path("Grabaciones/Semana 1/video.mp4"),
            1700000000,
            "https://idat628-my.sharepoint.com/:v:/r/personal/video.mp4",
        )
        tasks = self.manager.prepare_download_tasks([rec])
        self.assertEqual(len(tasks), 1)
        self.assertIs(tasks[0].downloader, self.recording_downloader)
        self.assertIs(tasks[0].content, rec)

    def test_custom_downloader_extends_without_modifying_base_classes(self):
        downloaded: list[RecordingContent] = []

        class YoutubeRecordingDownloader(Downloader[RecordingContent]):
            content_type = RecordingContent

            def can_handle(self, content: RecordingContent) -> bool:
                return "youtube.com" in content.url

            def download(self, content: RecordingContent) -> None:
                downloaded.append(content)

        yt_downloader = YoutubeRecordingDownloader()
        manager = DownloadManager(
            [self.recording_downloader, yt_downloader], file_system=self.fs
        )
        orchestrator = TaskOrchestrator([[self.recording_downloader, yt_downloader]])

        yt_rec = RecordingContent(
            Path("Grabaciones/Semana 1/yt_video.mp4"),
            1700000000,
            "https://youtube.com/watch?v=123",
        )
        tasks = manager.prepare_download_tasks([yt_rec])
        self.assertEqual(len(tasks), 1)
        self.assertIs(tasks[0].downloader, yt_downloader)

        orchestrator.run(tasks)
        self.assertEqual(downloaded, [yt_rec])

    def test_diff_trees_uses_python_sets_for_sync_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            synced_file = tmp_path / "Semana 01" / "synced.pdf"
            synced_file.parent.mkdir(parents=True, exist_ok=True)
            synced_file.write_bytes(b"ok")
            os.utime(synced_file, (2000000000, 2000000000))

            outdated_file = tmp_path / "Semana 01" / "outdated.pdf"
            outdated_file.write_bytes(b"old")
            os.utime(outdated_file, (1500000000, 1500000000))

            local_only_file = tmp_path / "Semana 01" / "my_notes.txt"
            local_only_file.write_text("personal notes", encoding="utf-8")

            missing_file = tmp_path / "Semana 02" / "new_slides.pdf"

            remote_contents = [
                MoodleContent(synced_file, 1900000000, "https://aulavirtual/1"),
                MoodleContent(outdated_file, 1800000000, "https://aulavirtual/2"),
                SharepointContent(missing_file, 1800000000, MagicMock()),
            ]

            diff = self.manager.diff_trees(tmp_path, remote_contents)

            self.assertEqual(diff.missing_paths, {missing_file})
            self.assertEqual(diff.outdated_paths, {outdated_file})
            self.assertEqual(diff.synced_paths, {synced_file})
            self.assertEqual(diff.local_only_paths, {local_only_file})
            self.assertEqual(
                diff.to_download_paths, {missing_file, outdated_file}
            )
            self.assertEqual(len(diff.pending_contents), 2)

            status_by_path = {e.path: e.status for e in diff.entries}
            self.assertEqual(status_by_path[synced_file], SyncStatus.SYNCED)
            self.assertEqual(status_by_path[outdated_file], SyncStatus.OUTDATED)
            self.assertEqual(status_by_path[missing_file], SyncStatus.MISSING)
            self.assertEqual(
                status_by_path[local_only_file], SyncStatus.LOCAL_ONLY
            )

    def test_safe_filename_at_domain_path_utils_seam(self):
        from idat_sync.domain.path_utils import WINDOWS_RESERVED, safe_filename

        self.assertIn("CON", WINDOWS_RESERVED)
        self.assertIn("NUL", WINDOWS_RESERVED)

        # Accents normalized and forbidden chars replaced
        sanitized = safe_filename("Lección 1: Diseño & Programación?*")
        self.assertEqual(sanitized, "Leccion 1 Diseno & Programacion")

        # Windows reserved device names prefixed
        self.assertEqual(safe_filename("con"), "_con")
        self.assertEqual(safe_filename("NUL"), "_NUL")

        # Default fallback for empty/whitespace
        self.assertEqual(safe_filename("   "), "untitled")

        # Max length enforcement
        self.assertEqual(safe_filename("very_long_file_name", max_len=9), "very_long")

    def test_sync_diff_from_trees_pure_in_memory(self):
        p_missing = Path("semana1/missing.pdf")
        p_outdated = Path("semana1/outdated.pdf")
        p_synced = Path("semana1/synced.pdf")
        p_local_only = Path("semana1/notes.txt")

        c_missing = MoodleContent(p_missing, 1800000000, "http://a/missing")
        c_outdated = MoodleContent(p_outdated, 1900000000, "http://a/outdated")
        c_synced = MoodleContent(p_synced, 1700000000, "http://a/synced")

        remote_contents = [c_synced, c_missing, c_outdated]
        local_mtimes = {
            p_outdated: 1850000000,
            p_synced: 1750000000,
            p_local_only: 1600000000,
        }

        diff = SyncDiff.from_trees(remote_contents, local_mtimes)

        self.assertEqual(diff.missing_paths, {p_missing})
        self.assertEqual(diff.outdated_paths, {p_outdated})
        self.assertEqual(diff.synced_paths, {p_synced})
        self.assertEqual(diff.local_only_paths, {p_local_only})
        self.assertEqual(diff.to_download_paths, {p_missing, p_outdated})
        self.assertEqual(diff.pending_contents, [c_missing, c_outdated])
        self.assertEqual([e.path for e in diff.entries], sorted([p_missing, p_outdated, p_synced, p_local_only]))

    def test_local_filesystem_adapter_and_download_manager_seam(self):
        from idat_sync.infrastructure.filesystem import LocalFileSystemAdapter

        fs = LocalFileSystemAdapter()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            test_sub = tmp_path / "nested" / "dir"
            fs.ensure_dir(test_sub)
            self.assertTrue(fs.exists(test_sub))

            test_file = test_sub / "test.txt"
            test_file.write_text("content", encoding="utf-8")
            os.utime(test_file, (1234567890, 1234567890))

            self.assertTrue(fs.exists(test_file))
            self.assertEqual(fs.get_mtime(test_file), 1234567890)

            mtimes = fs.scan_mtimes(tmp_path)
            self.assertEqual(mtimes.get(test_file), 1234567890)

            mgr = DownloadManager(self.downloaders, file_system=fs)
            content = MoodleContent(test_file, 1234567890, "http://a")
            diff = mgr.diff_trees(tmp_path, [content])
            self.assertEqual(diff.synced_paths, {test_file})

    def test_idat_sync_orchestration_with_injected_collaborators(self):
        from idat_sync.application.sync_service import IDATSync
        from idat_sync.application.course_repository import CourseRepository
        from idat_sync.application.ports import FileSystemPort
        from idat_sync.domain.course import Course

        mock_repo = MagicMock(spec=CourseRepository)
        mock_mgr = MagicMock(spec=DownloadManager)
        mock_orch = MagicMock(spec=TaskOrchestrator)
        mock_fs = MagicMock(spec=FileSystemPort)

        sync = IDATSync(
            course_repository=mock_repo,
            download_manager=mock_mgr,
            task_orchestrator=mock_orch,
            file_system=mock_fs,
            recordings_folder_name="Grabaciones",
        )

        course = Course(id=1, name="Algorithms")
        mock_repo.get_course_content.return_value = []
        mock_repo.get_recordings.return_value = []
        fake_diff = MagicMock()
        fake_diff.pending_contents = []
        mock_mgr.diff_trees.return_value = fake_diff
        mock_mgr.prepare_download_tasks.return_value = []

        result_diff = sync.sync_course(course, Path("target_dir"))

        self.assertIs(result_diff, fake_diff)
        mock_fs.ensure_dir.assert_any_call(Path("target_dir"))
        mock_fs.ensure_dir.assert_any_call(Path("target_dir") / "Grabaciones")
        mock_orch.run.assert_called_once_with([])

        # sync_courses
        mock_repo.get_courses.return_value = [course]
        sync.sync_courses(Path("root_folder"))
        mock_fs.ensure_dir.assert_any_call(Path("root_folder") / "Algorithms")

    def test_hexagonal_architecture_import_boundaries_ast(self):
        import ast

        disallowed_prefixes = (
            "idat_sync.config",
            "idat_sync.infrastructure",
            "idat_sync.ui",
            "requests",
            "office365",
            "yt_dlp",
            "playwright",
        )

        project_root = Path(__file__).resolve().parent.parent
        src_root = project_root / "src" / "idat_sync"
        target_dirs = [src_root / "domain", src_root / "application"]

        violations: list[str] = []
        for target_dir in target_dirs:
            for py_file in target_dir.rglob("*.py"):
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            for dis in disallowed_prefixes:
                                if alias.name == dis or alias.name.startswith(dis + "."):
                                    violations.append(f"{py_file.name}:{node.lineno} imports {alias.name}")
                    elif isinstance(node, ast.ImportFrom):
                        module_name = node.module or ""
                        for dis in disallowed_prefixes:
                            if module_name == dis or module_name.startswith(dis + "."):
                                violations.append(f"{py_file.name}:{node.lineno} imports from {module_name}")

        self.assertEqual(
            violations,
            [],
            f"Found architectural layer violations in domain/application: {violations}",
        )

        infra_violations: list[str] = []
        forbidden_utils_symbols = {"MOODLE_IDAT", "REQUEST_DELAY"}
        for py_file in (src_root / "infrastructure").rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "idat_sync.utils":
                    imported = {alias.name for alias in node.names}
                    bad = imported & forbidden_utils_symbols
                    if bad:
                        infra_violations.append(
                            f"{py_file.name}:{node.lineno} imports {sorted(bad)} from idat_sync.utils"
                        )

        self.assertEqual(
            infra_violations,
            [],
            f"Found forbidden global constant imports in infrastructure: {infra_violations}",
        )

        utils_tree = ast.parse(
            (src_root / "utils.py").read_text(encoding="utf-8"),
            filename=str(src_root / "utils.py"),
        )
        defined_names = {
            node.name
            for node in utils_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        } | {
            target.id
            for node in utils_tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        self.assertNotIn("save_content", defined_names)
        self.assertNotIn("MOODLE_IDAT", defined_names)
        self.assertNotIn("REQUEST_DELAY", defined_names)

    def test_download_manager_requires_filesystem_port(self):
        with self.assertRaises(TypeError):
            DownloadManager(self.downloaders)  # type: ignore[call-arg]

    def test_downloaders_use_injected_request_delay_and_personal_host(self):
        from idat_sync.domain.content import RecordingContent, SharepointContent
        from idat_sync.infrastructure.downloaders import (
            MoodleDownloader,
            RecordingDownloader,
            SharepointDownloader,
        )

        mock_moodle = MagicMock()
        mock_moodle.token = "tok"
        moodle_dl = MoodleDownloader(mock_moodle, request_delay=0.75)

        mock_sp = MagicMock()
        sp_dl = SharepointDownloader(mock_sp, request_delay=1.25)

        rec_dl = RecordingDownloader(
            cookies=[],
            sharepoint_personal_host="contoso-my.sharepoint.com",
        )
        self.assertTrue(
            rec_dl.can_handle(
                RecordingContent(
                    Path("v.mp4"),
                    0,
                    "https://contoso-my.sharepoint.com/:v:/r/personal/u/v.mp4",
                )
            )
        )
        self.assertFalse(
            rec_dl.can_handle(
                RecordingContent(
                    Path("v.mp4"),
                    0,
                    "https://idat628-my.sharepoint.com/:v:/r/personal/u/v.mp4",
                )
            )
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with patch("idat_sync.infrastructure.downloaders.time.sleep") as mock_sleep, patch(
                "idat_sync.infrastructure.downloaders.requests.get"
            ) as mock_get:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.iter_content.return_value = [b"data"]
                mock_get.return_value.__enter__.return_value = mock_resp
                moodle_dl.download(MoodleContent(tmp / "a.pdf", 0, "https://moodle/a.pdf"))
                mock_sleep.assert_called_once_with(0.75)

            with patch("idat_sync.infrastructure.downloaders.time.sleep") as mock_sleep:
                mock_file = MagicMock()
                sp_dl.download(SharepointContent(tmp / "b.pdf", 0, mock_file))
                mock_sleep.assert_called_once_with(1.25)

    def test_idat_sync_uses_injected_recordings_folder_name(self):
        from idat_sync.application.course_repository import CourseRepository
        from idat_sync.application.ports import FileSystemPort
        from idat_sync.application.sync_service import IDATSync
        from idat_sync.domain.course import Course

        mock_repo = MagicMock(spec=CourseRepository)
        mock_mgr = MagicMock(spec=DownloadManager)
        mock_orch = MagicMock(spec=TaskOrchestrator)
        mock_fs = MagicMock(spec=FileSystemPort)

        sync = IDATSync(
            course_repository=mock_repo,
            download_manager=mock_mgr,
            task_orchestrator=mock_orch,
            file_system=mock_fs,
            recordings_folder_name="Videos",
        )

        course = Course(id=1, name="Algorithms")
        mock_repo.get_course_content.return_value = []
        mock_repo.get_recordings.return_value = []
        fake_diff = MagicMock()
        fake_diff.pending_contents = []
        mock_mgr.diff_trees.return_value = fake_diff
        mock_mgr.prepare_download_tasks.return_value = []

        sync.sync_course(course, Path("target_dir"))

        mock_fs.ensure_dir.assert_any_call(Path("target_dir") / "Videos")
        mock_repo.get_recordings.assert_called_once_with("Algorithms", Path("target_dir") / "Videos")

    def test_create_sync_service_wires_sync_config(self):
        from datetime import date
        from idat_sync.bootstrap import create_sync_service
        from idat_sync.config import SyncConfig

        custom_config = SyncConfig(
            semester_start=date(2026, 3, 16),
            request_delay=1.5,
            moodle_base_url="https://lms.example.edu",
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            sharepoint_academic_site_url="https://contoso.sharepoint.com/sites/ACAD",
            recordings_folder_name="Videos",
            week_folder_prefix="Week",
        )
        credentials = {
            "token": "tok123",
            "sharepoint_storage_state": {"cookies": [{"name": "FedAuth", "value": "v", "domain": ".sharepoint.com"}]},
        }

        with patch("idat_sync.bootstrap.MoodleClient") as mock_mc, patch(
            "idat_sync.bootstrap.MoodleAdapter"
        ) as mock_ma, patch("idat_sync.bootstrap.SharepointClient") as mock_sc, patch(
            "idat_sync.bootstrap.SharepointApiAdapter"
        ) as mock_sa, patch(
            "idat_sync.bootstrap.MoodleDownloader"
        ) as mock_md, patch(
            "idat_sync.bootstrap.SharepointDownloader"
        ) as mock_sd, patch(
            "idat_sync.bootstrap.RecordingDownloader"
        ) as mock_rd:
            service = create_sync_service(credentials, config=custom_config)  # type: ignore[arg-type]

        mock_mc.assert_called_once_with(
            "tok123",
            base_url="https://lms.example.edu",
            request_delay=1.5,
        )
        mock_ma.assert_called_once_with(
            mock_mc.return_value,
            moodle_base_url="https://lms.example.edu",
        )
        mock_sc.assert_called_once_with(
            credentials["sharepoint_storage_state"]["cookies"],
            sharepoint_personal_host="contoso-my.sharepoint.com",
            academic_site_url="https://contoso.sharepoint.com/sites/ACAD",
            request_delay=1.5,
        )
        mock_sa.assert_called_once_with(
            mock_sc.return_value,
            semester_start=date(2026, 3, 16),
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            recordings_folder_name="Videos",
            week_folder_prefix="Week",
            request_delay=1.5,
        )
        mock_md.assert_called_once_with(mock_ma.return_value, request_delay=1.5)
        mock_sd.assert_called_once_with(mock_sa.return_value, request_delay=1.5)
        mock_rd.assert_called_once_with(
            credentials["sharepoint_storage_state"]["cookies"],
            sharepoint_personal_host="contoso-my.sharepoint.com",
        )
        self.assertEqual(service.recordings_folder_name, "Videos")

    def test_auth_provider_and_ui_accept_configured_paths_and_hosts(self):
        import asyncio
        import inspect
        from idat_sync.config import SyncConfig
        from idat_sync.infrastructure.auth import AuthProvider
        from idat_sync.ui.app import MainApp
        from idat_sync.ui.sync_frame import SyncFrame
        from idat_sync.utils import get_sharepoint_cookies

        sig = inspect.signature(get_sharepoint_cookies)
        self.assertIn("sharepoint_host", sig.parameters)
        self.assertIn("sharepoint_personal_host", sig.parameters)
        self.assertIn("email_domain", sig.parameters)

        self.assertIn("config", inspect.signature(MainApp.__init__).parameters)
        self.assertIn("config", inspect.signature(SyncFrame.__init__).parameters)

        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir) / "sess.json"
            auth = AuthProvider(
                session_file=session_path,
                moodle_base_url="https://lms.example.edu/",
                sharepoint_host="contoso.sharepoint.com",
                sharepoint_personal_host="contoso-my.sharepoint.com",
                email_domain="example.edu",
            )
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"token": "tok"}
            with patch("idat_sync.infrastructure.auth.requests.post", return_value=mock_resp) as mock_post, patch(
                "idat_sync.infrastructure.auth.get_sharepoint_cookies",
                return_value={"cookies": []},
            ) as mock_cookies:
                ok = asyncio.run(
                    auth.login("jdoe", "pw", asyncio.Event(), lambda: asyncio.sleep(0, result="123456"))  # type: ignore[arg-type]
                )

            self.assertTrue(ok)
            self.assertTrue(session_path.exists())
            mock_post.assert_called_once()
            self.assertEqual(mock_post.call_args.args[0], "https://lms.example.edu/login/token.php")
            mock_cookies.assert_called_once()
            self.assertEqual(mock_cookies.call_args.kwargs["sharepoint_host"], "contoso.sharepoint.com")
            self.assertEqual(
                mock_cookies.call_args.kwargs["sharepoint_personal_host"],
                "contoso-my.sharepoint.com",
            )
            self.assertEqual(mock_cookies.call_args.kwargs["email_domain"], "example.edu")


if __name__ == "__main__":
    unittest.main()
