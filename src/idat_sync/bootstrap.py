"""Composition root wiring application services, adapters, and logging."""

import logging
from pathlib import Path

from idat_sync.application.auth_service import AuthService
from idat_sync.application.course_repository import CourseRepository
from idat_sync.application.download_manager import DownloadManager
from idat_sync.application.ports import Credentials, Downloader
from idat_sync.application.sync_service import IDATSync
from idat_sync.config import SyncConfig, is_android
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
from idat_sync.infrastructure.rate_limiter import AsyncRateLimiter
from idat_sync.infrastructure.sharepoint_adapter import SharepointApiAdapter
from idat_sync.infrastructure.sharepoint_auth import (
    AndroidWebViewDriver,
    PyWebviewDriver,
    SharepointBrowserAuth,
)
from idat_sync.infrastructure.sharepoint_client import SharepointClient


def configure_logging(log_file: Path) -> logging.Logger:
    """Configure stream and file handlers on the ``idat_sync`` logger."""
    logger = logging.getLogger("idat_sync")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    return logger


async def create_sync_service(
    credentials: Credentials,
    config: SyncConfig,
) -> IDATSync:
    """Build a fully wired :class:`IDATSync` service from credentials and config."""
    storage_state = credentials.get("sharepoint_storage_state")
    cookies = storage_state.get("cookies", []) if storage_state else []

    moodle_limiter = AsyncRateLimiter(config.request_delay)
    sharepoint_limiter = AsyncRateLimiter(config.request_delay)

    moodle_client = MoodleClient(
        credentials.get("token"),
        base_url=config.moodle_base_url,
        rate_limiter=moodle_limiter,
    )
    moodle_adapter = MoodleAdapter(
        moodle_client,
        moodle_base_url=config.moodle_base_url,
    )

    try:
        sharepoint_raw_client = await SharepointClient.create(
            cookies,
            sharepoint_personal_host=config.sharepoint_personal_host,
            academic_site_url=config.sharepoint_academic_site_url,
            rate_limiter=sharepoint_limiter,
        )
    except BaseException:
        await moodle_client.aclose()
        raise
    sharepoint_adapter = SharepointApiAdapter(
        sharepoint_raw_client,
        semester_start=config.semester_start,
        sharepoint_host=config.sharepoint_host,
        sharepoint_personal_host=config.sharepoint_personal_host,
        recordings_folder_name=config.recordings_folder_name,
        week_folder_prefix=config.week_folder_prefix,
        timezone=config.timezone,
    )

    download_tasks_group: list[list[Downloader]] = [
        [MoodleDownloader(moodle_client)],
        [StringDownloader()],
        [
            SharepointDownloader(sharepoint_raw_client),
            RecordingDownloader(
                cookies,
                sharepoint_personal_host=config.sharepoint_personal_host,
                rate_limiter=sharepoint_limiter,
            ),
        ],
    ]
    file_system = LocalFileSystemAdapter()

    return IDATSync(
        course_repository=CourseRepository(
            course_gateway=moodle_adapter,
            recording_gateway=sharepoint_adapter,
            url_strategies=[moodle_adapter, sharepoint_adapter],
        ),
        download_manager=DownloadManager(
            [d for group in download_tasks_group for d in group],
            file_system=file_system,
        ),
        downloader_groups=download_tasks_group,
        file_system=file_system,
        recordings_folder_name=config.recordings_folder_name,
        closers=[moodle_client.aclose, sharepoint_raw_client.aclose],
    )


def create_auth_service(config: SyncConfig) -> AuthService:
    """Build an :class:`AuthService` configured from ``config``."""
    return AuthService(
        moodle_auth=MoodleTokenAdapter(MoodleTokenClient(config.moodle_base_url)),
        sharepoint_auth=SharepointBrowserAuth(
            sharepoint_host=config.sharepoint_host,
            sharepoint_personal_host=config.sharepoint_personal_host,
            email_domain=config.email_domain,
            driver=AndroidWebViewDriver() if is_android() else PyWebviewDriver(),
        ),
        credential_store=JsonCredentialStore(config.session_file),
    )


__all__ = [
    "configure_logging",
    "create_auth_service",
    "create_sync_service",
]
