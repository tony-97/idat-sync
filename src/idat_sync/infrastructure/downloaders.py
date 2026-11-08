from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import io
import os
from pathlib import Path
import time
from typing import TYPE_CHECKING

from office365.sharepoint.files.file import File
from playwright._impl._api_structures import StorageStateCookie
import requests
import yt_dlp

from idat_sync.domain.content import (
    Content,
    MoodleContent,
    RecordingContent,
    SharepointContent,
    StringContent,
)
from idat_sync.utils import REQUEST_DELAY, RQ_HEADER

if TYPE_CHECKING:
    from idat_sync.infrastructure.moodle_adapter import MoodleAdapter
    from idat_sync.infrastructure.sharepoint_adapter import SharepointApiAdapter


@dataclass(eq=False)
class Downloader[T: DownloadTask](ABC):
    tasks: list[T] = field(default_factory=list)

    def create_moodle_download_task(
        self, content: MoodleContent
    ) -> DownloadTask | None:
        return None

    def create_sharepoint_download_task(
        self, content: SharepointContent
    ) -> DownloadTask | None:
        return None

    def create_string_download_task(
        self, content: StringContent
    ) -> DownloadTask | None:
        return None

    def create_recording_download_task(
        self, content: RecordingContent
    ) -> DownloadTask | None:
        return None


@dataclass
class DownloadTask[D: Downloader]:
    path: str
    last_modified: float
    downloader: D

    @abstractmethod
    def download(self) -> None: ...


@dataclass
class MoodleDownloadTask(DownloadTask["MoodleDownloader"]):
    url: str

    def download(self) -> None:
        self.downloader.download(self)


@dataclass
class RecordingDownloadTask(DownloadTask["RecordingDownloader"]):
    url: str

    def download(self) -> None:
        return self.downloader.download(self)


@dataclass
class SharepointDownloadTask(DownloadTask["SharepointDownloader"]):
    file: File

    def download(self) -> None:
        return self.downloader.download(self)


@dataclass
class StringDownloadTask(DownloadTask["StringDownloader"]):
    contents: str

    def download(self) -> None:
        return self.downloader.download(self)


class MoodleDownloader(Downloader[MoodleDownloadTask]):
    def __init__(self, client: MoodleAdapter) -> None:
        super().__init__()
        self.moodle_client = client

    def create_moodle_download_task(self, content: MoodleContent):
        return MoodleDownloadTask(
            str(content.path), content.last_modified, self, content.url
        )

    def download(self, file_info: MoodleDownloadTask) -> None:
        Path(file_info.path).parent.mkdir(parents=True, exist_ok=True)
        with requests.get(
            file_info.url,
            params={
                "wstoken": self.moodle_client.token,
                "token": self.moodle_client.token,
            },
            stream=True,
            allow_redirects=True,
            timeout=60,
            headers=RQ_HEADER,
        ) as r:
            if r.status_code == 403:
                raise RuntimeError(
                    "403 Forbidden: your session cookie is missing/expired."
                )
            r.raise_for_status()
            with open(file_info.path, "wb") as f:
                for part in r.iter_content(chunk_size=1024 * 256):
                    if part:
                        f.write(part)
            time.sleep(REQUEST_DELAY)


class SharepointDownloader(Downloader[SharepointDownloadTask]):
    def __init__(self, client: SharepointApiAdapter) -> None:
        super().__init__()
        self.sharepoint_client = client

    def create_sharepoint_download_task(
        self, content: SharepointContent
    ) -> SharepointDownloadTask:
        return SharepointDownloadTask(
            str(content.path), content.last_modified, self, content.file
        )

    def download(self, file_info: SharepointDownloadTask) -> None:
        Path(file_info.path).parent.mkdir(parents=True, exist_ok=True)
        with open(file_info.path, "wb") as local_file:
            file_info.file.download(
                local_file
            ).execute_query_with_incremental_retry()
            time.sleep(REQUEST_DELAY)


class StringDownloader(Downloader[StringDownloadTask]):
    def create_string_download_task(
        self, content: StringContent
    ) -> StringDownloadTask | None:
        text = "".join(content.contents).strip()
        if not text:
            return None
        return StringDownloadTask(
            str(content.path), content.last_modified, self, text
        )

    def download(self, task: StringDownloadTask) -> None:
        Path(task.path).parent.mkdir(parents=True, exist_ok=True)
        with open(task.path, "w", encoding="utf-8") as f:
            f.write(task.contents)


class RecordingDownloader(Downloader[RecordingDownloadTask]):
    def __init__(self, cookies: list[StorageStateCookie]) -> None:
        super().__init__()
        self.cookies = self.netscape_cookies_format(cookies)

    def create_recording_download_task(
        self, content: RecordingContent
    ) -> RecordingDownloadTask | None:
        if "idat628-my.sharepoint.com/:v:" in content.url:
            return RecordingDownloadTask(
                str(content.path), content.last_modified, self, content.url
            )
        return None

    def netscape_cookies_format(self, cookies: list[StorageStateCookie]):
        lines = []
        lines.append("# Netscape HTTP Cookie File")

        for c in cookies:
            to_bool_str = lambda value: "TRUE" if value else "FALSE"
            name = c.get("name", "")
            value = c.get("value", "")
            domain = c.get("domain", "")
            path = c.get("path", "/")
            secure = bool(c.get("secure", False))
            expires = c.get("expires", 0)
            try:
                expires_int = int(expires if expires and expires > 0 else 0)
            except Exception:
                expires_int = 0

            if not domain or not name:
                continue

            include_subdomains = domain.startswith(".")
            line = "\t".join(
                [
                    domain,
                    to_bool_str(include_subdomains),
                    path,
                    to_bool_str(secure),
                    str(expires_int),
                    name,
                    value,
                ]
            )
            lines.append(line)
        return "\n".join(lines)

    def download(self, task: RecordingDownloadTask) -> None:
        print(
            f"downloading share url: {task.url}",
        )
        target_path = Path(task.path)
        if target_path.suffix:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            output_path_template = f"\\\\?\\{os.path.abspath(target_path)}"
        else:
            target_path.mkdir(parents=True, exist_ok=True)
            output_path_template = os.path.join(
                f"\\\\?\\{os.path.abspath(target_path)}",
                "%(title)s %(id)s.%(ext)s",
            )
        ydl_opts = {
            "cookiefile": io.StringIO(self.cookies),
            "format_sort": ["proto:dash"],
            "postprocessors": [{"key": "FFmpegMetadata"}],
            "format": "bestvideo+bestaudio/bestvideo",
            "outtmpl": output_path_template,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
            ydl.download([task.url])


__all__ = [
    "Content",
    "DownloadTask",
    "Downloader",
    "MoodleContent",
    "MoodleDownloadTask",
    "MoodleDownloader",
    "RecordingContent",
    "RecordingDownloadTask",
    "RecordingDownloader",
    "SharepointContent",
    "SharepointDownloadTask",
    "SharepointDownloader",
    "StringContent",
    "StringDownloadTask",
    "StringDownloader",
]
