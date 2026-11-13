from datetime import date
import pathlib
from pathlib import Path
import re
import time
import urllib.parse
from urllib.parse import quote, urlparse

from office365.runtime.client_result import ClientResult
from office365.sharepoint.client_context import ClientContext
from office365.sharepoint.files.file import File
from office365.sharepoint.folders.folder import Folder
from office365.sharepoint.listitems.collection import ListItem
from office365.sharepoint.search.result import SearchResult

from idat_sync.domain.content import RecordingContent, SharepointContent
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
        request_delay: float,
    ) -> None:
        self.sp_client = client
        self.semester_start = semester_start
        self.sharepoint_host = sharepoint_host.lower()
        self.sharepoint_personal_host = sharepoint_personal_host.lower()
        self.recordings_folder_name = recordings_folder_name
        self.week_folder_prefix = week_folder_prefix
        self.request_delay = request_delay
        self.properties_to_retrieve = self.sp_client.properties_to_retrieve
        self.client = self.sp_client.client
        self.search_client = self.sp_client.search_client

    def __find_recordings_site(
        self, course_name: str, search_result: ClientResult[SearchResult]
    ):
        for row in search_result.value.PrimaryQueryResult.RelevantResults.Table.Rows:
            cells = row.Cells or {}
            title: str = cells.get("Title") or ""
            parent_link: str = cells.get("ParentLink") or ""
            site_name: str = cells.get("SiteName") or ""
            if (
                course_name in title
                and parent_link.lower().endswith(self.recordings_folder_name.lower())
                and site_name
            ):
                return site_name

    def get_name(self, f: Folder | File, fallback_name: str = ""):
        return (
            f.name
            or pathlib.PurePosixPath(f.serverRelativeUrl or "").name
            or fallback_name
        )

    def get_last_modified(
        self, f: Folder | File, fallback_last_modified: int = 0
    ):
        if last_modified := f.time_last_modified:
            return int(last_modified.timestamp())
        else:
            return fallback_last_modified

    __get_name = get_name
    __get_last_modified = get_last_modified

    def __file_to_content(
        self, f: File, file_name: str = "", last_modified: int = 0
    ):
        return SharepointContent(
            Path(self.__get_name(f, file_name)),
            self.__get_last_modified(f, last_modified),
            f,
        )

    def can_handle(self, url: str):
        return (urlparse(url).hostname or "").lower() == self.sharepoint_host

    def list_url_content(
        self, url: str, filename: str, last_modified: int
    ) -> list[SharepointContent]:
        if re.search(rf"https://{re.escape(self.sharepoint_host)}/:(\w+):", url):
            ctx: ClientContext = self.sp_client.get_client_from_url(url)
            if url.startswith(f"https://{self.sharepoint_host}/:f:"):
                folder: Folder = ctx.web.get_folder_by_guest_url(url)
                self.sp_client.load_entity(folder, ctx)
                return self.fetch_folder_contents(folder)
            else:
                file = ctx.web.get_file_by_guest_url(url)
                self.sp_client.load_entity(file, ctx)
                return [self.__file_to_content(file, filename, last_modified)]
        else:
            if file := self.fetch_file_from_direct_link(url):
                return [self.__file_to_content(file, filename, last_modified)]
        return []

    def fetch_folder_contents(self, folder: Folder) -> list[SharepointContent]:
        contents = list[SharepointContent]()
        files = (
            folder.get_files()
            .select(self.properties_to_retrieve)
            .execute_query_retry()
        )
        for file in files:
            contents.append(self.__file_to_content(file))
        time.sleep(self.request_delay)
        folders = folder.get_folders().execute_query_retry()
        time.sleep(self.request_delay)
        for sub_folder in folders:
            if save_name := self.__get_name(sub_folder):
                for item in self.fetch_folder_contents(sub_folder):
                    contents.append(item.in_folder(save_name))
        return contents

    def fetch_file_from_direct_link(self, link: str):
        ctx = self.sp_client.get_client_from_url(link)
        file_id = None
        query_params = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
        lower_query_keys = {k.lower(): v for k, v in query_params.items()}

        if "uniqueid" in lower_query_keys:
            file_id = lower_query_keys["uniqueid"][0].strip("{}")
        elif "sourcedoc" in lower_query_keys:
            file_id = lower_query_keys["sourcedoc"][0].strip("{}")
        else:
            return

        file = ctx.web.get_file_by_id(file_id)
        self.sp_client.load_entity(file, ctx)
        return file

    def fetch_recordings(self, course_name: str) -> list[RecordingContent]:
        search_result = self.sp_client.search_for_recordings(course_name)
        recording_site = self.__find_recordings_site(course_name, search_result)
        recordings = list[RecordingContent]()
        if recording_site:
            items = self.sp_client.fetch_document_library_items(recording_site)

            def is_course_recording(item: ListItem):
                relative_url = item.file.serverRelativeUrl or ""
                return (
                    Path(urlparse(relative_url).path).parent.name.lower()
                    == self.recordings_folder_name.lower()
                    and course_name in relative_url
                )

            for item in filter(is_course_recording, items):
                file = item.file
                if not file.time_created or not file.serverRelativeUrl:
                    continue

                week = (
                    1
                    + (file.time_created.date() - self.semester_start).days
                    // 7
                )
                name = self.get_name(file)
                last_mod = self.get_last_modified(file)
                share_url = f"https://{self.sharepoint_personal_host}/:v:/r{quote(file.serverRelativeUrl)}?csf=1&web=1"
                rec_path = Path(f"{self.week_folder_prefix} {week}") / name
                recordings.append(
                    RecordingContent(rec_path, last_mod, share_url)
                )

        return recordings
