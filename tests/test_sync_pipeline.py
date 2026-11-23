"""Integration and unit tests for the sync pipeline, downloaders, and architecture."""
# pylint: disable=too-many-lines

import ast
import asyncio
import dataclasses
import inspect
import logging
import os
import platform
import shutil
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from yt_dlp.utils import DownloadCancelled, DownloadError

from idat_sync import domain as domain_mod
from idat_sync.application import events as events_mod
from idat_sync.application import ports
from idat_sync.application.auth_service import AuthService
from idat_sync.application.course_repository import CourseRepository
from idat_sync.application.download_manager import DownloadManager
from idat_sync.application.events import (
    CourseDiscoveryCompleted,
    CourseFileDiscovered,
    CourseSyncCompleted,
    CourseSyncStarted,
    SyncEvent,
    TaskDownloadCompleted,
    TaskDownloadFailed,
    TaskDownloadProgress,
    TaskDownloadStarted,
)
from idat_sync.application.ports import (
    CourseGateway,
    CredentialsExpiredError,
    Downloader,
    DownloadTask,
    FileSystemError,
    FileSystemPort,
    IDATSyncError,
    RecordingGateway,
    RemoteServiceError,
    UrlContentStrategy,
)
from idat_sync.application.sync_service import IDATSync
from idat_sync.application.task_orchestrator import TaskOrchestrator
from idat_sync.bootstrap import (
    configure_logging,
    create_auth_service,
    create_sync_service,
)
from idat_sync.config import SyncConfig
from idat_sync.domain import sync as domain_sync
from idat_sync.domain.content import (
    MoodleContent,
    RecordingContent,
    SharepointContent,
    StringContent,
)
from idat_sync.domain.course import (
    Course,
    CourseModule,
    CourseSection,
    ModuleContent,
)
from idat_sync.domain.path_utils import (
    WINDOWS_RESERVED,
    PathDisambiguator,
    safe_filename,
    safe_path_segment,
    safe_suffixed_segment,
)
from idat_sync.domain.sync import ContentSyncState, classify_content
from idat_sync.infrastructure.credential_store import JsonCredentialStore
from idat_sync.infrastructure.downloaders import (
    MoodleDownloader,
    RecordingDownloader,
    SharepointDownloader,
    StringDownloader,
)
from idat_sync.infrastructure.filesystem import (
    LocalFileSystemAdapter,
    windows_long_path,
)
from idat_sync.infrastructure.moodle_adapter import MoodleAdapter, MoodleTokenAdapter
from idat_sync.infrastructure.moodle_client import MoodleClient, MoodleTokenClient
from idat_sync.infrastructure.rate_limiter import AsyncRateLimiter
from idat_sync.infrastructure.sharepoint_adapter import SharepointApiAdapter
from idat_sync.infrastructure.sharepoint_auth import SharepointBrowserAuth
from idat_sync.infrastructure.sharepoint_client import SharepointClient
from idat_sync.ui.app import MainApp
from idat_sync.ui.login_frame import LoginFrame
from idat_sync.ui.sync_frame import SyncFrame
from tests.test_architecture import iter_module_imports, matches_prefix


def _collect_legacy_and_playwright_imports(
    src_root: Path,
) -> tuple[list[str], set[str]]:
    legacy_imports: list[str] = []
    playwright_importers: set[str] = set()
    for py_file in src_root.rglob("*.py"):
        rel_path = py_file.relative_to(src_root).as_posix()
        for lineno, name in iter_module_imports(py_file):
            if matches_prefix(
                name, ("idat_sync.utils", "idat_sync.infrastructure.auth")
            ):
                legacy_imports.append(f"{rel_path}:{lineno} imports {name}")
            if matches_prefix(name, ("playwright",)):
                playwright_importers.add(rel_path)
    return legacy_imports, playwright_importers


def _collect_downloader_imports(downloaders_path: Path) -> set[str]:
    tree = ast.parse(
        downloaders_path.read_text(encoding="utf-8"),
        filename=str(downloaders_path),
    )
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
            imports.update(alias.name for alias in node.names)
    return imports


def _collect_print_calls(src_root: Path) -> list[str]:
    calls: list[str] = []
    for py_file in src_root.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        calls.extend(
            f"{py_file.relative_to(src_root).as_posix()}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        )
    return calls


async def _consume_events(agen, events: list[SyncEvent]) -> None:
    async for event in agen:
        events.append(event)  # noqa: PERF401


class TestSyncPipeline(unittest.IsolatedAsyncioTestCase):  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """End-to-end and seam tests for synchronization, downloaders, and boundaries."""

    def setUp(self):
        """Initialize downloaders, filesystem adapter, manager, and orchestrator."""
        self.moodle_downloader = MoodleDownloader(client=MagicMock())
        self.string_downloader = StringDownloader()
        self.sharepoint_downloader = SharepointDownloader(client=MagicMock())
        self.recording_downloader = RecordingDownloader(
            cookies=[],
            sharepoint_personal_host="idat628-my.sharepoint.com",
            rate_limiter=AsyncRateLimiter(0.0),
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

    def test_create_download_task_maps_contents_without_filesystem_calls(self):  # pylint: disable=too-many-locals
        """Map Contents to DownloadTasks without touching FileSystemPort."""
        mock_fs = MagicMock(spec=FileSystemPort)
        manager = DownloadManager(self.downloaders, file_system=mock_fs)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            existing_file = tmp_path / "already_synced.pdf"
            missing_file = tmp_path / "new_file.pdf"
            empty_summary = tmp_path / "empty.html"
            valid_summary = tmp_path / "summary.html"

            contents = [
                MoodleContent(existing_file, 1900000000, "https://aulavirtual/1"),
                SharepointContent(missing_file, 1800000000, MagicMock()),
                StringContent(empty_summary, 0, ["   "]),
                StringContent(valid_summary, 0, ["<p>Hello</p>"]),
            ]

            tasks = [
                task
                for content in contents
                if (task := manager.create_download_task(content)) is not None
            ]

            task_paths = [Path(t.path) for t in tasks]
            assert task_paths == [existing_file, missing_file, valid_summary]
            assert empty_summary not in task_paths
            mock_fs.assert_not_called()
            assert not hasattr(DownloadManager, "should_download")
            assert not hasattr(DownloadManager, "diff_trees")
            assert not hasattr(DownloadManager, "prepare_download_tasks")
            assert not hasattr(tasks[0], "last_modified")

    async def test_task_orchestrator_runs_groups_and_writes_string_tasks(self):
        """Run channel workers and write StringContent to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target_html = Path(tmpdir) / "Semana 01" / "resumen.html"
            content = StringContent(target_html, 0, ["<h1>Week 1</h1>"])

            task = self.manager.create_download_task(content)
            assert task is not None
            channel = self.orchestrator.create_channel()
            workers = [
                asyncio.create_task(coro)
                for coro in channel.create_workers(lambda t: t.download())
            ]
            channel.enqueue(task)
            channel.close()
            await asyncio.gather(*workers)

            assert target_html.exists()
            assert target_html.read_text(encoding="utf-8") == "<h1>Week 1</h1>"

    def test_recording_content_creates_recording_task(self):
        """Route RecordingContent to RecordingDownloader."""
        rec = RecordingContent(
            Path("Grabaciones/Semana 1/video.mp4"),
            1700000000,
            "https://idat628-my.sharepoint.com/:v:/r/personal/video.mp4",
        )
        task = self.manager.create_download_task(rec)
        assert task is not None
        assert task.downloader is self.recording_downloader
        assert task.content is rec

    async def test_recording_downloader_reports_cumulative_dash_progress(self):
        """Bridge yt-dlp DASH video+audio progress onto the caller's event loop."""
        ticks: list[tuple[int, int | None, int]] = []
        main_thread = threading.get_ident()

        def _on_progress(downloaded: int, total: int | None) -> None:
            ticks.append((downloaded, total, threading.get_ident()))

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "Grabaciones" / "video.mp4"
            rec = RecordingContent(
                target,
                1700000000,
                "https://idat628-my.sharepoint.com/:v:/r/personal/video.mp4",
            )

            def _fake_ydl(opts):
                hook = opts["progress_hooks"][0]

                class _FakeYdl:
                    def __enter__(self):
                        return self

                    def __exit__(self, *_args):
                        return False

                    def download(self, _urls):
                        """Emit simulated video, audio, and indeterminate ticks."""
                        hook(
                            {
                                "status": "downloading",
                                "downloaded_bytes": 200,
                                "total_bytes_estimate": 400,
                            }
                        )
                        hook(
                            {
                                "status": "finished",
                                "downloaded_bytes": 400,
                                "total_bytes_estimate": 400,
                            }
                        )
                        hook(
                            {
                                "status": "downloading",
                                "downloaded_bytes": 50,
                                "total_bytes": 100,
                            }
                        )
                        hook(
                            {
                                "status": "finished",
                                "downloaded_bytes": 100,
                                "total_bytes": 100,
                            }
                        )
                        hook(
                            {
                                "status": "downloading",
                                "downloaded_bytes": 30,
                                "total_bytes": None,
                                "total_bytes_estimate": None,
                            }
                        )

                return _FakeYdl()

            with patch(
                "idat_sync.infrastructure.downloaders.yt_dlp.YoutubeDL",
                side_effect=_fake_ydl,
            ):
                await self.recording_downloader.download(rec, on_progress=_on_progress)
            await asyncio.sleep(0)

        assert [(d, t) for d, t, _ in ticks] == [
            (200, 400),
            (400, 400),
            (450, 500),
            (500, 500),
            (530, None),
        ]
        assert all(tid == main_thread for _, _, tid in ticks)

    async def test_recording_downloader_uses_exact_target_file_path_even_without_suffix(
        self,
    ):
        """Pass exact target path to yt-dlp outtmpl even without file extension."""
        prefix = "\\\\?\\" if platform.system() == "Windows" else ""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            for rel_path in (
                Path("Grabaciones/Semana 1/video.mp4"),
                Path("Grabaciones/Semana 1/recording_no_ext"),
            ):
                target_path = tmp_path / rel_path
                rec = RecordingContent(
                    target_path,
                    1700000000,
                    "https://idat628-my.sharepoint.com/:v:/r/personal/video.mp4",
                )
                with patch(
                    "idat_sync.infrastructure.downloaders.yt_dlp.YoutubeDL"
                ) as mock_ydl:
                    await self.recording_downloader.download(rec)

                assert target_path.parent.is_dir()
                assert not target_path.is_dir()
                ydl_opts = mock_ydl.call_args.args[0]
                abs_target = await asyncio.to_thread(os.path.abspath, target_path)
                assert ydl_opts["outtmpl"] == f"{prefix}{abs_target}"
                mock_ydl.return_value.__enter__.return_value.download.assert_called_once_with(
                    [rec.url]
                )

    async def test_custom_downloader_extends_without_modifying_base_classes(self):
        """Allow registering a custom Downloader subclass without core changes."""
        downloaded: list[RecordingContent] = []

        class YoutubeRecordingDownloader(Downloader[RecordingContent]):
            """Custom downloader for YouTube recording URLs."""

            content_type = RecordingContent

            def can_handle(self, content: RecordingContent) -> bool:
                """Handle YouTube URLs."""
                return "youtube.com" in content.url

            async def download(self, content: RecordingContent) -> None:  # pylint: disable=arguments-differ
                """Record downloaded content."""
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
        task = manager.create_download_task(yt_rec)
        assert task is not None
        assert task.downloader is yt_downloader

        channel = orchestrator.create_channel()
        channel.enqueue(task)
        channel.close()
        await asyncio.gather(*channel.create_workers())
        assert downloaded == [yt_rec]
        assert hasattr(ports, "ProgressCallback")

        seen_progress: list[tuple[int, int | None]] = []

        class ProgressAwareDownloader(Downloader[RecordingContent]):
            """Downloader accepting on_progress callback."""

            content_type = RecordingContent

            async def download(
                self,
                _content: RecordingContent,
                on_progress: ports.ProgressCallback | None = None,
            ) -> None:
                """Emit progress when provided."""
                if on_progress is not None:
                    on_progress(50, 100)

        p_task = DownloadTask(yt_rec, ProgressAwareDownloader())
        await p_task.download()
        assert not seen_progress
        await p_task.download(on_progress=lambda d, t: seen_progress.append((d, t)))
        assert seen_progress == [(50, 100)]

    def test_safe_filename_at_domain_path_utils_seam(self):
        """Sanitize accents, Windows reserved names, and length in safe_filename."""
        assert "CON" in WINDOWS_RESERVED
        assert "NUL" in WINDOWS_RESERVED

        sanitized = safe_filename("Lección 1: Diseño & Programación?*")
        assert sanitized == "Leccion 1 Diseno & Programacion"
        assert safe_filename("con") == "_con"
        assert safe_filename("NUL") == "_NUL"
        assert safe_filename("   ") == "untitled"
        assert safe_filename("very_long_file_name", max_len=9) == "very_long"
        assert safe_filename("CON.txt") == "_CON.txt"
        assert safe_filename("a" * 98 + "...b", max_len=100) == "a" * 98
        assert safe_filename("." * 105, max_len=100) == "untitled"

    def test_safe_path_segment_preserves_utf8_and_extensions(self):
        """Preserve UTF-8 characters and extensions in safe_path_segment."""
        assert (
            safe_path_segment("Lección 1: Diseño/Guía?.pdf")
            == "Lección 1 Diseño Guía.pdf"
        )
        assert safe_path_segment("CON.txt") == "_CON.txt"
        assert safe_path_segment("..") == "untitled"
        assert safe_path_segment("a\x00\x7fb") == "a b"
        assert safe_path_segment("a" * 110 + ".mp4", max_len=100) == "a" * 96 + ".mp4"

    def test_content_in_folder_rejects_traversal_and_anchored_paths(self):
        """Reject absolute, anchored, and '..' traversal paths in in_folder."""
        for unsafe_item in (Path("/etc/passwd"), Path("../secret.txt")):
            with pytest.raises(ValueError, match="Unsafe content path"):
                StringContent(unsafe_item, 0, ["x"]).in_folder("Course")
        with pytest.raises(ValueError, match="Unsafe content path"):
            StringContent(Path("../secret.txt"), 0, ["x"]).in_folder("")
        with pytest.raises(ValueError, match="Unsafe folder path"):
            StringContent(Path("file.pdf"), 0, ["x"]).in_folder("../outside")

        wrapped = StringContent(Path("file.pdf"), 0, ["x"]).in_folder(
            Path("/tmp/Course")  # noqa: S108
        )
        assert wrapped.path == Path("/tmp/Course/file.pdf")  # noqa: S108

    def test_local_filesystem_adapter_and_download_manager_seam(self):  # pylint: disable=too-many-locals
        """Verify LocalFileSystemAdapter and DownloadManager is_pending behavior."""
        fs = LocalFileSystemAdapter()
        assert not hasattr(FileSystemPort, "exists")
        assert not hasattr(FileSystemPort, "get_mtime")
        assert not hasattr(fs, "exists")
        assert not hasattr(fs, "get_mtime")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            test_sub = tmp_path / "nested" / "dir"
            fs.ensure_dir(test_sub)
            assert test_sub.is_dir()

            test_file = test_sub / "test.txt"
            test_file.write_text("content", encoding="utf-8")
            os.utime(test_file, (1234567890, 1234567890))
            part_file = test_sub / "test.txt.part"
            part_file.write_text("partial", encoding="utf-8")

            mtimes = fs.scan_mtimes(tmp_path)
            assert mtimes.get(test_file) == 1234567890
            assert part_file not in mtimes
            assert fs.read_bytes(test_file) == b"content"
            assert fs.read_bytes(tmp_path / "missing.txt") is None

            mgr = DownloadManager(self.downloaders, file_system=fs)
            assert not hasattr(DownloadManager, "is_pending")
            content = MoodleContent(test_file, 1234567890, "http://a")
            assert mgr.classify_content(content, mtimes) is ContentSyncState.SYNCED

            unchanged_html = tmp_path / "info.html"
            unchanged_html.write_text("<p>Same</p>", encoding="utf-8")
            changed_html = tmp_path / "resumen.html"
            changed_html.write_text("<p>Old</p>", encoding="utf-8")
            empty_html = tmp_path / "empty.html"
            empty_html.write_text("<p>Kept</p>", encoding="utf-8")

            html_mtimes = fs.scan_mtimes(tmp_path)
            assert (
                mgr.classify_content(
                    StringContent(unchanged_html, 0, ["  <p>Same</p>  "]), html_mtimes
                )
                is ContentSyncState.SYNCED
            )
            assert (
                mgr.classify_content(
                    StringContent(changed_html, 0, ["<p>New</p>"]), html_mtimes
                )
                is ContentSyncState.OUTDATED
            )
            assert (
                mgr.classify_content(StringContent(empty_html, 0, ["   "]), html_mtimes)
                is ContentSyncState.SYNCED
            )

    def test_local_filesystem_adapter_translates_os_errors(self):
        """Translate OSError in scan_mtimes and ensure_dir while keeping read_bytes."""
        fs = LocalFileSystemAdapter()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            test_file = tmp_path / "test.txt"
            test_file.write_text("ok", encoding="utf-8")

            with (
                patch.object(Path, "mkdir", side_effect=PermissionError("no perms")),
                pytest.raises(FileSystemError, match="no perms") as dir_err,
            ):
                fs.ensure_dir(tmp_path / "blocked")
            assert isinstance(dir_err.value.__cause__, OSError)

            with (
                patch.object(Path, "rglob", side_effect=OSError("io failure")),
                pytest.raises(FileSystemError, match="io failure") as scan_err,
            ):
                fs.scan_mtimes(tmp_path)
            assert isinstance(scan_err.value.__cause__, OSError)

            with patch.object(
                Path, "read_bytes", side_effect=PermissionError("locked")
            ):
                assert fs.read_bytes(test_file) is None

    async def test_idat_sync_orchestration_with_injected_collaborators(self):
        """Verify IDATSync orchestration events and directory creation."""
        mock_course_gw = MagicMock(spec=CourseGateway)
        mock_course_gw.populate_course_sections = AsyncMock(side_effect=lambda c: c)
        mock_rec_gw = MagicMock(spec=RecordingGateway)
        mock_rec_gw.fetch_recordings = AsyncMock(return_value=[])
        mock_fs = MagicMock(spec=FileSystemPort)
        mock_fs.scan_mtimes.return_value = {}

        sync = IDATSync(
            course_repository=CourseRepository(mock_course_gw, mock_rec_gw, []),
            download_manager=DownloadManager([], mock_fs),
            task_orchestrator=TaskOrchestrator([]),
            file_system=mock_fs,
            recordings_folder_name="Grabaciones",
        )
        assert not hasattr(sync, "get_course_remote_tree")
        assert not hasattr(sync, "check_course_sync")

        course = Course(id=1, name="Algorithms")
        events = [event async for event in sync.sync_course(course, Path("target_dir"))]

        assert events == [
            CourseSyncStarted(course, Path("target_dir")),
            CourseDiscoveryCompleted(course, 0, 0),
            CourseSyncCompleted(course, None),
        ]
        mock_fs.ensure_dir.assert_any_call(Path("target_dir"))
        mock_fs.ensure_dir.assert_any_call(Path("target_dir") / "Grabaciones")

        # sync_courses & list_courses
        mock_course_gw.list_courses = AsyncMock(return_value=[course])
        assert await sync.list_courses() == [course]
        _ = [event async for event in sync.sync_courses(Path("root_folder"), [course])]
        mock_fs.ensure_dir.assert_any_call(Path("root_folder") / "Algorithms")

    def test_hexagonal_architecture_import_boundaries_ast(self):
        """Verify AST import boundaries, legacy cleanup, and zero print() calls."""
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

        violations = [
            f"{py_file.name}:{lineno} imports {name}"
            for target_dir in target_dirs
            for py_file in target_dir.rglob("*.py")
            for lineno, name in iter_module_imports(py_file)
            if matches_prefix(name, disallowed_prefixes)
        ]

        assert not violations, (
            f"Found architectural layer violations in domain/application: {violations}"
        )

        assert not (src_root / "utils.py").exists(), (
            "src/idat_sync/utils.py should be deleted"
        )
        assert not (src_root / "infrastructure" / "auth.py").exists(), (
            "src/idat_sync/infrastructure/auth.py should be deleted"
        )

        legacy_imports, playwright_importers = _collect_legacy_and_playwright_imports(
            src_root
        )

        assert not legacy_imports, f"Found forbidden legacy imports: {legacy_imports}"
        assert playwright_importers == set(), (
            "Playwright imports must not appear anywhere in src/idat_sync/, "
            f"got: {sorted(playwright_importers)}"
        )

        downloader_imports = _collect_downloader_imports(
            src_root / "infrastructure" / "downloaders.py"
        )
        assert "requests" not in downloader_imports
        assert "time" not in downloader_imports
        assert "RQ_HEADER" not in downloader_imports
        assert "REQUEST_HEADER" not in downloader_imports

        moodle_client_imports = _collect_downloader_imports(
            src_root / "infrastructure" / "moodle_client.py"
        )
        assert "asyncio" not in moodle_client_imports
        assert "time" not in moodle_client_imports

        sp_client_imports = _collect_downloader_imports(
            src_root / "infrastructure" / "sharepoint_client.py"
        )
        assert "time" not in sp_client_imports

        print_calls = _collect_print_calls(src_root)
        assert not print_calls, (
            f"Found forbidden print() calls in src/idat_sync/: {print_calls}"
        )

    def test_download_manager_requires_filesystem_port(self):
        """Require file_system argument on DownloadManager."""
        with pytest.raises(TypeError, match="file_system"):
            DownloadManager(self.downloaders)  # type: ignore[call-arg]  # pylint: disable=no-value-for-parameter

    async def test_downloaders_delegate_to_clients_and_use_injected_personal_host(  # pylint: disable=too-many-locals,too-many-statements
        self,
    ):
        """Delegate downloads to injected clients and honor personal host."""
        mock_moodle = MagicMock(spec=MoodleClient)
        moodle_dl = MoodleDownloader(mock_moodle)
        with pytest.raises(TypeError, match="request_delay"):
            MoodleDownloader(mock_moodle, request_delay=0.75)  # type: ignore[call-arg]  # pylint: disable=unexpected-keyword-arg

        mock_sp = MagicMock(spec=SharepointClient)
        sp_dl = SharepointDownloader(mock_sp)
        with pytest.raises(TypeError, match="request_delay"):
            SharepointDownloader(mock_sp, request_delay=1.25)  # type: ignore[call-arg]  # pylint: disable=unexpected-keyword-arg

        rec_dl = RecordingDownloader(
            cookies=[],
            sharepoint_personal_host="contoso-my.sharepoint.com",
            rate_limiter=AsyncRateLimiter(0.0),
        )
        assert rec_dl.can_handle(
            RecordingContent(
                Path("v.mp4"),
                0,
                "https://contoso-my.sharepoint.com/:v:/r/personal/u/v.mp4",
            )
        )
        assert rec_dl.can_handle(
            RecordingContent(
                Path("v.mp4"),
                0,
                "https://CONTOSO-MY.SHAREPOINT.COM/:v:/r/personal/u/v.mp4",
            )
        )
        assert not rec_dl.can_handle(
            RecordingContent(
                Path("v.mp4"),
                0,
                "https://idat628-my.sharepoint.com/:v:/r/personal/u/v.mp4",
            )
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            moodle_cb = MagicMock()

            async def _write_moodle(_url, dest, on_progress=None):
                assert on_progress is moodle_cb
                dest.write(b"moodle-bytes")

            mock_moodle.download_file.side_effect = _write_moodle
            moodle_target = tmp / "nested" / "a.pdf"
            await moodle_dl.download(
                MoodleContent(moodle_target, 0, "https://moodle/a.pdf"),
                on_progress=moodle_cb,
            )
            assert moodle_target.read_bytes() == b"moodle-bytes"
            mock_moodle.download_file.assert_called_once()
            assert mock_moodle.download_file.call_args.args[0] == "https://moodle/a.pdf"
            assert mock_moodle.download_file.call_args.args[1].closed

            mock_file = MagicMock()
            sp_cb = MagicMock()

            async def _write_sp(_file, dest, on_progress=None):
                assert on_progress is sp_cb
                dest.write(b"sp-bytes")

            mock_sp.download_file.side_effect = _write_sp
            sp_target = tmp / "nested_sp" / "b.pdf"
            await sp_dl.download(
                SharepointContent(sp_target, 0, mock_file),
                on_progress=sp_cb,
            )
            assert sp_target.read_bytes() == b"sp-bytes"
            mock_sp.download_file.assert_called_once()
            assert mock_sp.download_file.call_args.args[0] is mock_file
            assert mock_sp.download_file.call_args.args[1].closed

    async def test_downloaders_use_atomic_part_files_and_clean_up_on_failure(self):
        """Write to .part files and unlink them if a download fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            moodle_target = tmp / "moodle" / "a.pdf"
            sp_target = tmp / "sp" / "b.pdf"
            observed_paths: list[str] = []

            async def ok_moodle(_url, fh, on_progress=None):
                del on_progress
                observed_paths.append(fh.name)
                assert fh.name.endswith(".part")
                assert not moodle_target.exists()
                fh.write(b"ok")

            async def failing_sp(_file, fh, on_progress=None):
                del on_progress
                observed_paths.append(fh.name)
                fh.write(b"partial")
                raise RemoteServiceError("network drop")

            mock_moodle = MagicMock(spec=MoodleClient)
            mock_moodle.download_file = AsyncMock(side_effect=ok_moodle)
            mock_sp = MagicMock(spec=SharepointClient)
            mock_sp.download_file = AsyncMock(side_effect=failing_sp)

            await MoodleDownloader(mock_moodle).download(
                MoodleContent(moodle_target, 0, "https://example.com/a.pdf")
            )
            assert moodle_target.read_bytes() == b"ok"
            assert not moodle_target.with_name("a.pdf.part").exists()

            with pytest.raises(RemoteServiceError):
                await SharepointDownloader(mock_sp).download(
                    SharepointContent(sp_target, 0, MagicMock())
                )
            assert not sp_target.exists()
            assert not sp_target.with_name("b.pdf.part").exists()
            assert len(observed_paths) == 2

    async def test_idat_sync_uses_injected_recordings_folder_name(self):
        """Use configured recordings_folder_name when syncing a course."""

        class StubRecordingDownloader(Downloader[RecordingContent]):
            """No-op recording downloader."""

            content_type = RecordingContent

            async def download(
                self,
                _content: RecordingContent,
                on_progress: ports.ProgressCallback | None = None,
            ) -> None:
                """No-op download."""
                del on_progress

        mock_course_gw = MagicMock(spec=CourseGateway)
        mock_course_gw.populate_course_sections = AsyncMock(side_effect=lambda c: c)
        mock_rec_gw = MagicMock(spec=RecordingGateway)
        mock_rec_gw.fetch_recordings = AsyncMock(
            return_value=[RecordingContent(Path("rec.mp4"), 1, "https://rec/1")]
        )
        mock_fs = MagicMock(spec=FileSystemPort)
        mock_fs.scan_mtimes.return_value = {}
        rec_dl = StubRecordingDownloader()

        sync = IDATSync(
            course_repository=CourseRepository(mock_course_gw, mock_rec_gw, []),
            download_manager=DownloadManager([rec_dl], mock_fs),
            task_orchestrator=TaskOrchestrator([[rec_dl]]),
            file_system=mock_fs,
            recordings_folder_name="Videos",
        )

        course = Course(id=1, name="Algorithms")
        events = [event async for event in sync.sync_course(course, Path("target_dir"))]

        mock_fs.ensure_dir.assert_any_call(Path("target_dir") / "Videos")
        mock_rec_gw.fetch_recordings.assert_called_once_with("Algorithms")
        assert (
            CourseFileDiscovered(
                course, Path("target_dir") / "Videos" / "rec.mp4", ContentSyncState.NEW
            )
            in events
        )

    async def test_create_sync_service_wires_sync_config(self):  # noqa: PLR0915  # pylint: disable=too-many-locals,too-many-statements
        """Wire SyncConfig values through create_sync_service to adapters."""
        custom_config = SyncConfig(
            semester_start=date(2026, 3, 16),
            request_delay=1.5,
            moodle_base_url="https://lms.example.edu",
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            sharepoint_academic_site_url="https://contoso.sharepoint.com/sites/ACAD",
            recordings_folder_name="Videos",
            week_folder_prefix="Week",
            timezone="America/Bogota",
        )
        credentials = {
            "token": "tok123",
            "sharepoint_storage_state": {
                "cookies": [
                    {"name": "FedAuth", "value": "v", "domain": ".sharepoint.com"}
                ]
            },
        }

        mock_ctx = MagicMock()
        mock_ctx.with_cookies.return_value = mock_ctx
        mock_ctx.with_auto_retry.return_value = mock_ctx
        mock_personal_site = MagicMock()
        mock_personal_site.url = "https://contoso-my.sharepoint.com/personal/u"
        mock_ctx.web.current_user.get_personal_site.return_value.execute_query_async = (
            AsyncMock(return_value=mock_personal_site)
        )

        with (
            patch(
                "idat_sync.infrastructure.sharepoint_client.ClientContext",
                return_value=mock_ctx,
            ) as mock_ctx_cls,
            patch(
                "idat_sync.infrastructure.rate_limiter.asyncio.sleep",
                new_callable=AsyncMock,
            ) as mock_sleep,
        ):
            service = await create_sync_service(credentials, config=custom_config)  # type: ignore[arg-type]
            sp_adapter = service.course_repository.recording_gateway
            assert isinstance(sp_adapter, SharepointApiAdapter)
            sp_adapter.sp_client.get_client_from_url(
                "https://contoso.sharepoint.com/other"
            )

        called_urls = [call.args[0] for call in mock_ctx_cls.call_args_list]
        assert "https://contoso.sharepoint.com/sites/ACAD" in called_urls
        assert "https://contoso-my.sharepoint.com" in called_urls
        mock_sleep.assert_not_awaited()

        assert service.recordings_folder_name == "Videos"
        moodle_adapter = service.course_repository.course_gateway
        assert isinstance(moodle_adapter, MoodleAdapter)
        assert moodle_adapter.moodle_host == "lms.example.edu"
        assert moodle_adapter.client.base_url == "https://lms.example.edu"
        assert isinstance(moodle_adapter.client.rate_limiter, AsyncRateLimiter)
        assert moodle_adapter.client.rate_limiter.delay == 1.5
        assert not hasattr(moodle_adapter.client, "request_delay")

        sp_adapter = service.course_repository.recording_gateway
        assert isinstance(sp_adapter, SharepointApiAdapter)
        assert sp_adapter.semester_start == date(2026, 3, 16)
        assert sp_adapter.sharepoint_host == "contoso.sharepoint.com"
        assert sp_adapter.sharepoint_personal_host == "contoso-my.sharepoint.com"
        assert sp_adapter.recordings_folder_name == "Videos"
        assert sp_adapter.week_folder_prefix == "Week"
        assert sp_adapter.timezone == ZoneInfo("America/Bogota")
        assert not hasattr(sp_adapter, "request_delay")
        assert isinstance(sp_adapter.sp_client.rate_limiter, AsyncRateLimiter)
        assert sp_adapter.sp_client.rate_limiter.delay == 1.5
        assert (
            sp_adapter.sp_client.rate_limiter is not moodle_adapter.client.rate_limiter
        )
        assert not hasattr(sp_adapter.sp_client, "request_delay")

        downloaders = service.download_manager.strategies
        moodle_dl = next(d for d in downloaders if isinstance(d, MoodleDownloader))
        sp_dl = next(d for d in downloaders if isinstance(d, SharepointDownloader))
        rec_dl = next(d for d in downloaders if isinstance(d, RecordingDownloader))
        assert moodle_dl.moodle_client is moodle_adapter.client
        assert sp_dl.sharepoint_client is sp_adapter.sp_client
        assert not hasattr(moodle_dl, "request_delay")
        assert not hasattr(sp_dl, "request_delay")
        assert rec_dl.sharepoint_personal_host == "contoso-my.sharepoint.com"
        assert rec_dl.rate_limiter is sp_adapter.sp_client.rate_limiter

        with patch.object(
            sp_adapter.sp_client._transport,  # noqa: SLF001  # pylint: disable=protected-access
            "aclose",
            new_callable=AsyncMock,
        ) as mock_sp_transport_aclose:
            await service.aclose()
        assert moodle_adapter.client._client.is_closed  # noqa: SLF001  # pylint: disable=protected-access
        mock_sp_transport_aclose.assert_awaited_once_with()

    async def test_create_sync_service_closes_moodle_on_sharepoint_create_failure(
        self,
    ):
        """Close MoodleClient if SharepointClient.create() raises during bootstrap."""
        mock_moodle_client = MagicMock()
        mock_moodle_client.aclose = AsyncMock()
        with (
            patch("idat_sync.bootstrap.MoodleClient", return_value=mock_moodle_client),
            patch(
                "idat_sync.bootstrap.SharepointClient.create",
                new_callable=AsyncMock,
                side_effect=CredentialsExpiredError("expired"),
            ),
            pytest.raises(CredentialsExpiredError),
        ):
            await create_sync_service(
                {"token": "tok123", "sharepoint_storage_state": {"cookies": []}},  # type: ignore[arg-type]
                config=SyncConfig(request_delay=0.0),
            )

        mock_moodle_client.aclose.assert_awaited_once_with()

    def test_create_auth_service_wires_sync_config_and_ui_avoids_infrastructure(self):
        """Wire SyncConfig into create_auth_service and keep UI off infrastructure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            credentials_path = Path(tmpdir) / "credentials.json"
            custom_config = SyncConfig(
                session_file=credentials_path,
                moodle_base_url="https://lms.example.edu/",
                sharepoint_host="contoso.sharepoint.com",
                sharepoint_personal_host="contoso-my.sharepoint.com",
                email_domain="example.edu",
            )
            auth_service = create_auth_service(custom_config)

        assert isinstance(auth_service, AuthService)
        assert isinstance(auth_service.moodle_auth, MoodleTokenAdapter)
        assert isinstance(auth_service.moodle_auth.client, MoodleTokenClient)
        assert auth_service.moodle_auth.client.base_url == "https://lms.example.edu"
        assert isinstance(auth_service.sharepoint_auth, SharepointBrowserAuth)
        assert auth_service.sharepoint_auth.sharepoint_host == "contoso.sharepoint.com"
        assert (
            auth_service.sharepoint_auth.sharepoint_personal_host
            == "contoso-my.sharepoint.com"
        )
        assert auth_service.sharepoint_auth.email_domain == "example.edu"
        assert isinstance(auth_service.credential_store, JsonCredentialStore)
        assert auth_service.credential_store.credentials_file == credentials_path

        assert "config" in inspect.signature(MainApp.__init__).parameters
        assert "config" in inspect.signature(SyncFrame.__init__).parameters
        assert "auth" in inspect.signature(LoginFrame.__init__).parameters

        ui_root = Path(__file__).resolve().parent.parent / "src" / "idat_sync" / "ui"
        ui_violations = [
            f"{py_file.name}:{lineno} imports {name}"
            for py_file in ui_root.rglob("*.py")
            for lineno, name in iter_module_imports(py_file)
            if matches_prefix(name, ("idat_sync.infrastructure",))
        ]

        assert ui_violations == [], (
            f"Found forbidden infrastructure imports in ui/: {ui_violations}"
        )

    async def test_recording_downloader_maps_http_401_403_to_credentials_expired(
        self,
    ):
        """Map yt-dlp HTTP 401/403 DownloadErrors to CredentialsExpiredError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "Semana 1" / "video.mp4"
            content = RecordingContent(
                target, 1700000000, "https://idat628-my.sharepoint.com/:v:/r/video.mp4"
            )
            dl = RecordingDownloader(
                [], "idat628-my.sharepoint.com", AsyncRateLimiter(0.0)
            )
            with pytest.raises(TypeError, match="request_delay"):
                RecordingDownloader(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                    cookies=[],
                    sharepoint_personal_host="idat628-my.sharepoint.com",
                    request_delay=1.0,  # type: ignore[call-arg]
                )

            with patch(
                "idat_sync.infrastructure.downloaders.yt_dlp.YoutubeDL"
            ) as mock_ydl:
                await dl.download(content)
            assert not dl.rate_limiter._next_allowed  # noqa: SLF001  # pylint: disable=protected-access

            paced_limiter = AsyncRateLimiter(15.0)
            paced_dl = RecordingDownloader(
                [], "idat628-my.sharepoint.com", paced_limiter
            )
            with patch(
                "idat_sync.infrastructure.downloaders.yt_dlp.YoutubeDL"
            ) as mock_ydl:
                await paced_dl.download(content)
            assert paced_limiter._next_allowed > 0.0  # noqa: SLF001  # pylint: disable=protected-access

            for msg in ("HTTP Error 401: Unauthorized", "HTTP Error 403: Forbidden"):
                with patch(
                    "idat_sync.infrastructure.downloaders.yt_dlp.YoutubeDL"
                ) as mock_ydl:
                    dl_mock = mock_ydl.return_value.__enter__.return_value
                    dl_mock.download.side_effect = DownloadError(msg)
                    with pytest.raises(CredentialsExpiredError):
                        await dl.download(content)

            with patch(
                "idat_sync.infrastructure.downloaders.yt_dlp.YoutubeDL"
            ) as mock_ydl:
                dl_mock = mock_ydl.return_value.__enter__.return_value
                dl_mock.download.side_effect = DownloadError(
                    "HTTP Error 404: Not Found"
                )
                with pytest.raises(RemoteServiceError, match="HTTP Error 404") as ctx:
                    await dl.download(content)
                assert not isinstance(ctx.value, CredentialsExpiredError)
                assert isinstance(ctx.value.__cause__, DownloadError)

            with (
                patch.object(Path, "mkdir", side_effect=PermissionError("no mkdir")),
                pytest.raises(FileSystemError, match="no mkdir") as fs_ctx,
            ):
                await dl.download(content)
            assert isinstance(fs_ctx.value.__cause__, OSError)

    async def test_task_orchestrator_channel_fifo_concurrency_errors_and_imports(  # pylint: disable=too-many-locals,too-many-statements
        self,
    ):
        """Verify TaskChannel FIFO, concurrency, idempotent close, and errors."""
        executed: list[str] = []
        g1_started = asyncio.Event()
        g2_started = asyncio.Event()

        class StubDownloader(Downloader[StringContent]):
            """Stub downloader recording execution and coordinating overlap."""

            content_type = StringContent

            def __init__(self, label: str, *, gate: bool = False):
                self.label = label
                self.gate = gate

            async def download(
                self,
                content: StringContent,
                on_progress: ports.ProgressCallback | None = None,
            ) -> None:
                del on_progress
                if content.path.name == "fail.txt":
                    raise CredentialsExpiredError("401 expired")
                if self.gate:
                    if self.label == "g1a":
                        g1_started.set()
                        await g2_started.wait()
                    elif self.label == "g2":
                        g2_started.set()
                        await g1_started.wait()
                executed.append(f"{self.label}:{content.path.name}")

        d1 = StubDownloader("g1a", gate=True)
        d2 = StubDownloader("g1b")
        d3 = StubDownloader("g2", gate=True)
        unregistered = StubDownloader("unknown")
        orch = TaskOrchestrator([[d1, d2], [d3]])
        assert not hasattr(orch, "create_coroutines")

        channel = orch.create_channel()
        with pytest.raises(ValueError, match="Unregistered downloader"):
            channel.enqueue(
                DownloadTask(StringContent(Path("x.txt"), 0, ["x"]), unregistered)
            )

        workers = [asyncio.create_task(c) for c in channel.create_workers()]
        channel.enqueue(DownloadTask(StringContent(Path("a.txt"), 0, ["x"]), d1))
        channel.enqueue(DownloadTask(StringContent(Path("b.txt"), 0, ["x"]), d2))
        channel.enqueue(DownloadTask(StringContent(Path("c.txt"), 0, ["x"]), d3))
        channel.close()
        channel.close()  # idempotent
        await asyncio.gather(*workers)

        assert g1_started.is_set()
        assert g2_started.is_set()
        assert [x for x in executed if x.startswith("g1")] == [
            "g1a:a.txt",
            "g1b:b.txt",
        ]
        assert "g2:c.txt" in executed

        # Unhandled worker exception stops subsequent tasks in that group
        executed.clear()
        err_channel = orch.create_channel()
        err_channel.enqueue(DownloadTask(StringContent(Path("fail.txt"), 0, ["x"]), d2))
        err_channel.enqueue(
            DownloadTask(StringContent(Path("after.txt"), 0, ["x"]), d2)
        )
        err_channel.close()
        with pytest.raises(CredentialsExpiredError):
            await asyncio.gather(*err_channel.create_workers())
        assert "g1b:after.txt" not in executed

        orch_file = (
            Path(__file__).parent.parent
            / "src"
            / "idat_sync"
            / "application"
            / "task_orchestrator.py"
        )
        imported = [name for _, name in iter_module_imports(orch_file)]
        assert not any(
            matches_prefix(name, ("idat_sync.application.events", "threading"))
            for name in imported
        )

    async def test_idat_sync_courses_isolates_errors_and_aborts_on_auth_expired(  # pylint: disable=too-many-locals,too-many-statements
        self,
    ):
        """Isolate single-course failures and abort on CredentialsExpiredError."""
        c1 = Course(
            id=1,
            name="Course One",
            sections=[
                CourseSection(
                    id=1,
                    name="S1",
                    inline_contents=[StringContent(Path("c1.html"), 1, ["a"])],
                    modules=[],
                )
            ],
        )
        c2 = Course(
            id=2,
            name="Course Two",
            sections=[
                CourseSection(
                    id=2,
                    name="S2",
                    inline_contents=[StringContent(Path("c2.html"), 1, ["b"])],
                    modules=[],
                )
            ],
        )

        class StubFileSystem:
            """No-op FileSystemPort stub."""

            def ensure_dir(self, _path: Path) -> None:
                """No-op directory creation."""

            def scan_mtimes(self, _root: Path) -> dict[Path, int]:
                """Return empty mtime map."""
                return {}

        class StubStringDownloader(Downloader[StringContent]):
            """Stub StringDownloader recording target paths."""

            content_type = StringContent

            def __init__(self) -> None:
                self.downloaded: list[Path] = []

            async def download(
                self,
                content: StringContent,
                on_progress: ports.ProgressCallback | None = None,
            ) -> None:
                """Record downloaded path."""
                del on_progress
                self.downloaded.append(content.path)

        mock_course_gw = MagicMock(spec=CourseGateway)
        mock_course_gw.list_courses.return_value = [c1, c2]
        mock_rec_gw = MagicMock(spec=RecordingGateway)
        mock_rec_gw.fetch_recordings.side_effect = [
            RemoteServiceError("course 1 transient failure"),
            [],
        ]

        str_dl = StubStringDownloader()
        repo = CourseRepository(mock_course_gw, mock_rec_gw, [])
        dm = DownloadManager([str_dl], StubFileSystem())  # type: ignore[arg-type]
        orch = TaskOrchestrator([[str_dl]])
        sync = IDATSync(
            repo,
            dm,
            orch,
            StubFileSystem(),  # type: ignore[arg-type]
            "Grabaciones",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertLogs("idat_sync", level="ERROR") as err_logs:
                events: list[SyncEvent] = [
                    event async for event in sync.sync_courses(tmpdir, [c1, c2])
                ]
            assert mock_rec_gw.fetch_recordings.call_count == 2
            assert str_dl.downloaded == [
                Path(tmpdir) / "Course One" / "S1" / "c1.html",
                Path(tmpdir) / "Course Two" / "S2" / "c2.html",
            ]
            assert any("course 1 transient failure" in m for m in err_logs.output)
            completed_courses = [
                (e.course.id, e.error)
                for e in events
                if isinstance(e, CourseSyncCompleted)
            ]
            assert completed_courses[0][0] == 1
            assert isinstance(completed_courses[0][1], RemoteServiceError)
            assert completed_courses[1] == (2, None)

            events.clear()
            str_dl.downloaded.clear()
            mock_rec_gw.fetch_recordings.reset_mock()
            mock_rec_gw.fetch_recordings.side_effect = [
                CredentialsExpiredError("token expired"),
                [],
            ]

            with pytest.raises(CredentialsExpiredError):
                await _consume_events(sync.sync_courses(tmpdir, [c1, c2]), events)
            assert mock_rec_gw.fetch_recordings.call_count == 1
            aborted_courses = [e for e in events if isinstance(e, CourseSyncCompleted)]
            assert len(aborted_courses) == 1
            assert aborted_courses[0].course.id == 1
            assert isinstance(aborted_courses[0].error, CredentialsExpiredError)

    async def test_idat_sync_isolates_task_level_remote_and_filesystem_errors(self):
        """Isolate RemoteServiceError and FileSystemError while keeping bugs fatal."""

        class SelectiveFailingDownloader(Downloader[StringContent]):
            """Downloader raising configured errors per target filename."""

            content_type = StringContent

            def __init__(self) -> None:
                self.downloaded: list[Path] = []
                self.bugs_fatal = False

            async def download(
                self,
                content: StringContent,
                on_progress: ports.ProgressCallback | None = None,
            ) -> None:
                """Raise configured error or record success."""
                del on_progress
                if content.path.name == "remote_err.html":
                    raise RemoteServiceError("upstream 502")
                if content.path.name == "fs_err.html":
                    raise FileSystemError("disk full")
                if self.bugs_fatal and content.path.name == "bug.html":
                    raise RuntimeError("internal bug")
                self.downloaded.append(content.path)

        mock_fs = MagicMock(spec=FileSystemPort)
        mock_fs.scan_mtimes.return_value = {}
        mock_rec_gw = MagicMock(spec=RecordingGateway)
        mock_rec_gw.fetch_recordings = AsyncMock(return_value=[])
        dl = SelectiveFailingDownloader()
        sync = IDATSync(
            CourseRepository(MagicMock(spec=CourseGateway), mock_rec_gw, []),
            DownloadManager([dl], mock_fs),
            TaskOrchestrator([[dl]]),
            mock_fs,
            "Grabaciones",
        )

        course = Course(
            id=1,
            name="Resilient Course",
            sections=[
                CourseSection(
                    id=1,
                    name="S1",
                    inline_contents=[
                        StringContent(Path("remote_err.html"), 1, ["a"]),
                        StringContent(Path("fs_err.html"), 1, ["b"]),
                        StringContent(Path("ok.html"), 1, ["c"]),
                    ],
                )
            ],
        )
        events = [e async for e in sync.sync_course(course, Path("out"))]
        assert dl.downloaded == [Path("out/S1/ok.html")]
        failed = {
            e.path.name: e.error for e in events if isinstance(e, TaskDownloadFailed)
        }
        assert set(failed) == {"remote_err.html", "fs_err.html"}
        assert isinstance(failed["remote_err.html"], RemoteServiceError)
        assert isinstance(failed["fs_err.html"], FileSystemError)
        assert isinstance(events[-1], CourseSyncCompleted)
        assert isinstance(events[-1].error, (RemoteServiceError, FileSystemError))

        dl.bugs_fatal = True
        bug_course = Course(
            id=2,
            name="Bug Course",
            sections=[
                CourseSection(
                    id=2,
                    name="S1",
                    inline_contents=[StringContent(Path("bug.html"), 1, ["x"])],
                )
            ],
        )
        with pytest.raises(ExceptionGroup) as eg_ctx:
            _ = [e async for e in sync.sync_course(bug_course, Path("out2"))]
        assert [str(exc) for exc in eg_ctx.value.exceptions] == ["internal bug"]

    async def test_idat_sync_cancels_sibling_group_tasks_on_credentials_expired(self):  # pylint: disable=too-many-locals,too-many-statements
        """Cancel sibling groups when a task raises CredentialsExpiredError."""
        sibling_started = asyncio.Event()
        discovery_started = asyncio.Event()

        class FailingDownloader(Downloader[StringContent]):
            """Stub downloader raising CredentialsExpiredError."""

            content_type = StringContent

            def can_handle(self, content: StringContent) -> bool:
                """Handle auth.html only."""
                return content.path.name == "auth.html"

            async def download(
                self,
                _content: StringContent,
                on_progress: ports.ProgressCallback | None = None,
            ) -> None:
                """Wait for sibling + discovery, then raise CredentialsExpiredError."""
                del on_progress
                await sibling_started.wait()
                await discovery_started.wait()
                raise CredentialsExpiredError("401 expired")

        class SiblingDownloader(Downloader[MoodleContent]):
            """Stub sibling downloader tracking cancellation."""

            content_type = MoodleContent

            def __init__(self) -> None:
                self.cancelled = False
                self.finished = False

            async def download(
                self,
                _content: MoodleContent,
                on_progress: ports.ProgressCallback | None = None,
            ) -> None:
                """Sleep until cancelled."""
                del on_progress
                sibling_started.set()
                try:
                    await asyncio.sleep(5)
                    self.finished = True
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

        mock_fs = MagicMock(spec=FileSystemPort)
        mock_fs.scan_mtimes.return_value = {}

        class StubMoodleStrategy(UrlContentStrategy):
            """Stub strategy resolving Moodle links without network I/O."""

            def can_handle(self, url: str) -> bool:
                """Accept moodle URLs."""
                return url.startswith("https://moodle/")

            async def list_url_content(
                self, url: str, filename: str, last_modified: int
            ) -> list[MoodleContent]:
                """Return a single MoodleContent item."""
                return [MoodleContent(Path(filename), last_modified, url)]

        c1 = Course(
            id=1,
            name="Sibling Cancel Course",
            sections=[
                CourseSection(
                    id=1,
                    name="S1",
                    inline_contents=[StringContent(Path("auth.html"), 1, ["a"])],
                    modules=[
                        CourseModule(
                            id=1,
                            name="M1",
                            contents=[
                                ModuleContent("sib.pdf", "https://moodle/sib.pdf", 1)
                            ],
                        )
                    ],
                )
            ],
        )
        discovery_cancelled = False

        async def _slow_recordings(_course_name: str) -> list[RecordingContent]:
            nonlocal discovery_cancelled
            discovery_started.set()
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                discovery_cancelled = True
                raise
            return []

        mock_course_gw = MagicMock(spec=CourseGateway)
        mock_rec_gw = MagicMock(spec=RecordingGateway)
        mock_rec_gw.fetch_recordings.side_effect = _slow_recordings

        failing_dl = FailingDownloader()
        sibling_dl = SiblingDownloader()
        repo = CourseRepository(mock_course_gw, mock_rec_gw, [StubMoodleStrategy()])

        dm = DownloadManager([failing_dl, sibling_dl], mock_fs)
        orch = TaskOrchestrator([[failing_dl], [sibling_dl]])
        sync = IDATSync(repo, dm, orch, mock_fs, "Grabaciones")

        events: list[SyncEvent] = []

        with pytest.raises(CredentialsExpiredError):
            await _consume_events(sync.sync_course(c1, Path("out")), events)

        assert sibling_dl.cancelled
        assert not sibling_dl.finished
        assert discovery_cancelled
        assert any(isinstance(e, TaskDownloadFailed) for e in events)
        assert isinstance(events[-1], CourseSyncCompleted)
        assert isinstance(events[-1].error, CredentialsExpiredError)

    async def test_idat_sync_cancels_active_worker_on_discovery_credentials_expired(
        self,
    ):
        """Cancel active download workers on discovery CredentialsExpiredError."""
        worker_started = asyncio.Event()
        worker_cancelled = False

        class SlowStringDownloader(Downloader[StringContent]):
            """Stub downloader that blocks until cancelled."""

            content_type = StringContent

            async def download(
                self,
                _content: StringContent,
                on_progress: ports.ProgressCallback | None = None,
            ) -> None:
                """Signal start and sleep until cancelled."""
                del on_progress
                nonlocal worker_cancelled
                worker_started.set()
                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    worker_cancelled = True
                    raise

        async def _failing_recordings(_course_name: str) -> list[RecordingContent]:
            await worker_started.wait()
            raise CredentialsExpiredError("401 discovery expired")

        mock_course_gw = MagicMock(spec=CourseGateway)
        mock_rec_gw = MagicMock(spec=RecordingGateway)
        mock_rec_gw.fetch_recordings.side_effect = _failing_recordings
        mock_fs = MagicMock(spec=FileSystemPort)
        mock_fs.scan_mtimes.return_value = {}

        dl = SlowStringDownloader()
        sync = IDATSync(
            CourseRepository(mock_course_gw, mock_rec_gw, []),
            DownloadManager([dl], mock_fs),
            TaskOrchestrator([[dl]]),
            mock_fs,
            "Grabaciones",
        )
        course = Course(
            id=1,
            name="Discovery Auth Fail",
            sections=[
                CourseSection(
                    id=1,
                    name="S1",
                    inline_contents=[StringContent(Path("slow.html"), 1, ["a"])],
                )
            ],
        )
        events: list[SyncEvent] = []
        with pytest.raises(CredentialsExpiredError, match="401 discovery expired"):
            await _consume_events(sync.sync_course(course, Path("out")), events)

        assert worker_cancelled
        assert isinstance(events[-1], CourseSyncCompleted)
        assert isinstance(events[-1].error, CredentialsExpiredError)

    async def test_idat_sync_courses_isolates_pre_stream_error(self):
        """Isolate pre-stream IDATSyncError on course 1 and still sync course 2."""
        c1 = Course(id=1, name="Course One")
        c2 = Course(
            id=2,
            name="Course Two",
            sections=[CourseSection(id=2, name="S2")],
        )
        mock_course_gw = MagicMock(spec=CourseGateway)
        mock_course_gw.populate_course_sections = AsyncMock(
            side_effect=RemoteServiceError("pre-stream 503")
        )
        mock_rec_gw = MagicMock(spec=RecordingGateway)
        mock_rec_gw.fetch_recordings = AsyncMock(return_value=[])
        mock_fs = MagicMock(spec=FileSystemPort)
        mock_fs.scan_mtimes.return_value = {}

        sync = IDATSync(
            CourseRepository(mock_course_gw, mock_rec_gw, []),
            DownloadManager([], mock_fs),
            TaskOrchestrator([]),
            mock_fs,
            "Grabaciones",
        )

        events = [event async for event in sync.sync_courses(Path("root"), [c1, c2])]
        completed = [
            (e.course.id, e.error) for e in events if isinstance(e, CourseSyncCompleted)
        ]
        assert completed[0][0] == 1
        assert isinstance(completed[0][1], RemoteServiceError)
        assert completed[1] == (2, None)

    async def test_idat_sync_list_courses_lazy_populate_and_filtering(self):  # pylint: disable=too-many-locals
        """Lazily populate sections and filter already-synced or empty artifacts."""
        c_lazy = Course(id=10, name="Lazy Course", category="IV.01.2026-III")

        async def populate(course: Course) -> Course:
            course.sections = [
                CourseSection(
                    id=1,
                    name="S1",
                    inline_contents=[
                        StringContent(Path("empty.html"), 0, ["   "]),
                        StringContent(Path("synced.html"), 100, ["ok"]),
                        StringContent(Path("missing.html"), 100, ["new"]),
                    ],
                    modules=[],
                )
            ]
            return course

        mock_course_gw = MagicMock(spec=CourseGateway)
        mock_course_gw.list_courses.return_value = [c_lazy]
        mock_course_gw.populate_course_sections.side_effect = populate
        mock_rec_gw = MagicMock(spec=RecordingGateway)
        mock_rec_gw.fetch_recordings.return_value = []

        fs = LocalFileSystemAdapter()
        str_dl = StringDownloader()
        repo = CourseRepository(mock_course_gw, mock_rec_gw, [])
        dm = DownloadManager([str_dl], fs)
        orch = TaskOrchestrator([[str_dl]])
        sync = IDATSync(repo, dm, orch, fs, "Grabaciones")

        assert await sync.list_courses() == [c_lazy]

        with tempfile.TemporaryDirectory() as tmpdir:
            course_dir = Path(tmpdir) / "Lazy Course"
            synced_file = course_dir / "S1" / "synced.html"
            synced_file.parent.mkdir(parents=True, exist_ok=True)
            synced_file.write_text("ok", encoding="utf-8")
            os.utime(synced_file, (200, 200))

            events: list[SyncEvent] = [
                event async for event in sync.sync_course(c_lazy, course_dir)
            ]

            mock_course_gw.populate_course_sections.assert_called_once_with(c_lazy)
            missing_file = course_dir / "S1" / "missing.html"
            assert events[0] == CourseSyncStarted(c_lazy, course_dir)
            assert events[-1] == CourseSyncCompleted(c_lazy, None)
            assert (
                CourseFileDiscovered(c_lazy, synced_file, ContentSyncState.SYNCED)
                in events
            )
            assert (
                CourseFileDiscovered(c_lazy, missing_file, ContentSyncState.NEW)
                in events
            )
            assert CourseDiscoveryCompleted(c_lazy, 1, 1) in events
            assert TaskDownloadStarted(missing_file) in events
            assert TaskDownloadCompleted(missing_file) in events
            assert events.index(
                CourseFileDiscovered(c_lazy, missing_file, ContentSyncState.NEW)
            ) < events.index(TaskDownloadStarted(missing_file))
            assert all(
                not (
                    isinstance(e, CourseFileDiscovered) and e.path.name == "empty.html"
                )
                for e in events
            )
            with pytest.raises(TypeError, match="progress_listener"):
                sync.sync_course(c_lazy, course_dir, progress_listener=None)  # type: ignore[call-arg]  # pylint: disable=unexpected-keyword-arg

    def test_sync_events_contract_log_sync_event_and_ports_cleanup(self):  # pylint: disable=too-many-locals
        """Verify SyncEvent immutability and log_sync_event log levels."""
        assert not hasattr(ports, "SyncProgressPort")
        assert hasattr(events_mod, "SyncEvent")
        assert not hasattr(events_mod, "CourseFilesDiscovered")
        assert not hasattr(events_mod, "EventPublisher")
        assert not hasattr(events_mod, "noop_publisher")

        course = Course(id=1, name="Algebra")
        started = events_mod.CourseSyncStarted(
            course=course,
            course_dir=Path("/tmp/Algebra"),  # noqa: S108
        )
        file_discovered = events_mod.CourseFileDiscovered(
            course=course,
            path=Path("/tmp/Algebra/b.pdf"),  # noqa: S108
            state=ContentSyncState.NEW,
        )
        discovery_done = events_mod.CourseDiscoveryCompleted(
            course=course,
            synced_count=1,
            pending_count=1,
        )
        t_start = events_mod.TaskDownloadStarted(path=Path("/tmp/Algebra/b.pdf"))  # noqa: S108
        t_prog = events_mod.TaskDownloadProgress(
            path=Path("/tmp/Algebra/b.pdf"),  # noqa: S108
            downloaded=512,
            total=1024,
        )
        t_done = events_mod.TaskDownloadCompleted(path=Path("/tmp/Algebra/b.pdf"))  # noqa: S108
        t_fail = events_mod.TaskDownloadFailed(
            path=Path("/tmp/Algebra/b.pdf"),  # noqa: S108
            error=FileSystemError("disk full"),
        )
        c_ok = events_mod.CourseSyncCompleted(course=course)
        c_err = events_mod.CourseSyncCompleted(
            course=course, error=RemoteServiceError("boom")
        )
        assert isinstance(t_fail.error, IDATSyncError)
        assert isinstance(c_err.error, IDATSyncError)

        for ev in (
            started,
            file_discovered,
            discovery_done,
            t_start,
            t_prog,
            t_done,
            t_fail,
            c_ok,
            c_err,
        ):
            assert dataclasses.is_dataclass(ev)
            with pytest.raises(dataclasses.FrozenInstanceError):
                setattr(ev, "course" if hasattr(ev, "course") else "path", None)

        with self.assertLogs("idat_sync", level="DEBUG") as cm:
            events_mod.log_sync_event(started)
            events_mod.log_sync_event(file_discovered)
            events_mod.log_sync_event(discovery_done)
            events_mod.log_sync_event(t_start)
            events_mod.log_sync_event(t_prog)
            events_mod.log_sync_event(t_done)
            events_mod.log_sync_event(c_ok)
            events_mod.log_sync_event(t_fail)
            events_mod.log_sync_event(c_err)

        assert [r.levelname for r in cm.records] == [
            "INFO",
            "DEBUG",
            "INFO",
            "INFO",
            "INFO",
            "INFO",
            "ERROR",
            "ERROR",
        ]
        assert any("Course 'Algebra': 1 synced, 1 pending." in msg for msg in cm.output)

    async def test_bootstrap_configure_logging_and_event_logging(self):
        """Configure logger handlers idempotently and avoid legacy publish attrs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "nested" / "logs" / "app.log"
            logger1 = configure_logging(log_file)
            logger2 = configure_logging(log_file)
            try:
                assert logger1 is logger2
                assert logger2.name == "idat_sync"
                assert logger2.level == logging.INFO
                assert not logger2.propagate
                assert len(logger2.handlers) == 2
                assert any(isinstance(h, logging.FileHandler) for h in logger2.handlers)
                assert any(
                    type(h) is logging.StreamHandler  # pylint: disable=unidiomatic-typecheck
                    for h in logger2.handlers
                )
            finally:
                for h in list(logger2.handlers):
                    h.close()
                    logger2.removeHandler(h)

        mock_ctx = MagicMock()
        mock_ctx.with_cookies.return_value = mock_ctx
        mock_ctx.with_auto_retry.return_value = mock_ctx
        mock_personal_site = MagicMock()
        mock_personal_site.url = "https://idat628-my.sharepoint.com/personal/u"
        mock_ctx.web.current_user.get_personal_site.return_value.execute_query_async = (
            AsyncMock(return_value=mock_personal_site)
        )

        credentials = {
            "token": "tok123",
            "sharepoint_storage_state": {
                "cookies": [
                    {"name": "FedAuth", "value": "v", "domain": ".sharepoint.com"}
                ]
            },
        }
        with (
            patch(
                "idat_sync.infrastructure.sharepoint_client.ClientContext",
                return_value=mock_ctx,
            ),
            patch(
                "idat_sync.infrastructure.rate_limiter.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            service = await create_sync_service(
                credentials,  # type: ignore[arg-type]
                config=SyncConfig(request_delay=0.0),
            )

        assert not hasattr(service, "publish")
        assert not hasattr(service.task_orchestrator, "publish")

    async def test_string_downloader_writes_atomically_and_cleans_up_on_error(
        self,
    ):
        """Write StringContent via .part file and unlink .part on replace error."""
        dl = StringDownloader()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "sub" / "note.html"
            part_path = target.with_name("note.html.part")

            progress_calls: list[tuple[int, int | None]] = []
            with patch.object(
                Path, "replace", autospec=True, wraps=Path.replace
            ) as mock_replace:
                await dl.download(
                    StringContent(target, 0, ["  <p>Hola</p>  "]),
                    on_progress=lambda d, t: progress_calls.append((d, t)),
                )

            mock_replace.assert_called_once_with(
                windows_long_path(part_path), windows_long_path(target)
            )
            assert target.read_text(encoding="utf-8") == "<p>Hola</p>"
            assert not part_path.exists()
            assert not progress_calls

            with (
                patch.object(Path, "replace", side_effect=OSError("disk error")),
                pytest.raises(FileSystemError, match="disk error") as err_ctx,
            ):
                await dl.download(StringContent(target, 0, ["<p>Corrupted</p>"]))
            assert isinstance(err_ctx.value.__cause__, OSError)

            assert target.read_text(encoding="utf-8") == "<p>Hola</p>"
            assert not part_path.exists()

    async def test_idat_sync_sync_courses_invalidates_repository_cache_once(self):
        """Invalidate repository cache once before iterating courses."""
        call_order: list[str] = []
        mock_course_gw = MagicMock(spec=CourseGateway)
        mock_course_gw.invalidate_cache.side_effect = lambda: call_order.append(
            "invalidate"
        )

        async def _populate(course: Course) -> Course:
            call_order.append(f"populate:{course.id}")
            return course

        mock_course_gw.populate_course_sections = AsyncMock(side_effect=_populate)
        mock_rec_gw = MagicMock(spec=RecordingGateway)
        mock_rec_gw.fetch_recordings = AsyncMock(return_value=[])

        fs = LocalFileSystemAdapter()
        sync = IDATSync(
            CourseRepository(mock_course_gw, mock_rec_gw, []),
            DownloadManager([], fs),
            TaskOrchestrator([]),
            fs,
            "Grabaciones",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            courses = [Course(id=1, name="C1"), Course(id=2, name="C2")]
            _ = [event async for event in sync.sync_courses(tmpdir, courses)]

        mock_course_gw.invalidate_cache.assert_called_once_with()
        assert call_order == ["invalidate", "populate:1", "populate:2"]

    async def test_idat_sync_aclose_and_context_manager_run_all_closers_once(self):
        """Run all registered closers once and surface the first error."""
        mock_repo = MagicMock(spec=CourseRepository)
        fs = LocalFileSystemAdapter()
        dm = DownloadManager([], fs)
        orch = TaskOrchestrator([])

        closer_a = AsyncMock(side_effect=RuntimeError("closer_a failed"))
        closer_b = AsyncMock()
        sync = IDATSync(
            mock_repo, dm, orch, fs, "Grabaciones", closers=[closer_a, closer_b]
        )

        with pytest.raises(RuntimeError, match="closer_a failed"):
            await sync.aclose()

        closer_a.assert_awaited_once_with()
        closer_b.assert_awaited_once_with()
        await sync.aclose()
        closer_a.assert_awaited_once_with()
        closer_b.assert_awaited_once_with()

        closer_c = AsyncMock()
        async with IDATSync(
            mock_repo, dm, orch, fs, "Grabaciones", closers=[closer_c]
        ) as ctx_sync:
            assert isinstance(ctx_sync, IDATSync)
        closer_c.assert_awaited_once_with()

    async def test_idat_sync_streaming_overlap_disambiguation_and_fifo(  # pylint: disable=too-many-locals,too-many-statements
        self,
    ):
        """Overlap discovery with downloads, disambiguate paths, and keep FIFO order."""
        long_name = f"{'L' * 96}.pdf"
        release_recordings = asyncio.Event()
        fast_downloaded_before_discovery_done = False

        class TrackingDownloader(Downloader[MoodleContent]):
            """Downloader tracking whether downloads finish before discovery ends."""

            content_type = MoodleContent

            def __init__(self) -> None:
                self.downloaded: list[Path] = []

            async def download(
                self,
                content: MoodleContent,
                on_progress: ports.ProgressCallback | None = None,
            ) -> None:
                del on_progress
                self.downloaded.append(content.path)
                release_recordings.set()

        class TrackingRecordingDownloader(Downloader[RecordingContent]):
            """Downloader tracking recording paths."""

            content_type = RecordingContent

            def __init__(self) -> None:
                self.downloaded: list[Path] = []

            async def download(
                self,
                content: RecordingContent,
                on_progress: ports.ProgressCallback | None = None,
            ) -> None:
                del on_progress
                self.downloaded.append(content.path)

        course = Course(
            id=1,
            name="Course",
            sections=[
                CourseSection(
                    id=10,
                    name="Sec",
                    modules=[
                        CourseModule(
                            id=1,
                            name="Mod",
                            contents=[
                                ModuleContent(
                                    "guia.pdf", "http://a/1", 10, filepath="/Parte 1/"
                                ),
                                ModuleContent(
                                    "guia.pdf", "http://a/2", 20, filepath="/parte 1/"
                                ),
                                ModuleContent(
                                    "guia.pdf", "http://a/3", 30, filepath="/Parte 1/"
                                ),
                            ],
                        ),
                        CourseModule(
                            id=3,
                            name="Mod",
                            contents=[
                                ModuleContent(long_name, "http://a/7", 70),
                                ModuleContent(long_name, "http://a/8", 80),
                            ],
                        ),
                        CourseModule(
                            id=4,
                            name="Mod",
                            contents=[
                                ModuleContent("1." + "x" * 98, "http://a/9", 85),
                                ModuleContent("1." + "x" * 98, "http://a/10", 86),
                            ],
                        ),
                    ],
                )
            ],
        )
        course_dir = Path("Course")

        async def _slow_recordings(_name: str) -> list[RecordingContent]:
            await release_recordings.wait()
            return [
                RecordingContent(Path("Semana 1/video.mp4"), 90, "http://v/1"),
                RecordingContent(Path("Semana 1/video.mp4"), 95, "http://v/2"),
            ]

        mock_course_gw = MagicMock(spec=CourseGateway)
        mock_recording_gw = MagicMock(spec=RecordingGateway)
        mock_recording_gw.fetch_recordings = AsyncMock(side_effect=_slow_recordings)
        mock_strategy = MagicMock(spec=UrlContentStrategy)
        mock_strategy.can_handle.return_value = True
        mock_strategy.list_url_content = AsyncMock(
            side_effect=lambda url, filename, ts: [
                MoodleContent(Path(filename), ts, url)
            ]
        )
        repo = CourseRepository(mock_course_gw, mock_recording_gw, [mock_strategy])
        mock_fs = MagicMock(spec=FileSystemPort)
        mock_fs.scan_mtimes.return_value = {}
        moodle_dl = TrackingDownloader()
        rec_dl = TrackingRecordingDownloader()
        sync = IDATSync(
            repo,
            DownloadManager([moodle_dl, rec_dl], mock_fs),
            TaskOrchestrator([[moodle_dl], [rec_dl]]),
            mock_fs,
            "Grabaciones",
        )

        events: list[SyncEvent] = []
        async for ev in sync.sync_course(course, course_dir):
            events.append(ev)
            if isinstance(ev, TaskDownloadCompleted) and not any(
                isinstance(e, CourseDiscoveryCompleted) for e in events
            ):
                fast_downloaded_before_discovery_done = True

        assert fast_downloaded_before_discovery_done
        discovered_paths = [
            e.path for e in events if isinstance(e, CourseFileDiscovered)
        ]
        assert len(discovered_paths) == 9
        assert len({p.as_posix().casefold() for p in discovered_paths}) == 9
        for p in discovered_paths:
            assert len(p.name) <= 100
            assert events.index(
                CourseFileDiscovered(course, p, ContentSyncState.NEW)
            ) < events.index(TaskDownloadStarted(p))
        assert Path("Course/Sec/Mod-[1]/Parte 1/guia.pdf") in discovered_paths
        assert Path("Course/Sec/Mod-[1]/parte 1/guia (2).pdf") in discovered_paths
        assert Path("Course/Sec/Mod-[1]/Parte 1/guia (3).pdf") in discovered_paths
        assert Path(f"Course/Sec/Mod-[3]/{'L' * 92} (2).pdf") in discovered_paths
        assert Path(f"Course/Sec/Mod-[4]/1.{'x' * 94} (2)") in discovered_paths
        assert Path("Course/Grabaciones/Semana 1/video (2).mp4") in discovered_paths
        assert CourseDiscoveryCompleted(course, 0, 9) in events
        assert events[-1] == CourseSyncCompleted(course, None)

    async def test_idat_sync_throttles_progress_and_blocks_late_callbacks(self):  # pylint: disable=too-many-locals,too-many-statements
        """Throttle TaskDownloadProgress per task and ignore callbacks after finish."""
        mod = CourseModule(
            id=10,
            name="M1",
            contents=[
                ModuleContent("outdated.pdf", "https://a/1", 200),
                ModuleContent("new.pdf", "https://a/2", 200),
            ],
        )
        course = Course(
            id=7,
            name="Progress",
            sections=[CourseSection(id=1, name="S1", modules=[mod])],
        )
        captured_cbs: list[ports.ProgressCallback] = []
        clock = [10.00]

        class FakeProgressDownloader(Downloader[MoodleContent]):
            """Emit rapid ticks and save callback for post-completion invocation."""

            content_type = MoodleContent

            async def download(
                self,
                content: MoodleContent,
                on_progress: ports.ProgressCallback | None = None,
            ) -> None:
                assert on_progress is not None
                captured_cbs.append(on_progress)
                if content.path.name == "outdated.pdf":
                    for ts, done in (
                        (10.00, 100),
                        (10.05, 200),
                        (10.10, 300),
                        (10.16, 400),
                    ):
                        clock[0] = ts
                        on_progress(done, 1000)
                else:
                    clock[0] = 20.00
                    on_progress(50, None)
                    raise RemoteServiceError("boom")

        mock_strategy = MagicMock(spec=UrlContentStrategy)
        mock_strategy.can_handle.return_value = True
        mock_strategy.list_url_content = AsyncMock(
            side_effect=lambda url, fn, ts: [MoodleContent(Path(fn), ts, url)]
        )
        mock_course_gw = MagicMock(spec=CourseGateway)
        mock_rec_gw = MagicMock(spec=RecordingGateway)
        mock_rec_gw.fetch_recordings = AsyncMock(return_value=[])
        repo = CourseRepository(mock_course_gw, mock_rec_gw, [mock_strategy])

        outdated_path = Path("Course/S1/M1-[10]/outdated.pdf")
        new_path = Path("Course/S1/M1-[10]/new.pdf")
        mock_fs = MagicMock(spec=FileSystemPort)
        mock_fs.scan_mtimes.return_value = {outdated_path: 100}

        dl = FakeProgressDownloader()
        sync = IDATSync(
            repo,
            DownloadManager([dl], file_system=mock_fs),
            TaskOrchestrator([[dl]]),
            mock_fs,
            recordings_folder_name="Grabaciones",
            progress_interval=0.15,
        )

        with patch(
            "idat_sync.application.sync_service.time.monotonic",
            side_effect=lambda: clock[0],
        ):
            events = [ev async for ev in sync.sync_course(course, Path("Course"))]

        # Fire stale callbacks after sync finished
        clock[0] = 99.0
        for cb in captured_cbs:
            cb(999, 1000)

        assert (
            CourseFileDiscovered(course, outdated_path, ContentSyncState.OUTDATED)
            in events
        )
        assert CourseFileDiscovered(course, new_path, ContentSyncState.NEW) in events

        outdated_progress = [
            e
            for e in events
            if isinstance(e, TaskDownloadProgress) and e.path == outdated_path
        ]
        assert outdated_progress == [
            TaskDownloadProgress(outdated_path, 100, 1000),
            TaskDownloadProgress(outdated_path, 400, 1000),
        ]
        assert (
            events.index(TaskDownloadStarted(outdated_path))
            < events.index(outdated_progress[0])
            < events.index(outdated_progress[1])
            < events.index(TaskDownloadCompleted(outdated_path))
        )
        assert TaskDownloadProgress(new_path, 50, None) in events
        assert all(
            not (isinstance(e, TaskDownloadProgress) and e.downloaded == 999)
            for e in events
        )

        # Also verify stale callbacks after cancellation emit nothing
        cancel_started = asyncio.Event()
        cancel_cbs: list[ports.ProgressCallback] = []
        cancel_mod = CourseModule(
            id=11,
            name="M1",
            contents=[ModuleContent("slow.pdf", "https://a/3", 100)],
        )
        c_cancel = Course(
            id=8,
            name="CancelProgress",
            sections=[CourseSection(id=1, name="S1", modules=[cancel_mod])],
        )

        class HangingProgressDownloader(Downloader[MoodleContent]):
            """Hold download open until cancelled while saving callback."""

            content_type = MoodleContent

            async def download(
                self,
                content: MoodleContent,
                on_progress: ports.ProgressCallback | None = None,
            ) -> None:
                del content
                assert on_progress is not None
                cancel_cbs.append(on_progress)
                cancel_started.set()
                await asyncio.Future()

        hang_dl = HangingProgressDownloader()
        cancel_sync = IDATSync(
            repo,
            DownloadManager([hang_dl], file_system=mock_fs),
            TaskOrchestrator([[hang_dl]]),
            mock_fs,
            recordings_folder_name="Grabaciones",
            progress_interval=0.0,
        )

        async def consume_cancel() -> None:
            async for ev in cancel_sync.sync_course(c_cancel, Path("CancelCourse")):
                assert not isinstance(ev, TaskDownloadProgress)

        cancel_task = asyncio.create_task(consume_cancel())
        await asyncio.wait_for(cancel_started.wait(), timeout=2.0)
        cancel_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancel_task
        assert len(cancel_cbs) == 1
        cancel_cbs[0](123, 1000)

    async def test_windows_long_paths_work_across_filesystem_and_downloaders(self):  # pylint: disable=too-many-locals,too-many-statements
        """Support >260 char Windows paths across filesystem and all downloaders."""
        tmpdir = tempfile.mkdtemp()
        try:
            tmp_path = Path(tmpdir)
            deep_dir = tmp_path / ("a" * 90) / ("b" * 90) / ("c" * 90)
            deep_string = deep_dir / "notes.txt"
            deep_moodle = deep_dir / "slides.pdf"
            deep_sp = deep_dir / "handout.docx"
            deep_rec = deep_dir / "lecture.mp4"
            abs_deep_string = await asyncio.to_thread(os.path.abspath, deep_string)
            assert len(abs_deep_string) > 260

            fs = LocalFileSystemAdapter()
            fs.ensure_dir(deep_dir)

            await StringDownloader().download(
                StringContent(deep_string, 0, ["hello long path"])
            )

            mock_moodle = MagicMock()

            async def write_moodle(_url: str, dest, on_progress=None) -> None:
                del on_progress
                dest.write(b"moodle-bytes")

            mock_moodle.download_file = AsyncMock(side_effect=write_moodle)
            await MoodleDownloader(mock_moodle).download(
                MoodleContent(deep_moodle, 10, "https://example.edu/slides.pdf")
            )

            mock_sp = MagicMock()

            async def write_sp(_file_obj, dest, on_progress=None) -> None:
                del on_progress
                dest.write(b"sp-bytes")

            mock_sp.download_file = AsyncMock(side_effect=write_sp)
            await SharepointDownloader(mock_sp).download(
                SharepointContent(deep_sp, 20, MagicMock())
            )

            mtimes = fs.scan_mtimes(tmp_path)
            assert deep_string in mtimes
            assert deep_moodle in mtimes
            assert deep_sp in mtimes
            for key in mtimes:
                assert not str(key).startswith("\\\\?\\")
            assert fs.read_bytes(deep_string) == b"hello long path"

            rec_dl = RecordingDownloader(
                [], "idat628-my.sharepoint.com", AsyncRateLimiter(0.0)
            )
            with patch(
                "idat_sync.infrastructure.downloaders.yt_dlp.YoutubeDL"
            ) as mock_ydl:
                await rec_dl.download(
                    RecordingContent(
                        deep_rec,
                        30,
                        "https://idat628-my.sharepoint.com/:v:/r/video.mp4",
                    )
                )
            prefix = "\\\\?\\" if platform.system() == "Windows" else ""
            abs_deep_rec = await asyncio.to_thread(os.path.abspath, deep_rec)
            assert mock_ydl.call_args.args[0]["outtmpl"] == f"{prefix}{abs_deep_rec}"
        finally:
            shutil.rmtree(windows_long_path(tmpdir), ignore_errors=True)

    async def test_recording_downloader_aborts_progress_hook_on_task_cancellation(self):
        """Signal yt-dlp DownloadCancelled in progress hook after task cancel."""
        started = threading.Event()
        release = threading.Event()
        captured_hooks: list = []

        def fake_download(_urls):
            started.set()
            release.wait(timeout=2.0)

        with tempfile.TemporaryDirectory() as tmpdir:
            rec = RecordingContent(
                Path(tmpdir) / "Semana 1" / "video.mp4",
                1700000000,
                "https://idat628-my.sharepoint.com/:v:/r/video.mp4",
            )
            dl = RecordingDownloader(
                [], "idat628-my.sharepoint.com", AsyncRateLimiter(0.0)
            )

            def fake_ydl_factory(opts):
                captured_hooks.extend(opts.get("progress_hooks", []))
                ctx = MagicMock()
                ctx.__enter__.return_value.download.side_effect = fake_download
                return ctx

            progress_ticks: list[tuple[int, int | None]] = []

            with patch(
                "idat_sync.infrastructure.downloaders.yt_dlp.YoutubeDL",
                side_effect=fake_ydl_factory,
            ):
                task = asyncio.create_task(
                    dl.download(
                        rec,
                        on_progress=lambda d, t: progress_ticks.append((d, t)),
                    )
                )
                await asyncio.to_thread(started.wait, 2.0)

                assert len(captured_hooks) == 1
                captured_hooks[0]({"status": "downloading", "downloaded_bytes": 128})
                await asyncio.sleep(0)
                assert progress_ticks == [(128, None)]

                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

                with pytest.raises(DownloadCancelled):
                    captured_hooks[0](
                        {"status": "downloading", "downloaded_bytes": 256}
                    )
                await asyncio.sleep(0)
                assert progress_ticks == [(128, None)]
                release.set()

    def test_safe_path_segment_and_suffixed_segment_ignore_fake_long_extensions(self):
        """Treat >15 char or whitespace suffixes as part of stem, not extensions."""
        title = "Publishing, M. (2007. " + "x" * 100
        sanitized = safe_path_segment(title, max_len=100)
        assert len(sanitized) == 100
        assert sanitized.startswith("Publishing, M. (2007. ")

        spaced = "Report. v1 final " + "x" * 100
        assert len(safe_path_segment(spaced, max_len=100)) == 100
        assert safe_path_segment(spaced, max_len=100).startswith("Report. v1 final ")

        suffixed = safe_suffixed_segment("1", " (2)." + "x" * 98, max_len=100)
        assert len(suffixed) <= 100
        assert " (2)" in suffixed

    async def test_idat_sync_sync_courses_refresh_clears_sections(self):
        """Clear pre-populated Course.sections only when refresh=True."""
        course_cached = Course(
            id=1, name="Cached", sections=[CourseSection(id=10, name="Old")]
        )
        course_refreshed = Course(
            id=2, name="Refreshed", sections=[CourseSection(id=20, name="Old")]
        )
        mock_course_gw = MagicMock(spec=CourseGateway)
        mock_course_gw.populate_course_sections = AsyncMock(side_effect=lambda c: c)
        mock_rec_gw = MagicMock(spec=RecordingGateway)
        mock_rec_gw.fetch_recordings = AsyncMock(return_value=[])
        fs = MagicMock(spec=FileSystemPort)
        fs.scan_mtimes.return_value = {}
        sync = IDATSync(
            CourseRepository(mock_course_gw, mock_rec_gw, []),
            DownloadManager([], fs),
            TaskOrchestrator([]),
            fs,
            "Grabaciones",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            _ = [e async for e in sync.sync_courses(tmpdir, [course_cached])]
            mock_course_gw.populate_course_sections.assert_not_awaited()
            assert len(course_cached.sections) == 1

            _ = [
                e
                async for e in sync.sync_courses(
                    tmpdir, [course_refreshed], refresh=True
                )
            ]
            mock_course_gw.populate_course_sections.assert_awaited_once_with(
                course_refreshed
            )
            assert not course_refreshed.sections


class TestPathDisambiguator(unittest.TestCase):
    """Tests for streaming case-insensitive path disambiguation."""

    def test_collisions_truncation_and_no_content_import(self):
        """Disambiguate case-insensitive, long, and early-dot leaf collisions."""
        disambiguator = PathDisambiguator()
        assert disambiguator.disambiguate(
            Path("Course/Sec/Mod-[1]/Parte 1/guia.pdf")
        ) == Path("Course/Sec/Mod-[1]/Parte 1/guia.pdf")
        assert disambiguator.disambiguate(
            Path("Course/Sec/Mod-[1]/parte 1/guia.pdf")
        ) == Path("Course/Sec/Mod-[1]/parte 1/guia (2).pdf")
        assert disambiguator.disambiguate(
            Path("Course/Sec/Mod-[1]/Parte 1/guia.pdf")
        ) == Path("Course/Sec/Mod-[1]/Parte 1/guia (3).pdf")

        long_name = f"{'L' * 96}.pdf"
        assert disambiguator.disambiguate(Path(f"Course/Sec/{long_name}")) == Path(
            f"Course/Sec/{long_name}"
        )
        second_long = disambiguator.disambiguate(Path(f"Course/Sec/{long_name}"))
        assert second_long == Path(f"Course/Sec/{'L' * 92} (2).pdf")
        assert len(second_long.name) <= 100

        early_dot = Path("Course/Sec") / ("1." + "x" * 98)
        assert disambiguator.disambiguate(early_dot) == early_dot
        second_early_dot = disambiguator.disambiguate(early_dot)
        assert second_early_dot == Path("Course/Sec") / ("1." + "x" * 94 + " (2)")
        assert len(second_early_dot.name) <= 100

        path_utils_file = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "idat_sync"
            / "domain"
            / "path_utils.py"
        )
        imported = [name for _, name in iter_module_imports(path_utils_file)]
        assert not any(
            matches_prefix(name, ("idat_sync.domain.content",)) for name in imported
        )


class TestContentPendingClassification(unittest.TestCase):
    """Tests for domain and DownloadManager pending-item classification."""

    def test_classify_content_pure_in_memory(self):
        """Classify missing, outdated, and synced Content items purely in memory."""
        p_missing = Path("semana1/missing.pdf")
        p_outdated = Path("semana1/outdated.pdf")
        p_synced_equal = Path("semana1/synced_equal.pdf")
        p_synced_newer = Path("semana1/synced_newer.pdf")
        p_zero_mtime = Path("semana1/zero.html")

        local_mtimes = {
            p_outdated: 100,
            p_synced_equal: 100,
            p_synced_newer: 200,
            p_zero_mtime: 50,
        }

        assert (
            classify_content(MoodleContent(p_missing, 100, "http://a/1"), local_mtimes)
            is ContentSyncState.NEW
        )
        assert (
            classify_content(MoodleContent(p_outdated, 150, "http://a/2"), local_mtimes)
            is ContentSyncState.OUTDATED
        )
        assert (
            classify_content(
                MoodleContent(p_synced_equal, 100, "http://a/3"), local_mtimes
            )
            is ContentSyncState.SYNCED
        )
        assert (
            classify_content(
                MoodleContent(p_synced_newer, 100, "http://a/4"), local_mtimes
            )
            is ContentSyncState.SYNCED
        )
        assert (
            classify_content(
                StringContent(p_zero_mtime, 0, ["<p>Hi</p>"]), local_mtimes
            )
            is ContentSyncState.SYNCED
        )
        assert ContentSyncState.SYNCED == "Synced"
        assert ContentSyncState.NEW == "New"
        assert ContentSyncState.OUTDATED == "Outdated"
        assert not hasattr(domain_sync, "is_content_pending")
        assert not hasattr(domain_mod, "is_content_pending")

    def test_download_manager_classify_content_delegates_to_filesystem_port(self):
        """Classify Content items including StringContent byte comparison."""
        mock_fs = MagicMock(spec=FileSystemPort)
        manager = DownloadManager([], file_system=mock_fs)
        assert not hasattr(DownloadManager, "is_pending")

        p_missing = Path("missing.pdf")
        p_outdated = Path("outdated.pdf")
        p_synced = Path("synced.pdf")
        p_same_html = Path("same.html")
        p_changed_html = Path("changed.html")
        p_unreadable_html = Path("unreadable.html")
        p_empty_html = Path("empty.html")

        local_mtimes = {
            p_outdated: 100,
            p_synced: 200,
            p_same_html: 50,
            p_changed_html: 50,
            p_unreadable_html: 50,
            p_empty_html: 50,
        }
        bytes_by_path: dict[Path, bytes | None] = {
            p_same_html: b"<p>Same</p>",
            p_changed_html: b"<p>Old</p>",
            p_unreadable_html: None,
        }
        mock_fs.read_bytes.side_effect = bytes_by_path.get

        assert (
            manager.classify_content(
                MoodleContent(p_missing, 100, "http://a/1"), local_mtimes
            )
            is ContentSyncState.NEW
        )
        assert (
            manager.classify_content(
                MoodleContent(p_outdated, 150, "http://a/2"), local_mtimes
            )
            is ContentSyncState.OUTDATED
        )
        assert (
            manager.classify_content(
                MoodleContent(p_synced, 150, "http://a/3"), local_mtimes
            )
            is ContentSyncState.SYNCED
        )
        assert (
            manager.classify_content(
                StringContent(p_same_html, 0, ["  <p>Same</p>  "]), local_mtimes
            )
            is ContentSyncState.SYNCED
        )
        assert (
            manager.classify_content(
                StringContent(p_changed_html, 0, ["<p>New</p>"]), local_mtimes
            )
            is ContentSyncState.OUTDATED
        )
        assert (
            manager.classify_content(
                StringContent(p_unreadable_html, 0, ["<p>New</p>"]), local_mtimes
            )
            is ContentSyncState.OUTDATED
        )
        assert (
            manager.classify_content(
                StringContent(p_empty_html, 0, ["   "]), local_mtimes
            )
            is ContentSyncState.SYNCED
        )
        mock_fs.scan_mtimes.assert_not_called()
        mock_fs.ensure_dir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
