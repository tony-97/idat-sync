"""Anti-corruption adapter mapping SharePoint SDK responses to domain models."""

import pathlib
import re
import urllib.parse
from datetime import date, datetime
from pathlib import Path
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
    """Adapter exposing SharePoint resources and recordings as domain Content."""

    def __init__(  # noqa: PLR0913, PLR0917  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        client: SharepointClient,
        semester_start: date,
        sharepoint_host: str,
        sharepoint_personal_host: str,
        recordings_folder_name: str,
        week_folder_prefix: str,
        timezone: str,
    ) -> None:
        """Initialize the SharePoint adapter with client and semester settings."""
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
    ) -> str | None:
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
        return None

    def get_name(self, f: Folder | File, fallback_name: str = "") -> str:
        """Return the resolved filename for a SharePoint Folder or File."""
        return (
            f.name
            or pathlib.PurePosixPath(f.server_relative_url or "").name
            or fallback_name
        )

    def get_last_modified(
        self, f: Folder | File, fallback_last_modified: int = 0
    ) -> int:
        """Return the Unix timestamp for ``f``, or ``fallback_last_modified``."""
        last_modified = f.time_last_modified
        if not last_modified or last_modified == datetime.min:  # noqa: DTZ901
            return fallback_last_modified
        try:
            return int(last_modified.timestamp())
        except OSError:
            return fallback_last_modified

    def __file_to_content(
        self, f: File, file_name: str = "", last_modified: int = 0
    ) -> SharepointContent:
        return SharepointContent(
            Path(safe_path_segment(self.get_name(f, file_name))),
            self.get_last_modified(f, last_modified),
            f,
        )

    def can_handle(self, url: str) -> bool:
        """Return whether ``url`` targets a configured SharePoint host."""
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
        """Resolve a SharePoint sharing or direct URL into SharepointContent items."""
        path = urlparse(url).path
        result: list[SharepointContent] = []
        if re.search(r"^/:(\w+):/", path, re.IGNORECASE):
            if re.search(r"^/:f:/", path, re.IGNORECASE):
                folder = await self.sp_client.fetch_folder_by_guest_url(url)
                if not self._is_empty_entity(folder):
                    result = await self.fetch_folder_contents(folder)
            else:
                file = await self.sp_client.fetch_file_by_guest_url(url)
                if not self._is_empty_entity(file):
                    result = [self.__file_to_content(file, filename, last_modified)]
        else:
            file = await self.fetch_file_from_direct_link(url)
            if not self._is_empty_entity(file):
                result = [self.__file_to_content(file, filename, last_modified)]
        return result

    async def fetch_folder_contents(self, folder: Folder) -> list[SharepointContent]:
        """Recursively collect SharepointContent items under ``folder``."""
        contents = list[SharepointContent]()
        for file in await self.sp_client.fetch_folder_files(folder):
            contents.append(self.__file_to_content(file))
        for sub_folder in await self.sp_client.fetch_subfolders(folder):
            if save_name := self.get_name(sub_folder):
                safe_sub = safe_path_segment(save_name)
                for item in await self.fetch_folder_contents(sub_folder):
                    contents.append(item.in_folder(safe_sub))
        return contents

    async def fetch_file_from_direct_link(self, link: str) -> File | None:
        """Fetch a SharePoint File referenced by uniqueid or sourcedoc query param."""
        query_params = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
        lower_query_keys = {k.lower(): v for k, v in query_params.items()}

        if "uniqueid" in lower_query_keys:
            file_id = lower_query_keys["uniqueid"][0].strip("{}")
        elif "sourcedoc" in lower_query_keys:
            file_id = lower_query_keys["sourcedoc"][0].strip("{}")
        else:
            return None

        return await self.sp_client.fetch_file_by_id(link, file_id)

    def _to_recording_content(self, file: File) -> RecordingContent | None:
        created = file.time_created
        if (
            not created
            or created == datetime.min  # noqa: DTZ901
            or not file.server_relative_url
        ):
            return None

        if created.tzinfo is not None:
            created = created.astimezone(self.timezone)
        week = 1 + (created.date() - self.semester_start).days // 7
        name = safe_path_segment(self.get_name(file))
        last_mod = self.get_last_modified(file)
        share_url = (
            f"https://{self.sharepoint_personal_host}/:v:/r"
            f"{quote(file.server_relative_url)}?csf=1&web=1"
        )
        rec_path = Path(safe_path_segment(f"{self.week_folder_prefix} {week}")) / name
        return RecordingContent(rec_path, last_mod, share_url)

    async def fetch_recordings(self, course_name: str) -> list[RecordingContent]:
        """Fetch RecordingContent items for ``course_name`` organized by week."""
        search_result = await self.sp_client.search_for_recordings(course_name)
        recording_site = self.__find_recordings_site(course_name, search_result)
        if not recording_site:
            return []

        items = await self.sp_client.fetch_document_library_items(recording_site)

        def is_course_recording(item: ListItem) -> bool:
            decoded_path = urllib.parse.unquote(item.file.server_relative_url or "")
            return (
                pathlib.PurePosixPath(decoded_path).parent.name.lower()
                == self.recordings_folder_name.lower()
                and self._matches_course_name(course_name, decoded_path)
            )

        return [
            recording
            for item in filter(is_course_recording, items)
            if (recording := self._to_recording_content(item.file)) is not None
        ]
