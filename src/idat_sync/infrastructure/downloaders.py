"""Content downloader implementations for Moodle, SharePoint, text, and video."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import platform
import re
import sys
import threading
import urllib.error
from typing import TYPE_CHECKING, Any, BinaryIO, cast

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
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from idat_sync.infrastructure.moodle_client import MoodleClient
    from idat_sync.infrastructure.sharepoint_client import SharepointClient

_logger = logging.getLogger(__name__)


async def _atomic_download(
    target_path: Path, writer: Callable[[BinaryIO], Awaitable[None]]
) -> None:
    os_target = windows_long_path(target_path)
    os_target.parent.mkdir(parents=True, exist_ok=True)
    part_path = os_target.with_name(f"{os_target.name}.part")
    try:
        with part_path.open("wb") as f:
            await writer(f)
        part_path.replace(os_target)
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise


class MoodleDownloader(Downloader[MoodleContent]):
    """Downloader for files hosted on Moodle."""

    content_type = MoodleContent

    def __init__(self, client: MoodleClient) -> None:
        """Initialize the downloader with a MoodleClient."""
        self.moodle_client = client

    async def download(self, content: MoodleContent) -> None:
        """Download ``content`` atomically using the Moodle client."""
        await _atomic_download(
            content.path,
            lambda f: self.moodle_client.download_file(content.url, f),
        )


class SharepointDownloader(Downloader[SharepointContent]):
    """Downloader for files hosted on SharePoint or OneDrive."""

    content_type = SharepointContent

    def __init__(self, client: SharepointClient) -> None:
        """Initialize the downloader with a SharepointClient."""
        self.sharepoint_client = client

    async def download(self, content: SharepointContent) -> None:
        """Download ``content`` atomically using the SharePoint client."""
        await _atomic_download(
            content.path,
            lambda f: self.sharepoint_client.download_file(content.file, f),
        )


class StringDownloader(Downloader[StringContent]):
    """Downloader that renders StringContent items to disk."""

    content_type = StringContent

    def can_handle(self, content: StringContent) -> bool:
        """Return whether ``content`` renders to non-empty UTF-8 bytes."""
        return content.rendered_bytes() is not None

    async def download(self, content: StringContent) -> None:
        """Write rendered bytes of ``content`` atomically to disk."""
        data = content.rendered_bytes() or b""

        async def _write(f: BinaryIO) -> None:
            f.write(data)

        await _atomic_download(content.path, _write)


class RecordingDownloader(Downloader[RecordingContent]):
    """Downloader for SharePoint/Teams video recordings via yt-dlp."""

    content_type = RecordingContent

    def __init__(
        self,
        cookies: list[CookieData],
        sharepoint_personal_host: str,
    ) -> None:
        """Initialize the recording downloader with cookies and personal host."""
        self.cookies = self.netscape_cookies_format(cookies)
        self.sharepoint_personal_host = sharepoint_personal_host.lower()

    def can_handle(self, content: RecordingContent) -> bool:
        """Return whether ``content`` points to the configured personal host."""
        return f"{self.sharepoint_personal_host}/:v:" in content.url.lower()

    @staticmethod
    def netscape_cookies_format(cookies: list[CookieData]) -> str:
        """Serialize CookieData entries into Netscape HTTP Cookie File format."""
        lines = ["# Netscape HTTP Cookie File"]

        for cookie in cookies:
            name = cookie.get("name", "")
            value = cookie.get("value", "")
            domain = cookie.get("domain", "")
            path = cookie.get("path", "/")
            secure = "TRUE" if cookie.get("secure", False) else "FALSE"
            expires = cookie.get("expires", 0)
            try:
                expires_int = int(expires if expires and expires > 0 else 0)
            except (TypeError, ValueError):
                expires_int = 0

            if not domain or not name:
                continue

            include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
            lines.append(
                "\t".join(
                    [
                        domain,
                        include_subdomains,
                        path,
                        secure,
                        str(expires_int),
                        name,
                        value,
                    ]
                )
            )
        return "\n".join(lines)

    def _download_sync(
        self, content: RecordingContent, cancel_event: threading.Event
    ) -> None:
        _logger.info("Downloading share url: %s", content.url)
        os_target = windows_long_path(content.path)
        os_target.parent.mkdir(parents=True, exist_ok=True)
        output_path_template = (
            str(os_target)
            if platform.system() == "Windows"
            else os.path.abspath(os_target)  # noqa: PTH100
        )

        def _progress_hook(_status: dict[str, Any]) -> None:
            if cancel_event.is_set():
                raise DownloadCancelled("Download cancelled")

        ydl_opts: dict[str, Any] = {
            "cookiefile": io.StringIO(self.cookies),
            "format_sort": ["proto:dash"],
            "postprocessors": [{"key": "FFmpegMetadata"}],
            "format": "bestvideo+bestaudio/bestvideo",
            "outtmpl": output_path_template,
            "progress_hooks": [_progress_hook],
        }
        meipass = getattr(sys, "_MEIPASS", None)
        if getattr(sys, "frozen", False) and meipass is not None:
            ydl_opts["ffmpeg_location"] = meipass
        try:
            with yt_dlp.YoutubeDL(cast("Any", ydl_opts)) as ydl:
                ydl.download([content.url])
        except DownloadError as exc:
            cause = exc.exc_info[1] if exc.exc_info and len(exc.exc_info) > 1 else None
            if (
                isinstance(cause, urllib.error.HTTPError) and cause.code in (401, 403)
            ) or re.search(
                r"\b(?:HTTP Error 40[13]|401 Unauthorized|403 Forbidden)\b",
                str(exc),
            ):
                raise CredentialsExpiredError(str(exc)) from exc
            raise

    async def download(self, content: RecordingContent) -> None:
        """Download ``content`` in a worker thread, supporting cancellation."""
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
