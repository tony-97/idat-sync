import os
from pathlib import Path

from idat_sync.auth import Credentials
from idat_sync.download_manager import DownloadManager, SyncDiff
from idat_sync.models import Course
from idat_sync.services.course_repository import CourseRepository
from idat_sync.services.moodle_client import MoodleClient
from idat_sync.services.moodle_adapter import MoodleAdapter
from idat_sync.services.sharepoint_adapter import SharepointApiAdapter
from idat_sync.services.downloaders import (
    Content,
    Downloader,
    MoodleDownloader,
    RecordingDownloader,
    SharepointDownloader,
    StringDownloader,
)
from idat_sync.ui.task_orchestrator import TaskOrchestrator
from idat_sync.utils import safe_filename


class IDATSync:
    def __init__(self, credentials: Credentials) -> None:
        sharepoint_storage_state = credentials.get("sharepoint_storage_state")
        cookies = sharepoint_storage_state.get("cookies", [])
        self.moodle_client = MoodleClient(credentials.get("token"))
        self.moodle_adapter = MoodleAdapter(self.moodle_client)
        self.sharepoint_client = SharepointApiAdapter(cookies)
        self.course_repository = CourseRepository(
            self.moodle_adapter, self.sharepoint_client
        )
        self.download_tasks_group: list[list[Downloader]] = [
            [MoodleDownloader(self.moodle_adapter)],
            [StringDownloader()],
            [
                SharepointDownloader(self.sharepoint_client),
                RecordingDownloader(cookies),
            ],
        ]
        self.downloaders: list[Downloader] = [
            downloader
            for downloaders in self.download_tasks_group
            for downloader in downloaders
        ]
        self.task_orchestrator = TaskOrchestrator(self.download_tasks_group)
        self.download_manager = DownloadManager(self.downloaders)

    def get_course_remote_tree(
        self, course: Course, course_dir: str | Path
    ) -> list[Content]:
        course_path = Path(course_dir)
        recordings_folder = course_path / "Grabaciones"
        remote_tree = self.course_repository.get_course_content(
            course, course_path
        )
        recordings_content = self.course_repository.get_recordings(
            course.name, recordings_folder
        )
        remote_tree.extend(recordings_content)
        return remote_tree

    def check_course_sync(
        self, course: Course, course_dir: str | Path
    ) -> SyncDiff:
        remote_tree = self.get_course_remote_tree(course, course_dir)
        return self.download_manager.diff_trees(course_dir, remote_tree)

    def sync_course(self, course: Course, course_dir: str | Path) -> SyncDiff:
        course_path = Path(course_dir)
        recordings_folder = course_path / "Grabaciones"
        os.makedirs(course_path, exist_ok=True)
        os.makedirs(recordings_folder, exist_ok=True)

        diff = self.check_course_sync(course, course_path)
        tasks = self.download_manager.prepare_download_tasks(
            diff.pending_contents
        )
        self.task_orchestrator.run(tasks)
        return diff

    def sync_courses(self, root_folder: str | Path) -> None:
        root_path = Path(root_folder)
        courses = self.course_repository.get_courses()
        for course in courses:
            course_dir = root_path / safe_filename(course.name)
            self.sync_course(course, course_dir)
