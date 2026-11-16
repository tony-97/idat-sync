from datetime import date, datetime
import pathlib
from pathlib import Path
import re
import urllib.parse
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

from office365.runtime.client_result import ClientResult
from office365.sharepoint.files.file import File
from office365.sharepoint.folders.folder import Folder
from office365.sharepoint.listitems.collection import ListItem
from office365.sharepoint.search.result import SearchResult

from idat_sync.domain.content import RecordingContent, SharepointContent
from idat_sync.domain.path_utils import safe_path_segment
from idat_sync.infrastructure.sharepoint_client import SharepointClient


class SharepointApiAdapter:
    def __init__(
        self,
        client: SharepointClient,
        semester_start: date,
        sharepoint_host: str,
        sharepoint_personal_host: str,
        recordings_folder_name: str,
        week_folder_prefix: str,
        timezone: str,
    ) -> None:
        self.sp_client = client
        self.semester_start = semester_start
        self.sharepoint_host = sharepoint_host.lower()
        self.sharepoint_personal_host = sharepoint_personal_host.lower()
        self.recordings_folder_name = recordings_folder_name
        self.week_folder_prefix = week_folder_prefix
        self.timezone = ZoneInfo(timezone)

    @staticmethod
    def _matches_course_name(course_name: str, text: str) -> bool:
        return bool(
            re.search(
                rf"(?<![0-9A-Za-zÀ-ÿ]){re.escape(course_name)}(?![0-9A-Za-zÀ-ÿ])",
                text,
            )
        )

    def __find_recordings_site(
        self, course_name: str, search_result: ClientResult[SearchResult]
    ):
        for row in search_result.value.PrimaryQueryResult.RelevantResults.Table.Rows:
            cells = row.Cells or {}
            title: str = cells.get("Title") or ""
            parent_link: str = cells.get("ParentLink") or ""
            site_name: str = cells.get("SiteName") or ""
            if (
                self._matches_course_name(course_name, title)
                and parent_link.lower().endswith(self.recordings_folder_name.lower())
                and site_name
            ):
                return site_name

    def get_name(self, f: Folder | File, fallback_name: str = ""):
        return (
            f.name
            or pathlib.PurePosixPath(f.server_relative_url or "").name
            or fallback_name
        )

    def get_last_modified(
        self, f: Folder | File, fallback_last_modified: int = 0
    ):
        last_modified = f.time_last_modified
        if not last_modified or last_modified == datetime.min:
            return fallback_last_modified
        try:
            return int(last_modified.timestamp())
        except OSError:
            return fallback_last_modified

    def __file_to_content(
        self, f: File, file_name: str = "", last_modified: int = 0
    ):
        return SharepointContent(
            Path(safe_path_segment(self.get_name(f, file_name))),
            self.get_last_modified(f, last_modified),
            f,
        )

    def can_handle(self, url: str):
        return (urlparse(url).hostname or "").lower() in {
            self.sharepoint_host,
            self.sharepoint_personal_host,
        }

    @staticmethod
    def _is_empty_entity(entity: Folder | File | None) -> bool:
        return not entity or (not entity.name and not entity.server_relative_url)

    async def list_url_content(
        self, url: str, filename: str, last_modified: int
    ) -> list[SharepointContent]:
        path = urlparse(url).path
        if re.search(r"^/:(\w+):/", path, re.IGNORECASE):
            if re.search(r"^/:f:/", path, re.IGNORECASE):
                folder = await self.sp_client.fetch_folder_by_guest_url(url)
                if self._is_empty_entity(folder):
                    return []
                return await self.fetch_folder_contents(folder)
            file = await self.sp_client.fetch_file_by_guest_url(url)
            if self._is_empty_entity(file):
                return []
            return [self.__file_to_content(file, filename, last_modified)]
        file = await self.fetch_file_from_direct_link(url)
        if not self._is_empty_entity(file):
            return [self.__file_to_content(file, filename, last_modified)]
        return []

    async def fetch_folder_contents(
        self, folder: Folder
    ) -> list[SharepointContent]:
        contents = list[SharepointContent]()
        for file in await self.sp_client.fetch_folder_files(folder):
            contents.append(self.__file_to_content(file))
        for sub_folder in await self.sp_client.fetch_subfolders(folder):
            if save_name := self.get_name(sub_folder):
                safe_sub = safe_path_segment(save_name)
                for item in await self.fetch_folder_contents(sub_folder):
                    contents.append(item.in_folder(safe_sub))
        return contents

    async def fetch_file_from_direct_link(self, link: str):
        query_params = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
        lower_query_keys = {k.lower(): v for k, v in query_params.items()}

        if "uniqueid" in lower_query_keys:
            file_id = lower_query_keys["uniqueid"][0].strip("{}")
        elif "sourcedoc" in lower_query_keys:
            file_id = lower_query_keys["sourcedoc"][0].strip("{}")
        else:
            return None

        return await self.sp_client.fetch_file_by_id(link, file_id)

    async def fetch_recordings(
        self, course_name: str
    ) -> list[RecordingContent]:
        search_result = await self.sp_client.search_for_recordings(course_name)
        recording_site = self.__find_recordings_site(course_name, search_result)
        recordings = list[RecordingContent]()
        if recording_site:
            items = await self.sp_client.fetch_document_library_items(
                recording_site
            )

            def is_course_recording(item: ListItem):
                decoded_path = urllib.parse.unquote(
                    urlparse(item.file.server_relative_url or "").path
                )
                return (
                    Path(decoded_path).parent.name.lower()
                    == self.recordings_folder_name.lower()
                    and self._matches_course_name(course_name, decoded_path)
                )

            for item in filter(is_course_recording, items):
                file = item.file
                created = file.time_created
                if (
                    not created
                    or created == datetime.min
                    or not file.server_relative_url
                ):
                    continue

                if created.tzinfo is not None:
                    created = created.astimezone(self.timezone)
                week = (
                    1
                    + (created.date() - self.semester_start).days
                    // 7
                )
                name = safe_path_segment(self.get_name(file))
                last_mod = self.get_last_modified(file)
                share_url = f"https://{self.sharepoint_personal_host}/:v:/r{quote(file.server_relative_url)}?csf=1&web=1"
                rec_path = (
                    Path(safe_path_segment(f"{self.week_folder_prefix} {week}"))
                    / name
                )
                recordings.append(
                    RecordingContent(rec_path, last_mod, share_url)
                )

        return recordings
