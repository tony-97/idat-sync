from pathlib import Path
import tempfile
import os
import unittest
from unittest.mock import MagicMock

from idat_sync.application.download_manager import DownloadManager
from idat_sync.application.task_orchestrator import TaskOrchestrator
from idat_sync.domain.content import (
    MoodleContent,
    RecordingContent,
    SharepointContent,
    StringContent,
)
from idat_sync.domain.sync import SyncStatus
from idat_sync.infrastructure.downloaders import (
    MoodleDownloader,
    RecordingDownloader,
    SharepointDownloader,
    StringDownloader,
)


class TestSyncPipeline(unittest.TestCase):
    def setUp(self):
        self.moodle_downloader = MoodleDownloader(client=MagicMock())
        self.string_downloader = StringDownloader()
        self.sharepoint_downloader = SharepointDownloader(client=MagicMock())
        self.recording_downloader = RecordingDownloader(cookies=[])

        self.groups = [
            [self.moodle_downloader],
            [self.string_downloader],
            [self.sharepoint_downloader, self.recording_downloader],
        ]
        self.downloaders = [d for g in self.groups for d in g]
        self.manager = DownloadManager(self.downloaders)
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


if __name__ == "__main__":
    unittest.main()
