import asyncio
import os


from idat_sync.auth import Credentials
from idat_sync.download_manager import DownloadManager
from idat_sync.models import Course
from idat_sync.services.course_repository import CourseRepository
from idat_sync.services.moodle_client import MoodleClient
from idat_sync.services.moodle_adapter import MoodleAdapter
from idat_sync.services.sharepoint_adapter import SharepointApiAdapter
from idat_sync.services.downloaders import (
    DownloadTask,
    Downloader,
    MoodleDownloader,
    RecordingDownloader,
    SharepointDownloader,
    StringDownloader,
)
from idat_sync.ui.task_orchestrator import TaskOrchestrator


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
        self.download_tasks_group = [
            [
                [MoodleDownloader(self.moodle_adapter)],
                [StringDownloader()],
                [
                    SharepointDownloader(self.sharepoint_client),
                    RecordingDownloader(cookies),
                ],
            ],
        ]
        self.downloaders: list[Downloader] = [
            downloader
            for downloaders in self.download_tasks_group
            for downloader in downloaders
        ]
        self.task_orchestrator = TaskOrchestrator(self.download_tasks_group)
        self.download_manager = DownloadManager(self.downloaders)

    def sync_course(self, course: Course, course_dir: str):
        recordings_folder = os.path.join(course_dir, "Grabaciones")
        os.makedirs(course_dir, exist_ok=True)
        os.makedirs(recordings_folder, exist_ok=True)
        remote_tree = self.course_repository.get_course_content(
            course, course_dir
        )
        recordings_content = self.course_repository.get_recordings(
            course.name, recordings_folder
        )
        remote_tree.extend(recordings_content)
        tasks = list[DownloadTask]()
        for content in remote_tree:
            self.download_manager.create_download_task(content)
        # filter existing files on tasks
        download_tasks = {
            downloader: list[DownloadTask]() for downloader in self.downloaders
        }
        # generate download tasks for moodle sharepoint memory and recording then
        # run them in parallel
        for task in tasks:
            if d := download_tasks.get(task.downloader):
                d.append(task)
        asyncio.gather()

    def sync_courses(self, root_folder: str):
        courses = self.course_repository.get_courses()
        for course in courses:
            self.sync_course(course, "")
