"""Infrastructure adapters, clients, and persistence implementations."""

from idat_sync.infrastructure.credential_store import JsonCredentialStore
from idat_sync.infrastructure.downloaders import (
    MoodleDownloader,
    RecordingDownloader,
    SharepointDownloader,
    StringDownloader,
)
from idat_sync.infrastructure.moodle_adapter import MoodleAdapter, MoodleTokenAdapter
from idat_sync.infrastructure.moodle_client import MoodleClient, MoodleTokenClient
from idat_sync.infrastructure.sharepoint_adapter import SharepointApiAdapter
from idat_sync.infrastructure.sharepoint_auth import SharepointBrowserAuth
from idat_sync.infrastructure.sharepoint_client import SharepointClient

__all__ = [
    "JsonCredentialStore",
    "MoodleAdapter",
    "MoodleClient",
    "MoodleDownloader",
    "MoodleTokenAdapter",
    "MoodleTokenClient",
    "RecordingDownloader",
    "SharepointApiAdapter",
    "SharepointBrowserAuth",
    "SharepointClient",
    "SharepointDownloader",
    "StringDownloader",
]
