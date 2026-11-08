import re
import time
import urllib.parse

from office365.runtime.client_result import ClientResult
from office365.sharepoint.client_context import ClientContext
from office365.sharepoint.files.file import File
from office365.sharepoint.folders.folder import Folder
from office365.sharepoint.listitems.collection import ListItem
from office365.sharepoint.search.result import SearchResult
from playwright._impl._api_structures import StorageStateCookie

from idat_sync.utils import REQUEST_DELAY


class SharepointClient:
    """Low-level client wrapping the Office365 SharePoint REST SDK."""

    def __init__(self, cookies: list[StorageStateCookie]) -> None:
        self.properties_to_retrieve = [
            "Name",
            "ServerRelativeUrl",
            "TimeLastModified",
        ]
        self.sharepoint_cookies = self._load_auth_cookies(
            cookies, "idat628-my.sharepoint.com", ".sharepoint.com"
        )
        self.onedrive_cookies = self._load_auth_cookies(
            cookies, "idat628-my.sharepoint.com", ".sharepoint.com"
        )
        site_url = "https://idat628.sharepoint.com/sites/MATERIALESTED-ACADEMICOIDAT"
        search_site_url = "https://idat628.sharepoint.com"
        self.client = self.get_client(site_url)
        self.search_client = self.get_client(search_site_url)

    def get_client(self, site_url: str) -> ClientContext:
        return ClientContext(site_url).with_cookies(
            lambda: self.sharepoint_cookies
        )

    def get_client_from_url(self, url: str) -> ClientContext:
        parsed_url = urllib.parse.urlparse(url)
        site_url = None
        path_match = re.search(
            r"/(sites|teams|s)/([^/]+)", parsed_url.path, re.IGNORECASE
        )
        if path_match:
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            if path_match.group(1) == "s":
                site_url = f"{base_url}/sites/{path_match.group(2)}"
            else:
                site_url = f"{base_url}{path_match.group(0)}"

        if site_url:
            return self.get_client(site_url)
        return self.client

    def _load_auth_cookies(
        self,
        cookies: list[StorageStateCookie],
        domain: str,
        subdomain: str = "",
    ) -> dict[str, str]:
        auth_cookies: dict[str, str] = {}
        for cookie in cookies:
            name = cookie.get("name")
            cookie_domain = cookie.get("domain", "")
            if (name in {"FedAuth", "rtFa", "SPOIDCRL"}) and (
                domain == cookie_domain or (subdomain in cookie_domain)
            ):
                auth_cookies[name] = cookie.get("value", "")
        return auth_cookies

    def load_entity(self, entity: File | Folder, ctx: ClientContext) -> None:
        ctx.load(entity, self.properties_to_retrieve)
        ctx.execute_query_with_incremental_retry()
        time.sleep(REQUEST_DELAY)

    def search_for_recordings(
        self, course_name: str
    ) -> ClientResult[SearchResult]:
        result = self.search_client.search.query(
            course_name,
            row_limit=15,
            source_id="8413cd39-2156-4e00-b54d-11efd9abdb89",
        ).execute_query_retry()
        time.sleep(REQUEST_DELAY)
        return result

    def fetch_document_library_items(self, site_url: str) -> list[ListItem]:
        client = self.get_client(site_url)
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
        return list(items)
