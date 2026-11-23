"""Application port interfaces, credential types, and domain-boundary errors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypedDict

from idat_sync.application.exceptions import (
    AuthenticationError,
    AuthFailureReason,
    CredentialsExpiredError,
    CredentialStoreError,
    FileSystemError,
    FileSystemOperation,
    IDATSyncError,
    RemoteServiceError,
    ServiceName,
)
from idat_sync.domain.content import Content

if TYPE_CHECKING:
    from collections.abc import Awaitable, Mapping
    from pathlib import Path

    from idat_sync.domain.content import RecordingContent
    from idat_sync.domain.course import Course

ProgressCallback = Callable[[int, int | None], None]


class CookieData(TypedDict, total=False):
    """Serialized browser cookie attributes for SharePoint authentication."""

    name: str
    value: str
    domain: str
    path: str
    expires: float
    secure: bool


class StorageStateData(TypedDict, total=False):
    """Browser storage state holding SharePoint session cookies."""

    cookies: list[CookieData]


class Credentials(TypedDict):
    """Moodle token and SharePoint storage state persisted across sessions."""

    sharepoint_storage_state: StorageStateData
    token: str


@dataclass
class DownloadTask[C: Content]:
    """Executable download unit pairing a content item with its downloader."""

    content: C
    downloader: Downloader[C]

    @property
    def path(self) -> Path:
        """Return the target relative path for this download task."""
        return self.content.path

    async def download(self, on_progress: ProgressCallback | None = None) -> None:
        """Execute the download via the bound downloader."""
        if on_progress is None:
            await self.downloader.download(self.content)
        else:
            await self.downloader.download(self.content, on_progress=on_progress)


class Downloader[C: Content](ABC):
    """Abstract base class for content-type-specific downloaders."""

    content_type: type[C]

    def can_handle(self, _content: C) -> bool:
        """Return whether this downloader can handle the given content item."""
        return True

    def create_task(self, content: Content) -> DownloadTask[C] | None:
        """Create a DownloadTask if ``content`` matches this downloader."""
        if isinstance(content, self.content_type) and self.can_handle(content):
            return DownloadTask(content=content, downloader=self)
        return None

    @abstractmethod
    async def download(
        self, content: C, on_progress: ProgressCallback | None = None
    ) -> None:
        """Download ``content`` to its target destination."""


class FileSystemPort(Protocol):
    """Port abstracting local filesystem inspection and directory creation."""

    def scan_mtimes(self, root_dir: Path | str) -> Mapping[Path, int]:
        """Return a mapping of relative paths under ``root_dir`` to mtimes."""
        ...

    def ensure_dir(self, path: Path | str) -> None:
        """Ensure directory ``path`` and its parents exist."""
        ...

    def read_bytes(self, path: Path | str) -> bytes | None:
        """Read raw bytes from ``path``, or return None if missing."""
        ...


class CourseGateway(Protocol):
    """Port for fetching enrolled courses and their module trees."""

    async def list_courses(self) -> list[Course]:
        """Return all enrolled courses."""
        ...

    async def populate_course_sections(self, course: Course) -> Course:
        """Populate sections and modules on ``course``."""
        ...

    def invalidate_cache(self) -> None:
        """Clear any cached course metadata."""
        ...


class RecordingGateway(Protocol):  # pylint: disable=too-few-public-methods
    """Port for fetching class recording items for a course."""

    async def fetch_recordings(self, course_name: str) -> list[RecordingContent]:
        """Return recording items matching ``course_name``."""
        ...


class UrlContentStrategy(Protocol):
    """Strategy port for expanding external URLs into downloadable Content."""

    def can_handle(self, url: str) -> bool:
        """Return whether this strategy handles ``url``."""
        ...

    async def list_url_content(
        self, url: str, filename: str, last_modified: int
    ) -> list[Content]:
        """Resolve ``url`` into concrete Content items."""
        ...


class MoodleTokenPort(Protocol):  # pylint: disable=too-few-public-methods
    """Port for exchanging user credentials for a Moodle API token."""

    async def fetch_token(self, username: str, password: str) -> str:
        """Authenticate and return a Moodle token."""
        ...


class SharepointAuthPort(Protocol):  # pylint: disable=too-few-public-methods
    """Port for interactive SharePoint browser authentication."""

    async def fetch_storage_state(
        self,
        username: str,
        password: str,
        get_mfa_code: Callable[[], Awaitable[str]],
    ) -> StorageStateData:
        """Authenticate via browser/MFA and return SharePoint storage state."""
        ...


class CredentialStorePort(Protocol):
    """Port for persisting and clearing user Credentials."""

    def load(self) -> Credentials | None:
        """Load stored credentials, or return None if unavailable."""
        ...

    def save(self, credentials: Credentials) -> None:
        """Persist ``credentials`` or raise CredentialStoreError on failure."""
        ...

    def clear(self) -> None:
        """Remove stored credentials or raise CredentialStoreError on failure."""
        ...


__all__ = [
    "AuthFailureReason",
    "AuthenticationError",
    "CookieData",
    "CourseGateway",
    "CredentialStoreError",
    "CredentialStorePort",
    "Credentials",
    "CredentialsExpiredError",
    "DownloadTask",
    "Downloader",
    "FileSystemError",
    "FileSystemOperation",
    "FileSystemPort",
    "IDATSyncError",
    "MoodleTokenPort",
    "ProgressCallback",
    "RecordingGateway",
    "RemoteServiceError",
    "ServiceName",
    "SharepointAuthPort",
    "StorageStateData",
    "UrlContentStrategy",
]
