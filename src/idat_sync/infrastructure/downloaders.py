from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import io
import os
from pathlib import Path
import platform
import sys
import time
from typing import TYPE_CHECKING

from playwright._impl._api_structures import StorageStateCookie
import requests
import yt_dlp

from idat_sync.application.ports import DownloadTask, Downloader
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


class MoodleDownloader(Downloader[MoodleContent]):
    content_type = MoodleContent

    def __init__(self, client: MoodleAdapter) -> None:
        self.moodle_client = client

    def download(self, content: MoodleContent) -> None:
        Path(content.path).parent.mkdir(parents=True, exist_ok=True)
        with requests.get(
            content.url,
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
            with open(content.path, "wb") as f:
                for part in r.iter_content(chunk_size=1024 * 256):
                    if part:
                        f.write(part)
            time.sleep(REQUEST_DELAY)


class SharepointDownloader(Downloader[SharepointContent]):
    content_type = SharepointContent

    def __init__(self, client: SharepointApiAdapter) -> None:
        self.sharepoint_client = client

    def download(self, content: SharepointContent) -> None:
        Path(content.path).parent.mkdir(parents=True, exist_ok=True)
        with open(content.path, "wb") as local_file:
            content.file.download_session(
                local_file
            ).execute_query_with_incremental_retry()
            time.sleep(REQUEST_DELAY)


class StringDownloader(Downloader[StringContent]):
    content_type = StringContent

    def can_handle(self, content: StringContent) -> bool:
        return bool("".join(content.contents).strip())

    def download(self, content: StringContent) -> None:
        text = "".join(content.contents).strip()
        Path(content.path).parent.mkdir(parents=True, exist_ok=True)
        with open(content.path, "w", encoding="utf-8") as f:
            f.write(text)


class RecordingDownloader(Downloader[RecordingContent]):
    content_type = RecordingContent

    def __init__(self, cookies: list[StorageStateCookie]) -> None:
        self.cookies = self.netscape_cookies_format(cookies)

    def can_handle(self, content: RecordingContent) -> bool:
        return "idat628-my.sharepoint.com/:v:" in content.url

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

    def download(self, content: RecordingContent) -> None:
        print(
            f"downloading share url: {content.url}",
        )
        prefix = "\\\\?\\" if platform.system() == "Windows" else ""
        target_path = Path(content.path)
        if target_path.suffix:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            output_path_template = f"{prefix}{os.path.abspath(target_path)}"
        else:
            target_path.mkdir(parents=True, exist_ok=True)
            output_path_template = os.path.join(
                f"{prefix}{os.path.abspath(target_path)}",
                "%(title)s %(id)s.%(ext)s",
            )
        ydl_opts = {
            "cookiefile": io.StringIO(self.cookies),
            "format_sort": ["proto:dash"],
            "postprocessors": [{"key": "FFmpegMetadata"}],
            "format": "bestvideo+bestaudio/bestvideo",
            "outtmpl": output_path_template,
        }
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            ydl_opts["ffmpeg_location"] = sys._MEIPASS  # type: ignore
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
            ydl.download([content.url])


__all__ = [
    "Content",
    "DownloadTask",
    "Downloader",
    "MoodleContent",
    "MoodleDownloader",
    "RecordingContent",
    "RecordingDownloader",
    "SharepointContent",
    "SharepointDownloader",
    "StringContent",
    "StringDownloader",
]
