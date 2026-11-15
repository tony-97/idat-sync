import logging
from pathlib import Path

from idat_sync.application.auth_service import AuthService
from idat_sync.application.course_repository import CourseRepository
from idat_sync.application.download_manager import DownloadManager
from idat_sync.application.ports import Credentials, Downloader
from idat_sync.application.sync_service import IDATSync
from idat_sync.application.task_orchestrator import TaskOrchestrator
from idat_sync.config import SyncConfig
from idat_sync.infrastructure.credential_store import JsonCredentialStore
from idat_sync.infrastructure.downloaders import (
    MoodleDownloader,
    RecordingDownloader,
    SharepointDownloader,
    StringDownloader,
)
from idat_sync.infrastructure.filesystem import LocalFileSystemAdapter
from idat_sync.infrastructure.moodle_adapter import MoodleAdapter, MoodleTokenAdapter
from idat_sync.infrastructure.moodle_client import MoodleClient, MoodleTokenClient
from idat_sync.infrastructure.sharepoint_adapter import SharepointApiAdapter
from idat_sync.infrastructure.sharepoint_auth import SharepointBrowserAuth
from idat_sync.infrastructure.sharepoint_client import SharepointClient


def configure_logging(log_file: Path) -> logging.Logger:
    logger = logging.getLogger("idat_sync")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    return logger


async def create_sync_service(
    credentials: Credentials,
    config: SyncConfig,
) -> IDATSync:
    sharepoint_storage_state = credentials.get("sharepoint_storage_state")
    cookies = (
        sharepoint_storage_state.get("cookies", [])
        if sharepoint_storage_state
        else []
    )

    moodle_client = MoodleClient(
        credentials.get("token"),
        base_url=config.moodle_base_url,
        request_delay=config.request_delay,
    )
    moodle_adapter = MoodleAdapter(
        moodle_client,
        moodle_base_url=config.moodle_base_url,
    )

    sharepoint_raw_client = await SharepointClient.create(
        cookies,
        sharepoint_personal_host=config.sharepoint_personal_host,
        academic_site_url=config.sharepoint_academic_site_url,
        request_delay=config.request_delay,
    )
    sharepoint_adapter = SharepointApiAdapter(
        sharepoint_raw_client,
        semester_start=config.semester_start,
        sharepoint_host=config.sharepoint_host,
        sharepoint_personal_host=config.sharepoint_personal_host,
        recordings_folder_name=config.recordings_folder_name,
        week_folder_prefix=config.week_folder_prefix,
    )

    course_repository = CourseRepository(
        course_gateway=moodle_adapter,
        recording_gateway=sharepoint_adapter,
        url_strategies=[moodle_adapter, sharepoint_adapter],
    )

    download_tasks_group: list[list[Downloader]] = [
        [MoodleDownloader(moodle_client)],
        [StringDownloader()],
        [
            SharepointDownloader(sharepoint_raw_client),
            RecordingDownloader(
                cookies,
                sharepoint_personal_host=config.sharepoint_personal_host,
            ),
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
        recordings_folder_name=config.recordings_folder_name,
    )


def create_auth_service(config: SyncConfig) -> AuthService:
    return AuthService(
        moodle_auth=MoodleTokenAdapter(MoodleTokenClient(config.moodle_base_url)),
        sharepoint_auth=SharepointBrowserAuth(
            sharepoint_host=config.sharepoint_host,
            sharepoint_personal_host=config.sharepoint_personal_host,
            email_domain=config.email_domain,
        ),
        credential_store=JsonCredentialStore(config.session_file),
    )


__all__ = ["configure_logging", "create_auth_service", "create_sync_service"]
