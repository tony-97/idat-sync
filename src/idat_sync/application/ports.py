from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypedDict

from idat_sync.domain.content import Content

if TYPE_CHECKING:
    from idat_sync.domain.content import RecordingContent
    from idat_sync.domain.course import Course


class CookieData(TypedDict, total=False):
    name: str
    value: str
    domain: str
    path: str
    expires: float
    secure: bool


class StorageStateData(TypedDict, total=False):
    cookies: list[CookieData]


class Credentials(TypedDict):
    sharepoint_storage_state: StorageStateData
    token: str


@dataclass
class DownloadTask[C: Content]:
    content: C
    downloader: Downloader[C]

    @property
    def path(self) -> Path:
        return self.content.path

    async def download(self) -> None:
        await self.downloader.download(self.content)


class Downloader[C: Content](ABC):
    content_type: type[C]

    def can_handle(self, content: C) -> bool:
        return True

    def create_task(self, content: Content) -> DownloadTask[C] | None:
        if isinstance(content, self.content_type) and self.can_handle(content):
            return DownloadTask(content=content, downloader=self)
        return None

    @abstractmethod
    async def download(self, content: C) -> None: ...


class FileSystemPort(Protocol):
    def scan_mtimes(self, root_dir: Path | str) -> Mapping[Path, int]:
        ...

    def ensure_dir(self, path: Path | str) -> None:
        ...


class CourseGateway(Protocol):
    async def list_courses(self) -> list[Course]:
        ...

    async def populate_course_sections(self, course: Course) -> Course:
        ...

    def invalidate_cache(self) -> None:
        ...


class RecordingGateway(Protocol):
    async def fetch_recordings(self, course_name: str) -> list[RecordingContent]:
        ...


class UrlContentStrategy(Protocol):
    def can_handle(self, url: str) -> bool:
        ...

    async def list_url_content(
        self, url: str, filename: str, last_modified: int
    ) -> list[Content]:
        ...


class MoodleTokenPort(Protocol):
    async def fetch_token(self, username: str, password: str) -> str | None:
        ...


class SharepointAuthPort(Protocol):
    async def fetch_storage_state(
        self,
        username: str,
        password: str,
        get_mfa_code: Callable[[], Awaitable[str]],
    ) -> StorageStateData | None:
        ...


class CredentialsExpiredError(RuntimeError):
    """Raised when stored Moodle or SharePoint Credentials are rejected or expired."""


class CredentialStorePort(Protocol):
    def load(self) -> Credentials | None:
        ...

    def save(self, credentials: Credentials) -> bool:
        ...

    def clear(self) -> None:
        ...


__all__ = [
    "CookieData",
    "CourseGateway",
    "CredentialStorePort",
    "Credentials",
    "CredentialsExpiredError",
    "DownloadTask",
    "Downloader",
    "FileSystemPort",
    "MoodleTokenPort",
    "RecordingGateway",
    "SharepointAuthPort",
    "StorageStateData",
    "UrlContentStrategy",
]
