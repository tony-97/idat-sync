from pathlib import Path
import pathlib
import re
from urllib.parse import urlparse
import urllib.parse

from office365.runtime.client_result import ClientResult
from office365.sharepoint.folders.folder import Folder
from office365.sharepoint.search.result import SearchResult
from office365.sharepoint.client_context import ClientContext
from office365.sharepoint.listitems.collection import ListItem
from office365.sharepoint.files.file import File

import time

import urllib

from idat_sync.services.downloaders import SharepointContent
from idat_sync.utils import REQUEST_DELAY

from playwright._impl._api_structures import StorageStateCookie


class SharepointApiAdapter:
    def __init__(self, cookies: list[StorageStateCookie]) -> None:
        self.properties_to_retrieve = ["Name", "ServerRelativeUrl", "TimeLastModified"]
        self.sharepoint_cookies = self.__load_auth_cookies(
            cookies, "idat628-my.sharepoint.com", ".sharepoint.com"
        )
        self.onedrive_cookies = self.__load_auth_cookies(
            cookies, "idat628-my.sharepoint.com", ".sharepoint.com"
        )
        site_url = "https://idat628.sharepoint.com/sites/MATERIALESTED-ACADEMICOIDAT"
        search_site_url = "https://idat628.sharepoint.com"
        self.client = self.__get_client(site_url)
        self.search_client = self.__get_client(search_site_url)

    def __get_client(self, site_url: str):
        return ClientContext(site_url).with_cookies(lambda: self.sharepoint_cookies)

    def __get_sharepoint_client_from_url(self, url: str):
        parsed_url = urllib.parse.urlparse(url)
        # 1. Extract Site URL
        # Matches patterns like /sites/YourSite or /teams/YourTeam
        site_url = None
        path_match = re.search(
            r"/(sites|teams|s)/([^/]+)", parsed_url.path, re.IGNORECASE
        )
        if path_match:
            # Rebuild the URL: https://tenant.sharepoint.com + /sites/SiteName
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            if path_match.group(1) == "s":
                site_url = f"{base_url}/sites/{path_match.group(2)}"
            else:
                site_url = f"{base_url}{path_match.group(0)}"

        if site_url:
            return self.__get_client(site_url)
        else:
            return self.client

    def __load_auth_cookies(
        self, cookies: list[StorageStateCookie], domain: str, subdomain: str = ""
    ):
        auth_cookies = {}
        for cookie in cookies:
            name = cookie.get("name")
            cookie_domain = cookie.get("domain", "")
            if (name in {"FedAuth", "rtFa", "SPOIDCRL"}) and (
                domain == cookie_domain or (subdomain in cookie_domain)
            ):
                auth_cookies[name] = cookie.get("value", "")
        return auth_cookies

    def __search_for_recordings(self, course_name: str):
        result = self.search_client.search.query(
            course_name,
            row_limit=15,
            source_id="8413cd39-2156-4e00-b54d-11efd9abdb89",
        ).execute_query_retry()
        time.sleep(REQUEST_DELAY)
        return result

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
                and parent_link.lower().endswith("grabaciones")
                and site_name
            ):
                return site_name

    def __get_name(self, f: Folder | File, fallback_name: str = ""):
        return (
            f.name
            or pathlib.PurePosixPath(f.serverRelativeUrl or "").name
            or fallback_name
        )

    def __get_last_modified(self, f: Folder | File, fallback_last_modified: int = 0):
        if last_modified := f.time_last_modified:
            return int(last_modified.timestamp())
        else:
            return fallback_last_modified

    def __load_entity(self, f: File | Folder, ctx: ClientContext):
        ctx.load(f, self.properties_to_retrieve)
        ctx.execute_query_with_incremental_retry()
        time.sleep(REQUEST_DELAY)

    def __file_to_content(
        self, f: File, parents: list[str], file_name: str = "", last_modified: int = 0
    ):
        return SharepointContent(
            parents,
            self.__get_name(f, file_name),
            self.__get_last_modified(f, last_modified),
            f,
        )

    def can_handle(self, url: str):
        return "idat628.sharepoint" in urlparse(url).netloc

    def list_url_content(
        self, url: str, parents: list[str], filename: str, last_modified: int
    ):
        url_contents = list[SharepointContent]()
        if re.search(r"https://idat628\.sharepoint\.com/:(\w+):", url):
            ctx: ClientContext = self.__get_sharepoint_client_from_url(url)
            if url.startswith("https://idat628.sharepoint.com/:f:"):
                folder: Folder = ctx.web.get_folder_by_guest_url(url)
                self.__load_entity(folder, ctx)
                folder_path = [*parents, self.__get_name(folder, filename)]
                self.fetch_folder_contents(folder, url_contents, folder_path)
            else:
                file = ctx.web.get_file_by_guest_url(url)
                self.__load_entity(file, ctx)
                url_contents.append(
                    self.__file_to_content(file, parents, filename, last_modified)
                )
        else:
            if file := self.fetch_file_from_direct_link(url):
                url_contents.append(
                    self.__file_to_content(file, parents, filename, last_modified)
                )
        return url_contents

    def fetch_folder_contents(
        self, folder: Folder, all_contents: list[SharepointContent], parents: list[str]
    ):
        files = (
            folder.get_files().select(self.properties_to_retrieve).execute_query_retry()
        )
        for file in files:
            all_contents.append(self.__file_to_content(file, parents))
        time.sleep(REQUEST_DELAY)
        folders = folder.get_folders().execute_query_retry()
        time.sleep(REQUEST_DELAY)
        for folder in folders:
            if save_name := self.__get_name(folder):
                sub_folder_path = [*parents, save_name]
                self.fetch_folder_contents(folder, all_contents, sub_folder_path)

    def fetch_file_from_direct_link(self, link: str):
        ctx = self.__get_sharepoint_client_from_url(link)
        # 2. Extract File ID (UniqueId)
        file_id = None
        query_params = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)

        # Lowercase the keys just to be safe against varying URL cases
        lower_query_keys = {k.lower(): v for k, v in query_params.items()}

        # Check for common ID parameters
        if "uniqueid" in lower_query_keys:
            file_id = lower_query_keys["uniqueid"][0].strip("{}")
        elif "sourcedoc" in lower_query_keys:  # Another common SP parameter for IDs
            file_id = lower_query_keys["sourcedoc"][0].strip("{}")
        else:
            return

        # 2. Download by Unique ID
        # Use get_file_by_id instead of get_file_by_server_relative_url
        file = ctx.web.get_file_by_id(file_id)
        self.__load_entity(file, ctx)
        return file

    def fetch_recordings(self, course_name: str) -> list[File]:
        search_result = self.__search_for_recordings(course_name)
        recording_site = self.__find_recordings_site(course_name, search_result)
        onedrive_recordings = list[File]()
        if recording_site:
            client = self.__get_client(recording_site)
            doc_lib = client.web.default_document_library()
            time.sleep(REQUEST_DELAY)
            items = (
                doc_lib.items.select(["FileSystemObjectType"])
                .expand(["File"])
                .select(self.properties_to_retrieve)
                .get_all(page_size=50)
                .execute_query_retry()
            )
            time.sleep(REQUEST_DELAY)

            def is_course_recording(item: ListItem):
                relative_url = item.file.serverRelativeUrl or ""
                return (
                    Path(urlparse(relative_url).path).parent.name.lower()
                    == "grabaciones"
                    and course_name in relative_url
                )

            onedrive_recordings = [
                item.file for item in filter(is_course_recording, items)
            ]
        return onedrive_recordings
