from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from idat_sync.domain.content import Content

if TYPE_CHECKING:
    from idat_sync.domain.content import RecordingContent
    from idat_sync.domain.course import Course


@dataclass
class DownloadTask[C: Content]:
    content: C
    downloader: Downloader[C]

    @property
    def path(self) -> Path:
        return self.content.path

    def download(self) -> None:
        self.downloader.download(self.content)


class Downloader[C: Content](ABC):
    content_type: type[C]

    def can_handle(self, content: C) -> bool:
        return True

    def create_task(self, content: Content) -> DownloadTask[C] | None:
        if isinstance(content, self.content_type) and self.can_handle(content):
            return DownloadTask(content=content, downloader=self)
        return None

    @abstractmethod
    def download(self, content: C) -> None: ...


class FileSystemPort(Protocol):
    def scan_mtimes(self, root_dir: Path | str) -> Mapping[Path, int]:
        ...

    def ensure_dir(self, path: Path | str) -> None:
        ...


class CourseGateway(Protocol):
    def get_courses_with_contents(self) -> list[Course]:
        ...


class RecordingGateway(Protocol):
    def fetch_recordings(self, course_name: str) -> list[RecordingContent]:
        ...


class UrlContentStrategy(Protocol):
    def can_handle(self, url: str) -> bool:
        ...

    def list_url_content(
        self, url: str, filename: str, last_modified: int
    ) -> list[Content]:
        ...


__all__ = [
    "CourseGateway",
    "DownloadTask",
    "Downloader",
    "FileSystemPort",
    "RecordingGateway",
    "UrlContentStrategy",
]
