from idat_sync.application.course_repository import CourseRepository
from idat_sync.application.download_manager import DownloadManager
from idat_sync.application.ports import Downloader
from idat_sync.application.sync_service import IDATSync
from idat_sync.application.task_orchestrator import TaskOrchestrator
from idat_sync.infrastructure.auth import Credentials
from idat_sync.infrastructure.downloaders import (
    MoodleDownloader,
    RecordingDownloader,
    SharepointDownloader,
    StringDownloader,
)
from idat_sync.infrastructure.filesystem import LocalFileSystemAdapter
from idat_sync.infrastructure.moodle_adapter import MoodleAdapter
from idat_sync.infrastructure.moodle_client import MoodleClient
from idat_sync.infrastructure.sharepoint_adapter import SharepointApiAdapter
from idat_sync.infrastructure.sharepoint_client import SharepointClient


def create_sync_service(credentials: Credentials) -> IDATSync:
    sharepoint_storage_state = credentials.get("sharepoint_storage_state")
    cookies = (
        sharepoint_storage_state.get("cookies", [])
        if sharepoint_storage_state
        else []
    )

    moodle_client = MoodleClient(credentials.get("token"))
    moodle_adapter = MoodleAdapter(moodle_client)

    sharepoint_raw_client = SharepointClient(cookies)
    sharepoint_adapter = SharepointApiAdapter(sharepoint_raw_client)

    course_repository = CourseRepository(
        course_gateway=moodle_adapter,
        recording_gateway=sharepoint_adapter,
        url_strategies=[moodle_adapter, sharepoint_adapter],
    )

    download_tasks_group: list[list[Downloader]] = [
        [MoodleDownloader(moodle_adapter)],
        [StringDownloader()],
        [
            SharepointDownloader(sharepoint_adapter),
            RecordingDownloader(cookies),
        ],
    ]
    downloaders: list[Downloader] = [
        downloader
        for downloaders in download_tasks_group
        for downloader in downloaders
    ]

    file_system = LocalFileSystemAdapter()
    download_manager = DownloadManager(downloaders, file_system=file_system)
    task_orchestrator = TaskOrchestrator(download_tasks_group)

    return IDATSync(
        course_repository=course_repository,
        download_manager=download_manager,
        task_orchestrator=task_orchestrator,
        file_system=file_system,
    )


__all__ = ["create_sync_service"]
