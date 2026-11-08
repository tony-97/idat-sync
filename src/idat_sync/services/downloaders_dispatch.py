from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from dataclasses import dataclass
import datetime
from urllib.parse import urlparse
import os
import requests
import time
import io

from office365.sharepoint.files.file import File
from office365.sharepoint.folders.folder import Folder

from playwright._impl._api_structures import StorageStateCookie

from idat_sync.services.moodle_adapter import MoodleApiAdapter
from idat_sync.services.sharepoint_adapter import SharepointApiAdapter
from idat_sync.utils import REQUEST_DELAY, RQ_HEADER, safe_filename


class Downloader(ABC):
    @abstractmethod
    def can_handle(self, content) -> bool:
        """Returns True if this downloader can handle the given URL."""
        pass

    @abstractmethod
    def list_files(self, parent_path: str, content) -> list["FileDownloadInfoBase"]:
        pass

    def process_download(self, file_info: "FileDownloadInfoBase"):
        file_info.accept(self)

    def download_sharepoint_file(self, file_info: "SharepointFileInfo"):
        raise NotImplementedError("Not supported for this downloader")

    def download_moodle_file(self, file_info: "MoodleFileInfo"):
        raise NotImplementedError("Not supported for this downloader")


D = TypeVar("D", bound=Downloader)


@dataclass
class FileDownloadInfoBase(Generic[D]):
    path: str
    last_modified: float
    downloader: D

    @abstractmethod
    def accept(self, downloader: "Downloader"): ...


@dataclass
class MoodleFileInfo(FileDownloadInfoBase["MoodleDownloader"]):
    url: str

    def accept(self, downloader: "Downloader") -> None:
        downloader.download_moodle_file(self)


@dataclass
class SharepointFileInfo(FileDownloadInfoBase["SharepointDownloader"]):
    file: File

    def accept(self, downloader: "Downloader") -> None:
        downloader.download_sharepoint_file(self)


class MoodleDownloader(Downloader):
    def __init__(self, client: MoodleApiAdapter) -> None:
        super().__init__()
        self.moodle_client = client

    def can_handle(self, content) -> bool:
        return "aulavirtual" in urlparse(content.get("fileurl")).netloc

    def list_files(self, parent_path: str, content) -> list[FileDownloadInfoBase]:
        file_list = list[FileDownloadInfoBase]()
        # TODO list recursive moodle folder url
        if (file_url := content.get("fileurl")) and (
            file_name := content.get("filename")
        ):
            file_list.append(
                MoodleFileInfo(
                    file_url,
                    content.get("timemodified", 0),
                    self,
                    os.path.join(parent_path, file_name),
                )
            )
        return file_list

    def download_moodle_file(self, file_info: MoodleFileInfo) -> None:
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


class SharepointDownloader(Downloader):
    def __init__(self, client: SharepointApiAdapter) -> None:
        super().__init__()
        self.sharepoint_client = client

    def can_handle(self, content) -> bool:
        return "idat628.sharepoint" in urlparse(content.get("fileurl")).netloc

    def __get_name(self, f: File | Folder):
        return f.name or safe_filename(f.serverRelativeUrl or "")

    def __file_info(self, file: File, parent_path: str) -> SharepointFileInfo:
        self.sharepoint_client.client.load(file, ["Name"])
        self.sharepoint_client.client.execute_query_with_incremental_retry()
        time.sleep(REQUEST_DELAY)
        return SharepointFileInfo(
            os.path.join(parent_path, self.__get_name(file)),
            (
                file.time_last_modified
                or datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
            ).timestamp(),
            self,
            file,
        )

    def __list_folder_files(
        self, file_list: list[FileDownloadInfoBase], folder: Folder, parent_path: str
    ):
        files = folder.files.get_all().execute_query_retry()
        time.sleep(REQUEST_DELAY)
        folders = folder.folders.get_all().execute_query_retry()
        time.sleep(REQUEST_DELAY)
        for file in files:
            file_list.append(self.__file_info(file, parent_path))
        for folder in folders:
            if save_name := self.__get_name(folder):
                sub_folder_path = os.path.join(parent_path, save_name)
                self.__list_folder_files(file_list, folder, sub_folder_path)

    def list_files(self, parent_path: str, content) -> list[FileDownloadInfoBase]:
        file_list = list[FileDownloadInfoBase]()
        if (file_url := content.get("fileurl")) and (
            file_name := content.get("filename")
        ):
            if file_url.startswith("https://idat628.sharepoint.com/:f:"):
                folder = self.sharepoint_client.client.web.get_folder_by_guest_url(
                    file_url
                ).execute_query_with_incremental_retry()
                time.sleep(REQUEST_DELAY)
                folder_path = os.path.join(parent_path, file_name)
                self.__list_folder_files(file_list, folder, folder_path)
            else:
                file = self.sharepoint_client.client.web.get_file_by_guest_url(file_url)
                file_list.append(self.__file_info(file, parent_path))
        return file_list

    def download_sharepoint_file(self, file_info: SharepointFileInfo):
        with open(file_info.path, "wb") as local_file:
            file_info.file.download(local_file).execute_query_with_incremental_retry()
            time.sleep(REQUEST_DELAY)


class MemoryDownloader(Downloader):
    def can_handle(self, content) -> bool: ...


class RecordingDownloader(Downloader):
    def __init__(self, cookies: list[StorageStateCookie]) -> None:
        super().__init__()
        self.cookies = self.netscape_cookies_format(cookies)

    def can_handle(self, content) -> bool:
        return "idat628-my.sharepoint.com/:v:" in content.get("fileurl")

    def netscape_cookies_format(self, cookies: list[StorageStateCookie]):
        lines = []
        lines.append("# Netscape HTTP Cookie File")

        for c in cookies:
            to_bool_str = lambda value: "TRUE" if value else "FALSE"
            # Required fields with sane defaults
            name = c.get("name", "")
            value = c.get("value", "")
            domain = c.get("domain", "")
            path = c.get("path", "/")
            secure = bool(c.get("secure", False))
            expires = c.get(
                "expires", 0
            )  # Playwright uses Unix seconds, 0/-1 for session
            try:
                expires_int = int(expires if expires and expires > 0 else 0)
            except Exception:
                expires_int = 0

            if not domain or not name:
                # Skip malformed cookies
                continue

            # include_subdomains flag:
            # TRUE if domain starts with a dot (host-only cookies typically won’t)
            include_subdomains = domain.startswith(".")

            # Netscape format columns:
            # domain, include_subdomains(TRUE|FALSE), path, secure(TRUE|FALSE),
            # expiration (Unix epoch), name, value
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

    def download(self, url: str, dest_path: str, filename: str | None = None) -> None:
        print(
            f"downloading share url: {url}",
        )
        output_path_template = os.path.join(
            f"\\\\?\\{os.path.abspath(dest_path)}", "%(title)s %(id)s.%(ext)s"
        )
        ydl_opts = {
            "cookiefile": io.StringIO(self.cookies),
            "format_sort": ["proto:dash"],
            "postprocessors": [{"key": "FFmpegMetadata"}],
            "format": "bestvideo+bestaudio/bestvideo",
            "outtmpl": output_path_template,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
            ydl.download([url])
