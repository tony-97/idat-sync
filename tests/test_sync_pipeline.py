import ast
import asyncio
import inspect
from pathlib import Path
import tempfile
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from idat_sync.application.download_manager import DownloadManager
from idat_sync.application.task_orchestrator import TaskOrchestrator
from idat_sync.domain.content import (
    MoodleContent,
    RecordingContent,
    SharepointContent,
    StringContent,
)
from idat_sync.application.ports import Downloader
from idat_sync.domain.sync import SyncDiff, SyncStatus
from idat_sync.infrastructure.filesystem import LocalFileSystemAdapter
from idat_sync.infrastructure.downloaders import (
    MoodleDownloader,
    RecordingDownloader,
    SharepointDownloader,
    StringDownloader,
)


def _iter_module_imports(py_file: Path) -> list[tuple[int, str]]:
    """Return (lineno, module_name) pairs from AST Import and ImportFrom nodes."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))
    return imports


def _matches_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(f"{p}.") for p in prefixes)


class TestSyncPipeline(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
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
        from idat_sync.application.ports import FileSystemPort

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
            self.assertEqual(task_paths, [existing_file, missing_file, valid_summary])
            self.assertNotIn(empty_summary, task_paths)
            mock_fs.assert_not_called()
            self.assertFalse(hasattr(DownloadManager, "should_download"))
            self.assertFalse(hasattr(tasks[0], "last_modified"))
            with self.assertRaises(TypeError):
                manager.prepare_download_tasks(contents, root_dir=tmp_path)  # type: ignore[call-arg]

    async def test_task_orchestrator_runs_groups_and_writes_string_tasks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target_html = Path(tmpdir) / "Semana 01" / "resumen.html"
            content = StringContent(target_html, 0, ["<h1>Week 1</h1>"])

            tasks = self.manager.prepare_download_tasks([content])
            coros = self.orchestrator.create_coroutines(
                tasks, lambda task: task.download()
            )
            await asyncio.gather(*coros)

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

    async def test_recording_downloader_uses_exact_target_file_path_even_without_suffix(self):
        import platform

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
                with patch("idat_sync.infrastructure.downloaders.yt_dlp.YoutubeDL") as mock_ydl:
                    await self.recording_downloader.download(rec)

                self.assertTrue(target_path.parent.is_dir())
                self.assertFalse(target_path.is_dir())
                ydl_opts = mock_ydl.call_args.args[0]
                self.assertEqual(
                    ydl_opts["outtmpl"],
                    f"{prefix}{os.path.abspath(target_path)}",
                )
                mock_ydl.return_value.__enter__.return_value.download.assert_called_once_with(
                    [rec.url]
                )

    async def test_custom_downloader_extends_without_modifying_base_classes(self):
        downloaded: list[RecordingContent] = []

        class YoutubeRecordingDownloader(Downloader[RecordingContent]):
            content_type = RecordingContent

            def can_handle(self, content: RecordingContent) -> bool:
                return "youtube.com" in content.url

            async def download(self, content: RecordingContent) -> None:
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

        await asyncio.gather(
            *orchestrator.create_coroutines(tasks, lambda task: task.download())
        )
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

        # Reserved stem with extension and trailing dots exposed by truncation
        self.assertEqual(safe_filename("CON.txt"), "_CON.txt")
        self.assertEqual(safe_filename("a" * 98 + "...b", max_len=100), "a" * 98)
        self.assertEqual(safe_filename("." * 105, max_len=100), "untitled")

    def test_safe_path_segment_preserves_utf8_and_extensions(self):
        from idat_sync.domain.path_utils import safe_path_segment

        self.assertEqual(
            safe_path_segment("Lección 1: Diseño/Guía?.pdf"),
            "Lección 1 Diseño Guía.pdf",
        )
        self.assertEqual(safe_path_segment("CON.txt"), "_CON.txt")
        self.assertEqual(safe_path_segment(".."), "untitled")
        self.assertEqual(safe_path_segment("a\x00\x7fb"), "a b")
        self.assertEqual(
            safe_path_segment("a" * 110 + ".mp4", max_len=100),
            "a" * 96 + ".mp4",
        )

    def test_content_in_folder_rejects_traversal_and_anchored_paths(self):
        for unsafe_item in (Path("/etc/passwd"), Path("../secret.txt")):
            with self.assertRaises(ValueError):
                StringContent(unsafe_item, 0, ["x"]).in_folder("Course")
        with self.assertRaises(ValueError):
            StringContent(Path("../secret.txt"), 0, ["x"]).in_folder("")
        with self.assertRaises(ValueError):
            StringContent(Path("file.pdf"), 0, ["x"]).in_folder("../outside")

        wrapped = StringContent(Path("file.pdf"), 0, ["x"]).in_folder(
            Path("/tmp/Course")
        )
        self.assertEqual(wrapped.path, Path("/tmp/Course/file.pdf"))

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

        override_diff = SyncDiff.from_trees(
            remote_contents,
            local_mtimes,
            outdated_paths={p_synced, p_local_only},
        )
        self.assertEqual(override_diff.outdated_paths, {p_outdated, p_synced})
        self.assertEqual(override_diff.synced_paths, set())
        self.assertEqual(override_diff.local_only_paths, {p_local_only})

    def test_sync_diff_from_trees_rejects_duplicate_remote_paths(self):
        dup_path = Path("semana1/Carpeta-[1]/guia.pdf")
        with self.assertRaises(ValueError) as ctx:
            SyncDiff.from_trees(
                [
                    MoodleContent(dup_path, 100, "http://a/1"),
                    MoodleContent(dup_path, 200, "http://a/2"),
                ],
                {},
            )
        self.assertIn("guia.pdf", str(ctx.exception))

    def test_local_filesystem_adapter_and_download_manager_seam(self):
        from idat_sync.application.ports import FileSystemPort
        from idat_sync.infrastructure.filesystem import LocalFileSystemAdapter

        fs = LocalFileSystemAdapter()
        self.assertFalse(hasattr(FileSystemPort, "exists"))
        self.assertFalse(hasattr(FileSystemPort, "get_mtime"))
        self.assertFalse(hasattr(fs, "exists"))
        self.assertFalse(hasattr(fs, "get_mtime"))

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            test_sub = tmp_path / "nested" / "dir"
            fs.ensure_dir(test_sub)
            self.assertTrue(test_sub.is_dir())

            test_file = test_sub / "test.txt"
            test_file.write_text("content", encoding="utf-8")
            os.utime(test_file, (1234567890, 1234567890))
            part_file = test_sub / "test.txt.part"
            part_file.write_text("partial", encoding="utf-8")

            mtimes = fs.scan_mtimes(tmp_path)
            self.assertEqual(mtimes.get(test_file), 1234567890)
            self.assertNotIn(part_file, mtimes)
            self.assertEqual(fs.read_bytes(test_file), b"content")
            self.assertIsNone(fs.read_bytes(tmp_path / "missing.txt"))

            mgr = DownloadManager(self.downloaders, file_system=fs)
            content = MoodleContent(test_file, 1234567890, "http://a")
            diff = mgr.diff_trees(tmp_path, [content])
            self.assertEqual(diff.synced_paths, {test_file})

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
            self.assertEqual(html_diff.synced_paths, {unchanged_html, empty_html})
            self.assertEqual(html_diff.outdated_paths, {changed_html})

    async def test_idat_sync_orchestration_with_injected_collaborators(self):
        from idat_sync.application.events import (
            CourseFilesDiscovered,
            CourseSyncCompleted,
            CourseSyncStarted,
        )
        from idat_sync.application.sync_service import IDATSync
        from idat_sync.application.course_repository import CourseRepository
        from idat_sync.application.ports import FileSystemPort
        from idat_sync.domain.course import Course

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

        self.assertEqual(
            events,
            [
                CourseSyncStarted(course),
                CourseFilesDiscovered(course, Path("target_dir"), (), ()),
                CourseSyncCompleted(course, None),
            ],
        )
        mock_fs.ensure_dir.assert_any_call(Path("target_dir"))
        mock_fs.ensure_dir.assert_any_call(Path("target_dir") / "Grabaciones")
        mock_orch.create_coroutines.assert_called_once()
        self.assertEqual(mock_orch.create_coroutines.call_args.args[0], [])

        # sync_courses & list_courses
        mock_repo.list_courses.return_value = [course]
        self.assertEqual(await sync.list_courses(), [course])
        _ = [event async for event in sync.sync_courses(Path("root_folder"), [course])]
        mock_fs.ensure_dir.assert_any_call(Path("root_folder") / "Algorithms")

    def test_hexagonal_architecture_import_boundaries_ast(self):
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
            for lineno, name in _iter_module_imports(py_file)
            if _matches_prefix(name, disallowed_prefixes)
        ]

        self.assertEqual(
            violations,
            [],
            f"Found architectural layer violations in domain/application: {violations}",
        )

        self.assertFalse(
            (src_root / "utils.py").exists(),
            "src/idat_sync/utils.py should be deleted",
        )
        self.assertFalse(
            (src_root / "infrastructure" / "auth.py").exists(),
            "src/idat_sync/infrastructure/auth.py should be deleted",
        )

        legacy_imports: list[str] = []
        playwright_importers: set[str] = set()
        for py_file in src_root.rglob("*.py"):
            rel_path = py_file.relative_to(src_root).as_posix()
            for lineno, name in _iter_module_imports(py_file):
                if _matches_prefix(
                    name, ("idat_sync.utils", "idat_sync.infrastructure.auth")
                ):
                    legacy_imports.append(f"{rel_path}:{lineno} imports {name}")
                if _matches_prefix(name, ("playwright",)):
                    playwright_importers.add(rel_path)

        self.assertEqual(
            legacy_imports,
            [],
            f"Found forbidden legacy imports: {legacy_imports}",
        )
        self.assertEqual(
            playwright_importers,
            {"infrastructure/sharepoint_auth.py"},
            f"Playwright imports must be confined to infrastructure/sharepoint_auth.py, got: {sorted(playwright_importers)}",
        )

        downloaders_path = src_root / "infrastructure" / "downloaders.py"
        downloaders_tree = ast.parse(
            downloaders_path.read_text(encoding="utf-8"),
            filename=str(downloaders_path),
        )
        downloader_imports: set[str] = set()
        for node in ast.walk(downloaders_tree):
            if isinstance(node, ast.Import):
                downloader_imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    downloader_imports.add(node.module.split(".")[0])
                downloader_imports.update(alias.name for alias in node.names)
        self.assertNotIn("requests", downloader_imports)
        self.assertNotIn("time", downloader_imports)
        self.assertNotIn("RQ_HEADER", downloader_imports)
        self.assertNotIn("REQUEST_HEADER", downloader_imports)

        print_calls: list[str] = []
        for py_file in src_root.rglob("*.py"):
            tree = ast.parse(
                py_file.read_text(encoding="utf-8"), filename=str(py_file)
            )
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "print"
                ):
                    print_calls.append(
                        f"{py_file.relative_to(src_root).as_posix()}:{node.lineno}"
                    )
        self.assertEqual(
            print_calls,
            [],
            f"Found forbidden print() calls in src/idat_sync/: {print_calls}",
        )

    def test_download_manager_requires_filesystem_port(self):
        with self.assertRaises(TypeError):
            DownloadManager(self.downloaders)  # type: ignore[call-arg]

    async def test_downloaders_delegate_to_clients_and_use_injected_personal_host(self):
        from idat_sync.domain.content import RecordingContent, SharepointContent
        from idat_sync.infrastructure.downloaders import (
            MoodleDownloader,
            RecordingDownloader,
            SharepointDownloader,
        )
        from idat_sync.infrastructure.moodle_client import MoodleClient
        from idat_sync.infrastructure.sharepoint_client import SharepointClient

        mock_moodle = MagicMock(spec=MoodleClient)
        moodle_dl = MoodleDownloader(mock_moodle)
        with self.assertRaises(TypeError):
            MoodleDownloader(mock_moodle, request_delay=0.75)  # type: ignore[call-arg]

        mock_sp = MagicMock(spec=SharepointClient)
        sp_dl = SharepointDownloader(mock_sp)
        with self.assertRaises(TypeError):
            SharepointDownloader(mock_sp, request_delay=1.25)  # type: ignore[call-arg]

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
        self.assertTrue(
            rec_dl.can_handle(
                RecordingContent(
                    Path("v.mp4"),
                    0,
                    "https://CONTOSO-MY.SHAREPOINT.COM/:v:/r/personal/u/v.mp4",
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

            async def _write_moodle(_url, dest):
                dest.write(b"moodle-bytes")

            mock_moodle.download_file.side_effect = _write_moodle
            moodle_target = tmp / "nested" / "a.pdf"
            await moodle_dl.download(MoodleContent(moodle_target, 0, "https://moodle/a.pdf"))
            self.assertEqual(moodle_target.read_bytes(), b"moodle-bytes")
            mock_moodle.download_file.assert_called_once()
            self.assertEqual(mock_moodle.download_file.call_args.args[0], "https://moodle/a.pdf")
            self.assertTrue(mock_moodle.download_file.call_args.args[1].closed)

            mock_file = MagicMock()

            async def _write_sp(_file, dest):
                dest.write(b"sp-bytes")

            mock_sp.download_file.side_effect = _write_sp
            sp_target = tmp / "nested_sp" / "b.pdf"
            await sp_dl.download(SharepointContent(sp_target, 0, mock_file))
            self.assertEqual(sp_target.read_bytes(), b"sp-bytes")
            mock_sp.download_file.assert_called_once()
            self.assertIs(mock_sp.download_file.call_args.args[0], mock_file)
            self.assertTrue(mock_sp.download_file.call_args.args[1].closed)

    async def test_downloaders_use_atomic_part_files_and_clean_up_on_failure(self):
        from idat_sync.infrastructure.moodle_client import MoodleClient
        from idat_sync.infrastructure.sharepoint_client import SharepointClient

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            moodle_target = tmp / "moodle" / "a.pdf"
            sp_target = tmp / "sp" / "b.pdf"
            observed_paths: list[str] = []

            async def ok_moodle(_url, fh):
                observed_paths.append(fh.name)
                self.assertTrue(fh.name.endswith(".part"))
                self.assertFalse(moodle_target.exists())
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
            self.assertEqual(moodle_target.read_bytes(), b"ok")
            self.assertFalse(moodle_target.with_name("a.pdf.part").exists())

            with self.assertRaises(RuntimeError):
                await SharepointDownloader(mock_sp).download(
                    SharepointContent(sp_target, 0, MagicMock())
                )
            self.assertFalse(sp_target.exists())
            self.assertFalse(sp_target.with_name("b.pdf.part").exists())
            self.assertEqual(len(observed_paths), 2)

    async def test_idat_sync_uses_injected_recordings_folder_name(self):
        from idat_sync.application.course_repository import CourseRepository
        from idat_sync.application.ports import FileSystemPort
        from idat_sync.application.sync_service import IDATSync
        from idat_sync.domain.course import Course

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
        mock_repo.get_recordings.assert_called_once_with("Algorithms", Path("target_dir") / "Videos")

    async def test_create_sync_service_wires_sync_config(self):
        from datetime import date
        from idat_sync.bootstrap import create_sync_service
        from idat_sync.config import SyncConfig
        from idat_sync.infrastructure.downloaders import (
            MoodleDownloader,
            RecordingDownloader,
            SharepointDownloader,
        )
        from idat_sync.infrastructure.moodle_adapter import MoodleAdapter
        from idat_sync.infrastructure.sharepoint_adapter import SharepointApiAdapter

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
                "cookies": [{"name": "FedAuth", "value": "v", "domain": ".sharepoint.com"}]
            },
        }

        mock_ctx = MagicMock()
        mock_ctx.with_cookies.return_value = mock_ctx
        mock_ctx.with_auto_retry.return_value = mock_ctx
        mock_personal_site = MagicMock()
        mock_personal_site.url = "https://contoso-my.sharepoint.com/personal/u"
        mock_ctx.web.current_user.get_personal_site.return_value.execute_query_async = AsyncMock(
            return_value=mock_personal_site
        )

        with patch("idat_sync.infrastructure.sharepoint_client.ClientContext", return_value=mock_ctx) as mock_ctx_cls, patch(
            "idat_sync.infrastructure.sharepoint_client.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            service = await create_sync_service(credentials, config=custom_config)  # type: ignore[arg-type]

        called_urls = [call.args[0] for call in mock_ctx_cls.call_args_list]
        self.assertIn("https://contoso.sharepoint.com/sites/ACAD", called_urls)
        self.assertIn("https://contoso-my.sharepoint.com", called_urls)
        mock_sleep.assert_awaited_once_with(1.5)

        self.assertEqual(service.recordings_folder_name, "Videos")
        moodle_adapter = service.course_repository.course_gateway
        assert isinstance(moodle_adapter, MoodleAdapter)
        self.assertEqual(moodle_adapter.moodle_host, "lms.example.edu")
        self.assertEqual(moodle_adapter.client.base_url, "https://lms.example.edu")
        self.assertEqual(moodle_adapter.client.request_delay, 1.5)

        sp_adapter = service.course_repository.recording_gateway
        assert isinstance(sp_adapter, SharepointApiAdapter)
        self.assertEqual(sp_adapter.semester_start, date(2026, 3, 16))
        self.assertEqual(sp_adapter.sharepoint_host, "contoso.sharepoint.com")
        self.assertEqual(sp_adapter.sharepoint_personal_host, "contoso-my.sharepoint.com")
        self.assertEqual(sp_adapter.recordings_folder_name, "Videos")
        self.assertEqual(sp_adapter.week_folder_prefix, "Week")
        from zoneinfo import ZoneInfo

        self.assertEqual(sp_adapter.timezone, ZoneInfo("America/Bogota"))
        self.assertFalse(hasattr(sp_adapter, "request_delay"))
        self.assertEqual(sp_adapter.sp_client.request_delay, 1.5)

        downloaders = service.download_manager.strategies
        moodle_dl = next(d for d in downloaders if isinstance(d, MoodleDownloader))
        sp_dl = next(d for d in downloaders if isinstance(d, SharepointDownloader))
        rec_dl = next(d for d in downloaders if isinstance(d, RecordingDownloader))
        self.assertIs(moodle_dl.moodle_client, moodle_adapter.client)
        self.assertIs(sp_dl.sharepoint_client, sp_adapter.sp_client)
        self.assertFalse(hasattr(moodle_dl, "request_delay"))
        self.assertFalse(hasattr(sp_dl, "request_delay"))
        self.assertEqual(rec_dl.sharepoint_personal_host, "contoso-my.sharepoint.com")

        with patch.object(
            sp_adapter.sp_client._transport, "aclose", new_callable=AsyncMock
        ) as mock_sp_transport_aclose:
            await service.aclose()
        self.assertTrue(moodle_adapter.client._client.is_closed)
        mock_sp_transport_aclose.assert_awaited_once_with()

    async def test_create_sync_service_closes_moodle_client_when_sharepoint_create_fails(self):
        from idat_sync.application.ports import CredentialsExpiredError
        from idat_sync.bootstrap import create_sync_service
        from idat_sync.config import SyncConfig

        mock_moodle_client = MagicMock()
        mock_moodle_client.aclose = AsyncMock()
        with patch("idat_sync.bootstrap.MoodleClient", return_value=mock_moodle_client), patch(
            "idat_sync.bootstrap.SharepointClient.create",
            new_callable=AsyncMock,
            side_effect=CredentialsExpiredError("expired"),
        ):
            with self.assertRaises(CredentialsExpiredError):
                await create_sync_service(
                    {"token": "tok123", "sharepoint_storage_state": {"cookies": []}},  # type: ignore[arg-type]
                    config=SyncConfig(request_delay=0.0),
                )

        mock_moodle_client.aclose.assert_awaited_once_with()

    def test_create_auth_service_wires_sync_config_and_ui_avoids_infrastructure(self):
        from idat_sync.application.auth_service import AuthService
        from idat_sync.bootstrap import create_auth_service
        from idat_sync.config import SyncConfig
        from idat_sync.infrastructure.credential_store import JsonCredentialStore
        from idat_sync.infrastructure.sharepoint_auth import SharepointBrowserAuth
        from idat_sync.infrastructure.moodle_adapter import MoodleTokenAdapter
        from idat_sync.infrastructure.moodle_client import MoodleTokenClient
        from idat_sync.ui.app import MainApp
        from idat_sync.ui.login_frame import LoginFrame
        from idat_sync.ui.sync_frame import SyncFrame

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

        self.assertIsInstance(auth_service, AuthService)
        self.assertIsInstance(auth_service.moodle_auth, MoodleTokenAdapter)
        self.assertIsInstance(auth_service.moodle_auth.client, MoodleTokenClient)
        self.assertEqual(
            auth_service.moodle_auth.client.base_url, "https://lms.example.edu"
        )
        self.assertIsInstance(auth_service.sharepoint_auth, SharepointBrowserAuth)
        self.assertEqual(
            auth_service.sharepoint_auth.sharepoint_host, "contoso.sharepoint.com"
        )
        self.assertEqual(
            auth_service.sharepoint_auth.sharepoint_personal_host,
            "contoso-my.sharepoint.com",
        )
        self.assertEqual(auth_service.sharepoint_auth.email_domain, "example.edu")
        self.assertIsInstance(auth_service.credential_store, JsonCredentialStore)
        self.assertEqual(
            auth_service.credential_store.credentials_file, credentials_path
        )

        self.assertIn("config", inspect.signature(MainApp.__init__).parameters)
        self.assertIn("config", inspect.signature(SyncFrame.__init__).parameters)
        self.assertIn("auth", inspect.signature(LoginFrame.__init__).parameters)

        ui_root = Path(__file__).resolve().parent.parent / "src" / "idat_sync" / "ui"
        ui_violations = [
            f"{py_file.name}:{lineno} imports {name}"
            for py_file in ui_root.rglob("*.py")
            for lineno, name in _iter_module_imports(py_file)
            if _matches_prefix(name, ("idat_sync.infrastructure",))
        ]

        self.assertEqual(
            ui_violations,
            [],
            f"Found forbidden infrastructure imports in ui/: {ui_violations}",
        )

    async def test_recording_downloader_maps_http_401_403_download_error_to_credentials_expired(self):
        from yt_dlp.utils import DownloadError
        from idat_sync.application.ports import CredentialsExpiredError

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "Semana 1" / "video.mp4"
            content = RecordingContent(
                target, 1700000000, "https://idat628-my.sharepoint.com/:v:/r/video.mp4"
            )
            dl = RecordingDownloader([], "idat628-my.sharepoint.com")

            for msg in ("HTTP Error 401: Unauthorized", "HTTP Error 403: Forbidden"):
                with patch("idat_sync.infrastructure.downloaders.yt_dlp.YoutubeDL") as mock_ydl:
                    mock_ydl.return_value.__enter__.return_value.download.side_effect = (
                        DownloadError(msg)
                    )
                    with self.assertRaises(CredentialsExpiredError):
                        await dl.download(content)

            with patch("idat_sync.infrastructure.downloaders.yt_dlp.YoutubeDL") as mock_ydl:
                mock_ydl.return_value.__enter__.return_value.download.side_effect = (
                    DownloadError("HTTP Error 404: Not Found")
                )
                with self.assertRaises(DownloadError) as ctx:
                    await dl.download(content)
                self.assertNotIsInstance(ctx.exception, CredentialsExpiredError)

    async def test_task_orchestrator_create_coroutines_groups_and_skips_empty_groups(self):
        from idat_sync.application.ports import DownloadTask

        executed: list[str] = []

        class StubDownloader(Downloader[StringContent]):
            content_type = StringContent

            def __init__(self, label: str):
                self.label = label

            async def download(self, content: StringContent) -> None:
                executed.append(f"{self.label}:{content.path.name}")

        d1 = StubDownloader("g1a")
        d2 = StubDownloader("g1b")
        d_empty = StubDownloader("empty")
        d3 = StubDownloader("g2")
        orch = TaskOrchestrator([[d1, d2], [d_empty], [d3]])

        self.assertEqual(orch.create_coroutines([], lambda t: t.download()), [])

        tasks = [
            DownloadTask(StringContent(Path("a.txt"), 0, ["x"]), d1),
            DownloadTask(StringContent(Path("b.txt"), 0, ["x"]), d2),
            DownloadTask(StringContent(Path("c.txt"), 0, ["x"]), d3),
        ]

        async def _run_task(task: DownloadTask) -> None:
            await task.download()

        coros = orch.create_coroutines(tasks, _run_task)
        self.assertEqual(len(coros), 2)
        await asyncio.gather(*coros)
        self.assertEqual(executed, ["g1a:a.txt", "g1b:b.txt", "g2:c.txt"])

        executed.clear()
        default_coros = orch.create_coroutines(tasks)
        self.assertEqual(len(default_coros), 2)
        await asyncio.gather(*default_coros)
        self.assertEqual(executed, ["g1a:a.txt", "g1b:b.txt", "g2:c.txt"])

    async def test_idat_sync_courses_isolates_course_errors_and_aborts_on_credentials_expired(self):
        from idat_sync.application.course_repository import CourseRepository
        from idat_sync.application.ports import (
            CourseGateway,
            CredentialsExpiredError,
            RecordingGateway,
        )
        from idat_sync.application.sync_service import IDATSync
        from idat_sync.domain.course import Course, CourseSection

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
            def ensure_dir(self, path: Path) -> None:
                pass

            def scan_mtimes(self, root: Path) -> dict[Path, int]:
                return {}

        class StubStringDownloader(Downloader[StringContent]):
            content_type = StringContent

            def __init__(self) -> None:
                self.downloaded: list[Path] = []

            async def download(self, content: StringContent) -> None:
                self.downloaded.append(content.path)

        mock_course_gw = MagicMock(spec=CourseGateway)
        mock_course_gw.list_courses.return_value = [c1, c2]
        mock_rec_gw = MagicMock(spec=RecordingGateway)
        mock_rec_gw.fetch_recordings.side_effect = [
            RuntimeError("course 1 transient failure"),
            [],
        ]

        from idat_sync.application.events import CourseSyncCompleted, SyncEvent

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
            self.assertEqual(mock_rec_gw.fetch_recordings.call_count, 2)
            self.assertEqual(
                str_dl.downloaded,
                [Path(tmpdir) / "Course Two" / "S2" / "c2.html"],
            )
            completed_courses = [
                (e.course.id, e.error)
                for e in events
                if isinstance(e, CourseSyncCompleted)
            ]
            self.assertEqual(completed_courses[0][0], 1)
            self.assertIsInstance(completed_courses[0][1], RuntimeError)
            self.assertEqual(completed_courses[1], (2, None))

            events.clear()
            str_dl.downloaded.clear()
            mock_rec_gw.fetch_recordings.reset_mock()
            mock_rec_gw.fetch_recordings.side_effect = [
                CredentialsExpiredError("token expired"),
                [],
            ]
            with self.assertRaises(CredentialsExpiredError):
                async for event in sync.sync_courses(tmpdir, [c1, c2]):
                    events.append(event)
            self.assertEqual(mock_rec_gw.fetch_recordings.call_count, 1)
            self.assertEqual(str_dl.downloaded, [])
            completed_courses = [
                (e.course.id, e.error)
                for e in events
                if isinstance(e, CourseSyncCompleted)
            ]
            self.assertEqual(len(completed_courses), 1)
            self.assertEqual(completed_courses[0][0], 1)
            self.assertIsInstance(completed_courses[0][1], CredentialsExpiredError)

    async def test_idat_sync_cancels_sibling_group_tasks_on_credentials_expired(self):
        from idat_sync.application.course_repository import CourseRepository
        from idat_sync.application.events import (
            CourseSyncCompleted,
            SyncEvent,
            TaskDownloadFailed,
        )
        from idat_sync.application.ports import (
            CourseGateway,
            CredentialsExpiredError,
            RecordingGateway,
        )
        from idat_sync.application.sync_service import IDATSync
        from idat_sync.domain.course import Course, CourseSection

        class FailingDownloader(Downloader[StringContent]):
            content_type = StringContent

            def can_handle(self, content: StringContent) -> bool:
                return content.path.name == "auth.html"

            async def download(self, content: StringContent) -> None:
                await asyncio.sleep(0)
                raise CredentialsExpiredError("401 expired")

        class SiblingDownloader(Downloader[MoodleContent]):
            content_type = MoodleContent

            def __init__(self) -> None:
                self.cancelled = False
                self.finished = False

            async def download(self, content: MoodleContent) -> None:
                try:
                    await asyncio.sleep(5)
                    self.finished = True
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

        class StubFileSystem:
            def ensure_dir(self, path: Path) -> None:
                pass

            def scan_mtimes(self, root: Path) -> dict[Path, int]:
                return {}

        c1 = Course(
            id=1,
            name="C1",
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
                MoodleContent(Path(course_dir) / "sib.pdf", 1, "https://moodle/sib.pdf"),
            ]

        repo.get_course_content = _fake_course_content  # type: ignore[method-assign]
        mock_rec_gw.fetch_recordings.return_value = []

        dm = DownloadManager([failing_dl, sibling_dl], StubFileSystem())  # type: ignore[arg-type]
        orch = TaskOrchestrator([[failing_dl], [sibling_dl]])
        sync = IDATSync(repo, dm, orch, StubFileSystem(), "Grabaciones")  # type: ignore[arg-type]

        events: list[SyncEvent] = []
        with self.assertRaises(CredentialsExpiredError):
            async for event in sync.sync_course(c1, Path("out")):
                events.append(event)

        self.assertTrue(sibling_dl.cancelled)
        self.assertFalse(sibling_dl.finished)
        self.assertTrue(any(isinstance(e, TaskDownloadFailed) for e in events))
        self.assertIsInstance(events[-1], CourseSyncCompleted)
        assert isinstance(events[-1], CourseSyncCompleted)
        self.assertIsInstance(events[-1].error, CredentialsExpiredError)

    async def test_idat_sync_list_courses_lazy_populate_and_actionable_file_filtering(self):
        from idat_sync.application.course_repository import CourseRepository
        from idat_sync.application.ports import CourseGateway, RecordingGateway
        from idat_sync.application.sync_service import IDATSync
        from idat_sync.domain.course import Course, CourseSection
        from idat_sync.infrastructure.downloaders import StringDownloader
        from idat_sync.infrastructure.filesystem import LocalFileSystemAdapter

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

        from idat_sync.application.events import (
            CourseFilesDiscovered,
            CourseSyncCompleted,
            CourseSyncStarted,
            SyncEvent,
            TaskDownloadCompleted,
            TaskDownloadStarted,
        )

        fs = LocalFileSystemAdapter()
        str_dl = StringDownloader()
        repo = CourseRepository(mock_course_gw, mock_rec_gw, [])
        dm = DownloadManager([str_dl], fs)
        orch = TaskOrchestrator([[str_dl]])
        sync = IDATSync(repo, dm, orch, fs, "Grabaciones")

        self.assertEqual(await sync.list_courses(), [c_lazy])

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
            self.assertEqual(
                events,
                [
                    CourseSyncStarted(c_lazy),
                    CourseFilesDiscovered(
                        c_lazy, course_dir, (synced_file,), (missing_file,)
                    ),
                    TaskDownloadStarted(missing_file),
                    TaskDownloadCompleted(missing_file),
                    CourseSyncCompleted(c_lazy, None),
                ],
            )
            with self.assertRaises(TypeError):
                sync.sync_course(c_lazy, course_dir, progress_listener=None)  # type: ignore[call-arg]

    def test_sync_events_contract_log_sync_event_and_ports_cleanup(self):
        from dataclasses import is_dataclass
        import dataclasses
        from idat_sync.application import events as events_mod
        from idat_sync.application import ports
        from idat_sync.domain.course import Course

        self.assertFalse(hasattr(ports, "SyncProgressPort"))
        self.assertTrue(hasattr(events_mod, "SyncEvent"))
        self.assertFalse(hasattr(events_mod, "EventPublisher"))
        self.assertFalse(hasattr(events_mod, "noop_publisher"))

        course = Course(id=1, name="Algebra")
        started = events_mod.CourseSyncStarted(course=course)
        discovered = events_mod.CourseFilesDiscovered(
            course=course,
            course_dir=Path("/tmp/Algebra"),
            synced_paths=(Path("/tmp/Algebra/a.pdf"),),
            pending_paths=(Path("/tmp/Algebra/b.pdf"),),
        )
        t_start = events_mod.TaskDownloadStarted(path=Path("/tmp/Algebra/b.pdf"))
        t_done = events_mod.TaskDownloadCompleted(path=Path("/tmp/Algebra/b.pdf"))
        t_fail = events_mod.TaskDownloadFailed(
            path=Path("/tmp/Algebra/b.pdf"), error=OSError("disk full")
        )
        c_ok = events_mod.CourseSyncCompleted(course=course)
        c_err = events_mod.CourseSyncCompleted(
            course=course, error=RuntimeError("boom")
        )

        for ev in (started, discovered, t_start, t_done, t_fail, c_ok, c_err):
            self.assertTrue(is_dataclass(ev))
            with self.assertRaises(dataclasses.FrozenInstanceError):
                setattr(ev, "course" if hasattr(ev, "course") else "path", None)

        with self.assertLogs("idat_sync", level="INFO") as cm:
            events_mod.log_sync_event(started)
            events_mod.log_sync_event(discovered)
            events_mod.log_sync_event(t_start)
            events_mod.log_sync_event(t_done)
            events_mod.log_sync_event(c_ok)
            events_mod.log_sync_event(t_fail)
            events_mod.log_sync_event(c_err)

        self.assertEqual(
            [r.levelname for r in cm.records],
            ["INFO", "INFO", "INFO", "INFO", "INFO", "ERROR", "ERROR"],
        )

    async def test_bootstrap_configure_logging_and_event_logging(self):
        import logging
        from idat_sync.bootstrap import configure_logging, create_sync_service
        from idat_sync.config import SyncConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "app.log"
            logger1 = configure_logging(log_file)
            logger2 = configure_logging(log_file)
            try:
                self.assertIs(logger1, logger2)
                self.assertEqual(logger2.name, "idat_sync")
                self.assertEqual(logger2.level, logging.INFO)
                self.assertFalse(logger2.propagate)
                self.assertEqual(len(logger2.handlers), 2)
                self.assertTrue(
                    any(isinstance(h, logging.FileHandler) for h in logger2.handlers)
                )
                self.assertTrue(
                    any(
                        type(h) is logging.StreamHandler
                        for h in logger2.handlers
                    )
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
        mock_ctx.web.current_user.get_personal_site.return_value.execute_query_async = AsyncMock(
            return_value=mock_personal_site
        )

        credentials = {
            "token": "tok123",
            "sharepoint_storage_state": {
                "cookies": [{"name": "FedAuth", "value": "v", "domain": ".sharepoint.com"}]
            },
        }
        with patch("idat_sync.infrastructure.sharepoint_client.ClientContext", return_value=mock_ctx), patch(
            "idat_sync.infrastructure.sharepoint_client.asyncio.sleep", new_callable=AsyncMock
        ):
            service = await create_sync_service(
                credentials,  # type: ignore[arg-type]
                config=SyncConfig(request_delay=0.0),
            )

        self.assertFalse(hasattr(service, "publish"))
        self.assertFalse(hasattr(service.task_orchestrator, "publish"))

    async def test_string_downloader_writes_atomically_via_part_file_and_cleans_up_on_error(self):
        from idat_sync.infrastructure.downloaders import StringDownloader
        from idat_sync.infrastructure.filesystem import windows_long_path

        dl = StringDownloader()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "sub" / "note.html"
            part_path = target.with_name("note.html.part")

            with patch("idat_sync.infrastructure.downloaders.os.replace", wraps=os.replace) as mock_replace:
                await dl.download(StringContent(target, 0, ["  <p>Hola</p>  "]))

            mock_replace.assert_called_once_with(
                windows_long_path(part_path), windows_long_path(target)
            )
            self.assertEqual(target.read_text(encoding="utf-8"), "<p>Hola</p>")
            self.assertFalse(part_path.exists())

            with patch("idat_sync.infrastructure.downloaders.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    await dl.download(StringContent(target, 0, ["<p>Corrupted</p>"]))

            self.assertEqual(target.read_text(encoding="utf-8"), "<p>Hola</p>")
            self.assertFalse(part_path.exists())

    async def test_idat_sync_sync_courses_invalidates_repository_cache_once_before_iterating(self):
        from idat_sync.application.course_repository import CourseRepository
        from idat_sync.application.sync_service import IDATSync
        from idat_sync.domain.course import Course
        from idat_sync.infrastructure.filesystem import LocalFileSystemAdapter

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
        self.assertEqual(call_order, ["invalidate", "populate:1", "populate:2"])

    async def test_idat_sync_aclose_and_context_manager_run_all_closers_once(self):
        from idat_sync.application.course_repository import CourseRepository
        from idat_sync.application.sync_service import IDATSync
        from idat_sync.infrastructure.filesystem import LocalFileSystemAdapter

        mock_repo = MagicMock(spec=CourseRepository)
        fs = LocalFileSystemAdapter()
        dm = DownloadManager([], fs)
        orch = TaskOrchestrator([])

        closer_a = AsyncMock(side_effect=RuntimeError("closer_a failed"))
        closer_b = AsyncMock()
        sync = IDATSync(mock_repo, dm, orch, fs, "Grabaciones", closers=[closer_a, closer_b])

        with self.assertRaisesRegex(RuntimeError, "closer_a failed"):
            await sync.aclose()

        closer_a.assert_awaited_once_with()
        closer_b.assert_awaited_once_with()
        await sync.aclose()
        closer_a.assert_awaited_once_with()
        closer_b.assert_awaited_once_with()

        closer_c = AsyncMock()
        async with IDATSync(mock_repo, dm, orch, fs, "Grabaciones", closers=[closer_c]) as ctx_sync:
            self.assertIsInstance(ctx_sync, IDATSync)
        closer_c.assert_awaited_once_with()

    async def test_idat_sync_get_course_remote_tree_disambiguates_case_insensitive_collisions(self):
        from idat_sync.application.course_repository import CourseRepository
        from idat_sync.application.ports import (
            CourseGateway,
            RecordingGateway,
            UrlContentStrategy,
        )
        from idat_sync.application.sync_service import IDATSync
        from idat_sync.domain.content import RecordingContent
        from idat_sync.domain.course import (
            Course,
            CourseModule,
            CourseSection,
            ModuleContent,
        )
        from idat_sync.infrastructure.filesystem import LocalFileSystemAdapter

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
                                ModuleContent("guia.pdf", "http://a/1", 10, filepath="/Parte 1/"),
                                ModuleContent("guia.pdf", "http://a/2", 20, filepath="/parte 1/"),
                                ModuleContent("guia.pdf", "http://a/3", 30, filepath="/Parte 1/"),
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
            side_effect=lambda url, filename, ts: [MoodleContent(Path(filename), ts, url)]
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
        self.assertEqual([item.path for item in items], expected_paths)
        for item in items:
            self.assertLessEqual(len(item.path.name), 100)

        with tempfile.TemporaryDirectory() as tmpdir:
            diff = await sync.check_course_sync(course, Path(tmpdir))
        self.assertEqual(len(diff.missing_paths), 12)

    async def test_windows_long_paths_work_across_filesystem_and_downloaders(self):
        import platform
        import shutil
        from idat_sync.infrastructure.filesystem import windows_long_path

        tmpdir = tempfile.mkdtemp()
        try:
            tmp_path = Path(tmpdir)
            deep_dir = tmp_path / ("a" * 90) / ("b" * 90) / ("c" * 90)
            deep_string = deep_dir / "notes.txt"
            deep_moodle = deep_dir / "slides.pdf"
            deep_sp = deep_dir / "handout.docx"
            deep_rec = deep_dir / "lecture.mp4"
            self.assertGreater(len(os.path.abspath(deep_string)), 260)

            fs = LocalFileSystemAdapter()
            fs.ensure_dir(deep_dir)

            await StringDownloader().download(
                StringContent(deep_string, 0, ["hello long path"])
            )

            mock_moodle = MagicMock()

            async def write_moodle(url: str, dest) -> None:
                dest.write(b"moodle-bytes")

            mock_moodle.download_file = AsyncMock(side_effect=write_moodle)
            await MoodleDownloader(mock_moodle).download(
                MoodleContent(deep_moodle, 10, "https://example.edu/slides.pdf")
            )

            mock_sp = MagicMock()

            async def write_sp(file_obj, dest) -> None:
                dest.write(b"sp-bytes")

            mock_sp.download_file = AsyncMock(side_effect=write_sp)
            await SharepointDownloader(mock_sp).download(
                SharepointContent(deep_sp, 20, MagicMock())
            )

            mtimes = fs.scan_mtimes(tmp_path)
            self.assertIn(deep_string, mtimes)
            self.assertIn(deep_moodle, mtimes)
            self.assertIn(deep_sp, mtimes)
            for key in mtimes:
                self.assertFalse(str(key).startswith("\\\\?\\"))
            self.assertEqual(fs.read_bytes(deep_string), b"hello long path")

            rec_dl = RecordingDownloader([], "idat628-my.sharepoint.com")
            with patch("idat_sync.infrastructure.downloaders.yt_dlp.YoutubeDL") as mock_ydl:
                await rec_dl.download(
                    RecordingContent(
                        deep_rec,
                        30,
                        "https://idat628-my.sharepoint.com/:v:/r/video.mp4",
                    )
                )
            prefix = "\\\\?\\" if platform.system() == "Windows" else ""
            self.assertEqual(
                mock_ydl.call_args.args[0]["outtmpl"],
                f"{prefix}{os.path.abspath(deep_rec)}",
            )
        finally:
            shutil.rmtree(windows_long_path(tmpdir), ignore_errors=True)

    async def test_recording_downloader_aborts_progress_hook_on_task_cancellation(self):
        import threading
        from yt_dlp.utils import DownloadCancelled

        started = threading.Event()
        release = threading.Event()
        captured_hooks: list = []

        def fake_download(urls):
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
                while not started.is_set():
                    await asyncio.sleep(0.01)

                self.assertEqual(len(captured_hooks), 1)
                captured_hooks[0]({"status": "downloading"})  # Not cancelled yet

                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

                with self.assertRaises(DownloadCancelled):
                    captured_hooks[0]({"status": "downloading"})
                release.set()

    def test_safe_path_segment_and_suffixed_segment_ignore_fake_long_extensions(self):
        from idat_sync.domain.path_utils import safe_path_segment, safe_suffixed_segment

        title = "Publishing, M. (2007. " + "x" * 100
        sanitized = safe_path_segment(title, max_len=100)
        self.assertEqual(len(sanitized), 100)
        self.assertTrue(sanitized.startswith("Publishing, M. (2007. "))

        spaced = "Report. v1 final " + "x" * 100
        self.assertEqual(len(safe_path_segment(spaced, max_len=100)), 100)
        self.assertTrue(safe_path_segment(spaced, max_len=100).startswith("Report. v1 final "))

        suffixed = safe_suffixed_segment("1", " (2)." + "x" * 98, max_len=100)
        self.assertLessEqual(len(suffixed), 100)
        self.assertIn(" (2)", suffixed)


if __name__ == "__main__":
    unittest.main()
