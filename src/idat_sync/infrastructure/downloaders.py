from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import io
import logging
import os
from pathlib import Path
import platform
import re
import sys
import threading
from typing import TYPE_CHECKING, BinaryIO
import urllib.error

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError

from idat_sync.application.ports import (
    CookieData,
    CredentialsExpiredError,
    Downloader,
)
from idat_sync.domain.content import (
    MoodleContent,
    RecordingContent,
    SharepointContent,
    StringContent,
)
from idat_sync.infrastructure.filesystem import windows_long_path

if TYPE_CHECKING:
    from idat_sync.infrastructure.moodle_client import MoodleClient
    from idat_sync.infrastructure.sharepoint_client import SharepointClient


async def _atomic_download(
    target_path: Path, writer: Callable[[BinaryIO], Awaitable[None]]
) -> None:
    os_target = windows_long_path(target_path)
    os_target.parent.mkdir(parents=True, exist_ok=True)
    part_path = os_target.with_name(f"{os_target.name}.part")
    try:
        with open(part_path, "wb") as f:
            await writer(f)
        os.replace(part_path, os_target)
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise


class MoodleDownloader(Downloader[MoodleContent]):
    content_type = MoodleContent

    def __init__(self, client: MoodleClient) -> None:
        self.moodle_client = client

    async def download(self, content: MoodleContent) -> None:
        await _atomic_download(
            content.path,
            lambda f: self.moodle_client.download_file(content.url, f),
        )


class SharepointDownloader(Downloader[SharepointContent]):
    content_type = SharepointContent

    def __init__(self, client: SharepointClient) -> None:
        self.sharepoint_client = client

    async def download(self, content: SharepointContent) -> None:
        await _atomic_download(
            content.path,
            lambda f: self.sharepoint_client.download_file(content.file, f),
        )


class StringDownloader(Downloader[StringContent]):
    content_type = StringContent

    def can_handle(self, content: StringContent) -> bool:
        return content.rendered_bytes() is not None

    async def download(self, content: StringContent) -> None:
        data = content.rendered_bytes() or b""

        async def _write(f: BinaryIO) -> None:
            f.write(data)

        await _atomic_download(content.path, _write)


class RecordingDownloader(Downloader[RecordingContent]):
    content_type = RecordingContent

    def __init__(
        self,
        cookies: list[CookieData],
        sharepoint_personal_host: str,
    ) -> None:
        self.cookies = self.netscape_cookies_format(cookies)
        self.sharepoint_personal_host = sharepoint_personal_host.lower()

    def can_handle(self, content: RecordingContent) -> bool:
        return f"{self.sharepoint_personal_host}/:v:" in content.url.lower()

    def netscape_cookies_format(self, cookies: list[CookieData]):
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

    def _download_sync(
        self, content: RecordingContent, cancel_event: threading.Event
    ) -> None:
        logging.getLogger(__name__).info(
            "Downloading share url: %s", content.url
        )
        os_target = windows_long_path(content.path)
        os_target.parent.mkdir(parents=True, exist_ok=True)
        output_path_template = (
            str(os_target)
            if platform.system() == "Windows"
            else os.path.abspath(os_target)
        )

        def _progress_hook(_status: dict) -> None:
            if cancel_event.is_set():
                raise DownloadCancelled("Download cancelled")

        ydl_opts = {
            "cookiefile": io.StringIO(self.cookies),
            "format_sort": ["proto:dash"],
            "postprocessors": [{"key": "FFmpegMetadata"}],
            "format": "bestvideo+bestaudio/bestvideo",
            "outtmpl": output_path_template,
            "progress_hooks": [_progress_hook],
        }
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            ydl_opts["ffmpeg_location"] = sys._MEIPASS  # type: ignore
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
                ydl.download([content.url])
        except DownloadError as exc:
            cause = exc.exc_info[1] if exc.exc_info and len(exc.exc_info) > 1 else None
            if (
                isinstance(cause, urllib.error.HTTPError) and cause.code in (401, 403)
            ) or re.search(r"\b(?:HTTP Error 40[13]|401 Unauthorized|403 Forbidden)\b", str(exc)):
                raise CredentialsExpiredError(str(exc)) from exc
            raise

    async def download(self, content: RecordingContent) -> None:
        cancel_event = threading.Event()
        try:
            await asyncio.to_thread(self._download_sync, content, cancel_event)
        except asyncio.CancelledError:
            cancel_event.set()
            raise


__all__ = [
    "MoodleDownloader",
    "RecordingDownloader",
    "SharepointDownloader",
    "StringDownloader",
]
