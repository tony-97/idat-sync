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

from idat_sync.application import events as events_mod
from idat_sync.application import ports
from idat_sync.application.auth_service import AuthService
from idat_sync.application.course_repository import CourseRepository
from idat_sync.application.download_manager import DownloadManager
from idat_sync.application.events import (
    CourseFilesDiscovered,
    CourseSyncCompleted,
    CourseSyncStarted,
    SyncEvent,
    TaskDownloadCompleted,
    TaskDownloadFailed,
    TaskDownloadStarted,
)
from idat_sync.application.ports import (
    CourseGateway,
    CredentialsExpiredError,
    Downloader,
    DownloadTask,
    FileSystemPort,
    RecordingGateway,
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
    safe_filename,
    safe_path_segment,
    safe_suffixed_segment,
)
from idat_sync.domain.sync import SyncDiff, SyncStatus
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

    def test_prepare_download_tasks_maps_contents_without_filesystem_calls(self):
        """Map Contents to DownloadTasks without touching FileSystemPort."""
        mock_fs = MagicMock(spec=FileSystemPort)
        manager = DownloadManager(self.downloaders, file_system=mock_fs)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            existing_file = tmp_path / "already_synced.pdf"
            existing_file.write_bytes(b"data")
            os.utime(existing_file, (2000000000, 2000000000))

            missing_file = tmp_path / "new_file.pdf"
            empty_summary = tmp_path / "empty.html"
            valid_summary = tmp_path / "summary.html"

            contents = [
                MoodleContent(existing_file, 1900000000, "https://aulavirtual/1"),
                SharepointContent(missing_file, 1800000000, MagicMock()),
                StringContent(empty_summary, 0, ["   "]),
                StringContent(valid_summary, 0, ["<p>Hello</p>"]),
            ]

            tasks = manager.prepare_download_tasks(contents)

            task_paths = [Path(t.path) for t in tasks]
            assert task_paths == [existing_file, missing_file, valid_summary]
            assert empty_summary not in task_paths
            mock_fs.assert_not_called()
            assert not hasattr(DownloadManager, "should_download")
            assert not hasattr(tasks[0], "last_modified")
            with pytest.raises(TypeError, match="root_dir"):
                manager.prepare_download_tasks(contents, root_dir=tmp_path)  # type: ignore[call-arg]  # pylint: disable=unexpected-keyword-arg

    async def test_task_orchestrator_runs_groups_and_writes_string_tasks(self):
        """Run orchestrator coroutines and write StringContent to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target_html = Path(tmpdir) / "Semana 01" / "resumen.html"
            content = StringContent(target_html, 0, ["<h1>Week 1</h1>"])

            tasks = self.manager.prepare_download_tasks([content])
            coros = self.orchestrator.create_coroutines(
                tasks, lambda task: task.download()
            )
            await asyncio.gather(*coros)

            assert target_html.exists()
            assert target_html.read_text(encoding="utf-8") == "<h1>Week 1</h1>"

    def test_recording_content_creates_recording_task(self):
        """Route RecordingContent to RecordingDownloader."""
        rec = RecordingContent(
            Path("Grabaciones/Semana 1/video.mp4"),
            1700000000,
            "https://idat628-my.sharepoint.com/:v:/r/personal/video.mp4",
        )
        tasks = self.manager.prepare_download_tasks([rec])
        assert len(tasks) == 1
        assert tasks[0].downloader is self.recording_downloader
        assert tasks[0].content is rec

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

            async def download(self, content: RecordingContent) -> None:
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
        tasks = manager.prepare_download_tasks([yt_rec])
        assert len(tasks) == 1
        assert tasks[0].downloader is yt_downloader

        await asyncio.gather(
            *orchestrator.create_coroutines(tasks, lambda task: task.download())
        )
        assert downloaded == [yt_rec]

    def test_diff_trees_uses_python_sets_for_sync_status(self):
        """Classify remote vs. local paths into SyncStatus buckets."""
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

            assert diff.missing_paths == {missing_file}
            assert diff.outdated_paths == {outdated_file}
            assert diff.synced_paths == {synced_file}
            assert diff.local_only_paths == {local_only_file}
            assert diff.to_download_paths == {missing_file, outdated_file}
            assert len(diff.pending_contents) == 2

            status_by_path = {e.path: e.status for e in diff.entries}
            assert status_by_path[synced_file] == SyncStatus.SYNCED
            assert status_by_path[outdated_file] == SyncStatus.OUTDATED
            assert status_by_path[missing_file] == SyncStatus.MISSING
            assert status_by_path[local_only_file] == SyncStatus.LOCAL_ONLY

    def test_safe_filename_at_domain_path_utils_seam(self):
        """Sanitize accents, Windows reserved names, and length in safe_filename."""
        assert "CON" in WINDOWS_RESERVED
        assert "NUL" in WINDOWS_RESERVED

        # Accents normalized and forbidden chars replaced
        sanitized = safe_filename("Lección 1: Diseño & Programación?*")
        assert sanitized == "Leccion 1 Diseno & Programacion"

        # Windows reserved device names prefixed
        assert safe_filename("con") == "_con"
        assert safe_filename("NUL") == "_NUL"

        # Default fallback for empty/whitespace
        assert safe_filename("   ") == "untitled"

        # Max length enforcement
        assert safe_filename("very_long_file_name", max_len=9) == "very_long"

        # Reserved stem with extension and trailing dots exposed by truncation
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

    def test_sync_diff_from_trees_pure_in_memory(self):
        """Compute SyncDiff from in-memory remote contents and local mtimes."""
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

        assert diff.missing_paths == {p_missing}
        assert diff.outdated_paths == {p_outdated}
        assert diff.synced_paths == {p_synced}
        assert diff.local_only_paths == {p_local_only}
        assert diff.to_download_paths == {p_missing, p_outdated}
        assert diff.pending_contents == [c_missing, c_outdated]
        assert [e.path for e in diff.entries] == sorted(
            [p_missing, p_outdated, p_synced, p_local_only]
        )

        override_diff = SyncDiff.from_trees(
            remote_contents,
            local_mtimes,
            outdated_paths={p_synced, p_local_only},
        )
        assert override_diff.outdated_paths == {p_outdated, p_synced}
        assert override_diff.synced_paths == set()
        assert override_diff.local_only_paths == {p_local_only}

    def test_sync_diff_from_trees_rejects_duplicate_remote_paths(self):
        """Raise ValueError when remote_contents contains duplicate paths."""
        dup_path = Path("semana1/Carpeta-[1]/guia.pdf")
        with pytest.raises(ValueError, match=r"guia\.pdf"):
            SyncDiff.from_trees(
                [
                    MoodleContent(dup_path, 100, "http://a/1"),
                    MoodleContent(dup_path, 200, "http://a/2"),
                ],
                {},
            )

    def test_local_filesystem_adapter_and_download_manager_seam(self):  # pylint: disable=too-many-locals
        """Verify LocalFileSystemAdapter and DownloadManager diff_trees behavior."""
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
            content = MoodleContent(test_file, 1234567890, "http://a")
            diff = mgr.diff_trees(tmp_path, [content])
            assert diff.synced_paths == {test_file}

            unchanged_html = tmp_path / "info.html"
            unchanged_html.write_text("<p>Same</p>", encoding="utf-8")
            changed_html = tmp_path / "resumen.html"
            changed_html.write_text("<p>Old</p>", encoding="utf-8")
            empty_html = tmp_path / "empty.html"
            empty_html.write_text("<p>Kept</p>", encoding="utf-8")

            html_diff = mgr.diff_trees(
                tmp_path,
                [
                    StringContent(unchanged_html, 0, ["  <p>Same</p>  "]),
                    StringContent(changed_html, 0, ["<p>New</p>"]),
                    StringContent(empty_html, 0, ["   "]),
                ],
            )
            assert html_diff.synced_paths == {unchanged_html, empty_html}
            assert html_diff.outdated_paths == {changed_html}

    async def test_idat_sync_orchestration_with_injected_collaborators(self):
        """Verify IDATSync orchestration events and directory creation."""
        mock_repo = MagicMock(spec=CourseRepository)
        mock_mgr = MagicMock(spec=DownloadManager)
        mock_orch = MagicMock(spec=TaskOrchestrator)
        mock_orch.create_coroutines.return_value = []
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
        fake_diff.synced_paths = set()
        fake_diff.pending_contents = []
        mock_mgr.diff_trees.return_value = fake_diff
        mock_mgr.prepare_download_tasks.return_value = []

        events = [event async for event in sync.sync_course(course, Path("target_dir"))]

        assert events == [
            CourseSyncStarted(course),
            CourseFilesDiscovered(course, Path("target_dir"), (), ()),
            CourseSyncCompleted(course, None),
        ]
        mock_fs.ensure_dir.assert_any_call(Path("target_dir"))
        mock_fs.ensure_dir.assert_any_call(Path("target_dir") / "Grabaciones")
        mock_orch.create_coroutines.assert_called_once()
        assert mock_orch.create_coroutines.call_args.args[0] == []

        # sync_courses & list_courses
        mock_repo.list_courses.return_value = [course]
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
        assert playwright_importers == {"infrastructure/sharepoint_auth.py"}, (
            "Playwright imports must be confined to infrastructure/sharepoint_auth.py, "
            f"got: {sorted(playwright_importers)}"
        )

        downloader_imports = _collect_downloader_imports(
            src_root / "infrastructure" / "downloaders.py"
        )
        assert "requests" not in downloader_imports
        assert "time" not in downloader_imports
        assert "RQ_HEADER" not in downloader_imports
        assert "REQUEST_HEADER" not in downloader_imports

        print_calls = _collect_print_calls(src_root)
        assert not print_calls, (
            f"Found forbidden print() calls in src/idat_sync/: {print_calls}"
        )

    def test_download_manager_requires_filesystem_port(self):
        """Require file_system argument on DownloadManager."""
        with pytest.raises(TypeError, match="file_system"):
            DownloadManager(self.downloaders)  # type: ignore[call-arg]  # pylint: disable=no-value-for-parameter

    async def test_downloaders_delegate_to_clients_and_use_injected_personal_host(  # pylint: disable=too-many-locals
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

            async def _write_moodle(_url, dest):
                dest.write(b"moodle-bytes")

            mock_moodle.download_file.side_effect = _write_moodle
            moodle_target = tmp / "nested" / "a.pdf"
            await moodle_dl.download(
                MoodleContent(moodle_target, 0, "https://moodle/a.pdf")
            )
            assert moodle_target.read_bytes() == b"moodle-bytes"
            mock_moodle.download_file.assert_called_once()
            assert mock_moodle.download_file.call_args.args[0] == "https://moodle/a.pdf"
            assert mock_moodle.download_file.call_args.args[1].closed

            mock_file = MagicMock()

            async def _write_sp(_file, dest):
                dest.write(b"sp-bytes")

            mock_sp.download_file.side_effect = _write_sp
            sp_target = tmp / "nested_sp" / "b.pdf"
            await sp_dl.download(SharepointContent(sp_target, 0, mock_file))
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

            async def ok_moodle(_url, fh):
                observed_paths.append(fh.name)
                assert fh.name.endswith(".part")
                assert not moodle_target.exists()
                fh.write(b"ok")

            async def failing_sp(_file, fh):
                observed_paths.append(fh.name)
                fh.write(b"partial")
                raise RuntimeError("network drop")

            mock_moodle = MagicMock(spec=MoodleClient)
            mock_moodle.download_file = AsyncMock(side_effect=ok_moodle)
            mock_sp = MagicMock(spec=SharepointClient)
            mock_sp.download_file = AsyncMock(side_effect=failing_sp)

            await MoodleDownloader(mock_moodle).download(
                MoodleContent(moodle_target, 0, "https://example.com/a.pdf")
            )
            assert moodle_target.read_bytes() == b"ok"
            assert not moodle_target.with_name("a.pdf.part").exists()

            with pytest.raises(RuntimeError):
                await SharepointDownloader(mock_sp).download(
                    SharepointContent(sp_target, 0, MagicMock())
                )
            assert not sp_target.exists()
            assert not sp_target.with_name("b.pdf.part").exists()
            assert len(observed_paths) == 2

    async def test_idat_sync_uses_injected_recordings_folder_name(self):
        """Use configured recordings_folder_name when syncing a course."""
        mock_repo = MagicMock(spec=CourseRepository)
        mock_mgr = MagicMock(spec=DownloadManager)
        mock_orch = MagicMock(spec=TaskOrchestrator)
        mock_orch.create_coroutines.return_value = []
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
        fake_diff.synced_paths = set()
        fake_diff.pending_contents = []
        mock_mgr.diff_trees.return_value = fake_diff
        mock_mgr.prepare_download_tasks.return_value = []

        _ = [event async for event in sync.sync_course(course, Path("target_dir"))]

        mock_fs.ensure_dir.assert_any_call(Path("target_dir") / "Videos")
        mock_repo.get_recordings.assert_called_once_with(
            "Algorithms", Path("target_dir") / "Videos"
        )

    async def test_create_sync_service_wires_sync_config(self):  # pylint: disable=too-many-locals,too-many-statements
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
                "idat_sync.infrastructure.sharepoint_client.asyncio.sleep",
                new_callable=AsyncMock,
            ) as mock_sleep,
        ):
            service = await create_sync_service(credentials, config=custom_config)  # type: ignore[arg-type]

        called_urls = [call.args[0] for call in mock_ctx_cls.call_args_list]
        assert "https://contoso.sharepoint.com/sites/ACAD" in called_urls
        assert "https://contoso-my.sharepoint.com" in called_urls
        mock_sleep.assert_awaited_once_with(1.5)

        assert service.recordings_folder_name == "Videos"
        moodle_adapter = service.course_repository.course_gateway
        assert isinstance(moodle_adapter, MoodleAdapter)
        assert moodle_adapter.moodle_host == "lms.example.edu"
        assert moodle_adapter.client.base_url == "https://lms.example.edu"
        assert moodle_adapter.client.request_delay == 1.5

        sp_adapter = service.course_repository.recording_gateway
        assert isinstance(sp_adapter, SharepointApiAdapter)
        assert sp_adapter.semester_start == date(2026, 3, 16)
        assert sp_adapter.sharepoint_host == "contoso.sharepoint.com"
        assert sp_adapter.sharepoint_personal_host == "contoso-my.sharepoint.com"
        assert sp_adapter.recordings_folder_name == "Videos"
        assert sp_adapter.week_folder_prefix == "Week"
        assert sp_adapter.timezone == ZoneInfo("America/Bogota")
        assert not hasattr(sp_adapter, "request_delay")
        assert sp_adapter.sp_client.request_delay == 1.5

        downloaders = service.download_manager.strategies
        moodle_dl = next(d for d in downloaders if isinstance(d, MoodleDownloader))
        sp_dl = next(d for d in downloaders if isinstance(d, SharepointDownloader))
        rec_dl = next(d for d in downloaders if isinstance(d, RecordingDownloader))
        assert moodle_dl.moodle_client is moodle_adapter.client
        assert sp_dl.sharepoint_client is sp_adapter.sp_client
        assert not hasattr(moodle_dl, "request_delay")
        assert not hasattr(sp_dl, "request_delay")
        assert rec_dl.sharepoint_personal_host == "contoso-my.sharepoint.com"

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
            dl = RecordingDownloader([], "idat628-my.sharepoint.com")

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
                with pytest.raises(DownloadError) as ctx:
                    await dl.download(content)
                assert not isinstance(ctx.value, CredentialsExpiredError)

    async def test_task_orchestrator_create_coroutines_groups_and_skips_empty_groups(
        self,
    ):
        """Group DownloadTasks by downloader group and skip empty groups."""
        executed: list[str] = []

        class StubDownloader(Downloader[StringContent]):
            """Stub downloader appending downloaded filenames."""

            content_type = StringContent

            def __init__(self, label: str):
                """Initialize stub with a group label."""
                self.label = label

            async def download(self, content: StringContent) -> None:
                """Record download in ``executed``."""
                executed.append(f"{self.label}:{content.path.name}")

        d1 = StubDownloader("g1a")
        d2 = StubDownloader("g1b")
        d_empty = StubDownloader("empty")
        d3 = StubDownloader("g2")
        orch = TaskOrchestrator([[d1, d2], [d_empty], [d3]])

        assert not orch.create_coroutines([], lambda t: t.download())

        tasks = [
            DownloadTask(StringContent(Path("a.txt"), 0, ["x"]), d1),
            DownloadTask(StringContent(Path("b.txt"), 0, ["x"]), d2),
            DownloadTask(StringContent(Path("c.txt"), 0, ["x"]), d3),
        ]

        async def _run_task(task: DownloadTask) -> None:
            await task.download()

        coros = orch.create_coroutines(tasks, _run_task)
        assert len(coros) == 2
        await asyncio.gather(*coros)
        assert executed == ["g1a:a.txt", "g1b:b.txt", "g2:c.txt"]

        executed.clear()
        default_coros = orch.create_coroutines(tasks)
        assert len(default_coros) == 2
        await asyncio.gather(*default_coros)
        assert executed == ["g1a:a.txt", "g1b:b.txt", "g2:c.txt"]

    async def test_idat_sync_courses_isolates_errors_and_aborts_on_auth_expired(  # pylint: disable=too-many-locals
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

            async def download(self, content: StringContent) -> None:
                """Record downloaded path."""
                self.downloaded.append(content.path)

        mock_course_gw = MagicMock(spec=CourseGateway)
        mock_course_gw.list_courses.return_value = [c1, c2]
        mock_rec_gw = MagicMock(spec=RecordingGateway)
        mock_rec_gw.fetch_recordings.side_effect = [
            RuntimeError("course 1 transient failure"),
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
            events: list[SyncEvent] = [
                event async for event in sync.sync_courses(tmpdir, [c1, c2])
            ]
            assert mock_rec_gw.fetch_recordings.call_count == 2
            assert str_dl.downloaded == [Path(tmpdir) / "Course Two" / "S2" / "c2.html"]
            completed_courses = [
                (e.course.id, e.error)
                for e in events
                if isinstance(e, CourseSyncCompleted)
            ]
            assert completed_courses[0][0] == 1
            assert isinstance(completed_courses[0][1], RuntimeError)
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
            assert not str_dl.downloaded
            aborted_courses = [e for e in events if isinstance(e, CourseSyncCompleted)]
            assert len(aborted_courses) == 1
            assert aborted_courses[0].course.id == 1
            assert isinstance(aborted_courses[0].error, CredentialsExpiredError)

    async def test_idat_sync_cancels_sibling_group_tasks_on_credentials_expired(self):  # pylint: disable=too-many-locals
        """Cancel sibling groups when a task raises CredentialsExpiredError."""

        class FailingDownloader(Downloader[StringContent]):
            """Stub downloader raising CredentialsExpiredError."""

            content_type = StringContent

            def can_handle(self, content: StringContent) -> bool:
                """Handle auth.html only."""
                return content.path.name == "auth.html"

            async def download(self, _content: StringContent) -> None:
                """Raise CredentialsExpiredError."""
                await asyncio.sleep(0)
                raise CredentialsExpiredError("401 expired")

        class SiblingDownloader(Downloader[MoodleContent]):
            """Stub sibling downloader tracking cancellation."""

            content_type = MoodleContent

            def __init__(self) -> None:
                self.cancelled = False
                self.finished = False

            async def download(self, _content: MoodleContent) -> None:
                """Sleep until cancelled."""
                try:
                    await asyncio.sleep(5)
                    self.finished = True
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

        class StubFileSystem:
            """No-op FileSystemPort stub."""

            def ensure_dir(self, _path: Path) -> None:
                """No-op directory creation."""

            def scan_mtimes(self, _root: Path) -> dict[Path, int]:
                """Return empty mtime map."""
                return {}

        c1 = Course(
            id=1,
            name="Sibling Cancel Course",
            sections=[
                CourseSection(
                    id=1,
                    name="S1",
                    inline_contents=[StringContent(Path("auth.html"), 1, ["a"])],
                    modules=[],
                )
            ],
        )
        mock_course_gw = MagicMock(spec=CourseGateway)
        mock_rec_gw = MagicMock(spec=RecordingGateway)
        mock_rec_gw.fetch_recordings.return_value = [
            RecordingContent(Path("r.mp4"), 1, "https://example.com/r.mp4")
        ]

        # Use MoodleContent via a stubbed repo method for the sibling group
        failing_dl = FailingDownloader()
        sibling_dl = SiblingDownloader()
        repo = CourseRepository(mock_course_gw, mock_rec_gw, [])

        async def _fake_course_content(_course, course_dir):
            return [
                StringContent(Path(course_dir) / "auth.html", 1, ["a"]),
                MoodleContent(
                    Path(course_dir) / "sib.pdf", 1, "https://moodle/sib.pdf"
                ),
            ]

        repo.get_course_content = _fake_course_content  # type: ignore[method-assign]
        mock_rec_gw.fetch_recordings.return_value = []

        dm = DownloadManager([failing_dl, sibling_dl], StubFileSystem())  # type: ignore[arg-type]
        orch = TaskOrchestrator([[failing_dl], [sibling_dl]])
        sync = IDATSync(repo, dm, orch, StubFileSystem(), "Grabaciones")  # type: ignore[arg-type]

        events: list[SyncEvent] = []

        with pytest.raises(CredentialsExpiredError):
            await _consume_events(sync.sync_course(c1, Path("out")), events)

        assert sibling_dl.cancelled
        assert not sibling_dl.finished
        assert any(isinstance(e, TaskDownloadFailed) for e in events)
        assert isinstance(events[-1], CourseSyncCompleted)
        assert isinstance(events[-1], CourseSyncCompleted)
        assert isinstance(events[-1].error, CredentialsExpiredError)

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
            assert events == [
                CourseSyncStarted(c_lazy),
                CourseFilesDiscovered(
                    c_lazy, course_dir, (synced_file,), (missing_file,)
                ),
                TaskDownloadStarted(missing_file),
                TaskDownloadCompleted(missing_file),
                CourseSyncCompleted(c_lazy, None),
            ]
            with pytest.raises(TypeError, match="progress_listener"):
                sync.sync_course(c_lazy, course_dir, progress_listener=None)  # type: ignore[call-arg]  # pylint: disable=unexpected-keyword-arg

    def test_sync_events_contract_log_sync_event_and_ports_cleanup(self):
        """Verify SyncEvent immutability and log_sync_event log levels."""
        assert not hasattr(ports, "SyncProgressPort")
        assert hasattr(events_mod, "SyncEvent")
        assert not hasattr(events_mod, "EventPublisher")
        assert not hasattr(events_mod, "noop_publisher")

        course = Course(id=1, name="Algebra")
        started = events_mod.CourseSyncStarted(course=course)
        discovered = events_mod.CourseFilesDiscovered(
            course=course,
            course_dir=Path("/tmp/Algebra"),  # noqa: S108
            synced_paths=(Path("/tmp/Algebra/a.pdf"),),  # noqa: S108
            pending_paths=(Path("/tmp/Algebra/b.pdf"),),  # noqa: S108
        )
        t_start = events_mod.TaskDownloadStarted(path=Path("/tmp/Algebra/b.pdf"))  # noqa: S108
        t_done = events_mod.TaskDownloadCompleted(path=Path("/tmp/Algebra/b.pdf"))  # noqa: S108
        t_fail = events_mod.TaskDownloadFailed(
            path=Path("/tmp/Algebra/b.pdf"),  # noqa: S108
            error=OSError("disk full"),
        )
        c_ok = events_mod.CourseSyncCompleted(course=course)
        c_err = events_mod.CourseSyncCompleted(
            course=course, error=RuntimeError("boom")
        )

        for ev in (started, discovered, t_start, t_done, t_fail, c_ok, c_err):
            assert dataclasses.is_dataclass(ev)
            with pytest.raises(dataclasses.FrozenInstanceError):
                setattr(ev, "course" if hasattr(ev, "course") else "path", None)

        with self.assertLogs("idat_sync", level="INFO") as cm:
            events_mod.log_sync_event(started)
            events_mod.log_sync_event(discovered)
            events_mod.log_sync_event(t_start)
            events_mod.log_sync_event(t_done)
            events_mod.log_sync_event(c_ok)
            events_mod.log_sync_event(t_fail)
            events_mod.log_sync_event(c_err)

        assert [r.levelname for r in cm.records] == [
            "INFO",
            "INFO",
            "INFO",
            "INFO",
            "INFO",
            "ERROR",
            "ERROR",
        ]

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
                "idat_sync.infrastructure.sharepoint_client.asyncio.sleep",
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

            with patch.object(
                Path, "replace", autospec=True, wraps=Path.replace
            ) as mock_replace:
                await dl.download(StringContent(target, 0, ["  <p>Hola</p>  "]))

            mock_replace.assert_called_once_with(
                windows_long_path(part_path), windows_long_path(target)
            )
            assert target.read_text(encoding="utf-8") == "<p>Hola</p>"
            assert not part_path.exists()

            with (
                patch.object(Path, "replace", side_effect=OSError("disk error")),
                pytest.raises(OSError, match="disk error"),
            ):
                await dl.download(StringContent(target, 0, ["<p>Corrupted</p>"]))

            assert target.read_text(encoding="utf-8") == "<p>Hola</p>"
            assert not part_path.exists()

    async def test_idat_sync_sync_courses_invalidates_repository_cache_once(self):
        """Invalidate repository cache once before iterating courses."""
        call_order: list[str] = []
        mock_repo = MagicMock(spec=CourseRepository)
        mock_repo.invalidate_cache.side_effect = lambda: call_order.append("invalidate")

        async def _populate(course: Course) -> Course:
            call_order.append(f"populate:{course.id}")
            return course

        mock_repo.populate_course_sections = AsyncMock(side_effect=_populate)
        mock_repo.get_course_content = AsyncMock(return_value=[])
        mock_repo.get_recordings = AsyncMock(return_value=[])

        fs = LocalFileSystemAdapter()
        dm = DownloadManager([], fs)
        orch = TaskOrchestrator([])
        sync = IDATSync(mock_repo, dm, orch, fs, "Grabaciones")

        with tempfile.TemporaryDirectory() as tmpdir:
            courses = [Course(id=1, name="C1"), Course(id=2, name="C2")]
            _ = [event async for event in sync.sync_courses(tmpdir, courses)]

        mock_repo.invalidate_cache.assert_called_once_with()
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

    async def test_idat_sync_get_course_remote_tree_disambiguates_collisions(  # pylint: disable=too-many-locals
        self,
    ):
        """Disambiguate case-insensitive path collisions in remote course tree."""
        long_name = f"{'L' * 96}.pdf"
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
                            id=2,
                            name="Mod",
                            contents=[
                                ModuleContent("file.pdf", "http://a/4", 40),
                                ModuleContent("file (2).pdf", "http://a/5", 50),
                                ModuleContent("file.pdf", "http://a/6", 60),
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

        mock_course_gw = MagicMock(spec=CourseGateway)
        mock_recording_gw = MagicMock(spec=RecordingGateway)
        mock_recording_gw.fetch_recordings = AsyncMock(
            return_value=[
                RecordingContent(Path("Semana 1/video.mp4"), 90, "http://v/1"),
                RecordingContent(Path("Semana 1/video.mp4"), 95, "http://v/2"),
            ]
        )
        mock_strategy = MagicMock(spec=UrlContentStrategy)
        mock_strategy.can_handle.return_value = True
        mock_strategy.list_url_content = AsyncMock(
            side_effect=lambda url, filename, ts: [
                MoodleContent(Path(filename), ts, url)
            ]
        )
        repo = CourseRepository(mock_course_gw, mock_recording_gw, [mock_strategy])

        fs = LocalFileSystemAdapter()
        dm = DownloadManager([], fs)
        orch = TaskOrchestrator([])
        sync = IDATSync(repo, dm, orch, fs, "Grabaciones")

        items = await sync.get_course_remote_tree(course, course_dir)

        expected_paths = [
            Path("Course/Sec/Mod-[1]/Parte 1/guia.pdf"),
            Path("Course/Sec/Mod-[1]/parte 1/guia (2).pdf"),
            Path("Course/Sec/Mod-[1]/Parte 1/guia (3).pdf"),
            Path("Course/Sec/Mod-[2]/file.pdf"),
            Path("Course/Sec/Mod-[2]/file (2).pdf"),
            Path("Course/Sec/Mod-[2]/file (3).pdf"),
            Path(f"Course/Sec/Mod-[3]/{long_name}"),
            Path(f"Course/Sec/Mod-[3]/{'L' * 92} (2).pdf"),
            Path(f"Course/Sec/Mod-[4]/1.{'x' * 98}"),
            Path(f"Course/Sec/Mod-[4]/1.{'x' * 94} (2)"),
            Path("Course/Grabaciones/Semana 1/video.mp4"),
            Path("Course/Grabaciones/Semana 1/video (2).mp4"),
        ]
        assert [item.path for item in items] == expected_paths
        for item in items:
            assert len(item.path.name) <= 100

        with tempfile.TemporaryDirectory() as tmpdir:
            diff = await sync.check_course_sync(course, Path(tmpdir))
        assert len(diff.missing_paths) == 12

    async def test_windows_long_paths_work_across_filesystem_and_downloaders(self):  # pylint: disable=too-many-locals
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

            async def write_moodle(_url: str, dest) -> None:
                dest.write(b"moodle-bytes")

            mock_moodle.download_file = AsyncMock(side_effect=write_moodle)
            await MoodleDownloader(mock_moodle).download(
                MoodleContent(deep_moodle, 10, "https://example.edu/slides.pdf")
            )

            mock_sp = MagicMock()

            async def write_sp(_file_obj, dest) -> None:
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

            rec_dl = RecordingDownloader([], "idat628-my.sharepoint.com")
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
            dl = RecordingDownloader([], "idat628-my.sharepoint.com")

            def fake_ydl_factory(opts):
                captured_hooks.extend(opts.get("progress_hooks", []))
                ctx = MagicMock()
                ctx.__enter__.return_value.download.side_effect = fake_download
                return ctx

            with patch(
                "idat_sync.infrastructure.downloaders.yt_dlp.YoutubeDL",
                side_effect=fake_ydl_factory,
            ):
                task = asyncio.create_task(dl.download(rec))
                await asyncio.to_thread(started.wait, 2.0)

                assert len(captured_hooks) == 1
                captured_hooks[0]({"status": "downloading"})

                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

                with pytest.raises(DownloadCancelled):
                    captured_hooks[0]({"status": "downloading"})
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


if __name__ == "__main__":
    unittest.main()
