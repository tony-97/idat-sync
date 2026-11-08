from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Self, TYPE_CHECKING

if TYPE_CHECKING:
    from idat_sync.infrastructure.downloaders import Downloader, DownloadTask


@dataclass
class Content(ABC):
    path: Path
    last_modified: int

    @property
    def filename(self) -> str:
        return self.path.name

    def in_folder(self, folder: str | Path) -> Self:
        if not folder:
            return self
        return replace(self, path=Path(folder) / self.path)

    @abstractmethod
    def generate_task(self, downloader: Downloader) -> DownloadTask | None: ...


@dataclass
class MoodleContent(Content):
    url: str

    def generate_task(self, downloader: Downloader):
        return downloader.create_moodle_download_task(self)


@dataclass
class SharepointContent(Content):
    file: Any

    def generate_task(self, downloader: Downloader):
        return downloader.create_sharepoint_download_task(self)


@dataclass
class StringContent(Content):
    contents: list[str]

    def generate_task(self, downloader: Downloader):
        return downloader.create_string_download_task(self)


@dataclass
class RecordingContent(Content):
    url: str

    def generate_task(self, downloader: Downloader):
        return downloader.create_recording_download_task(self)
