from office365.runtime.client_result import ClientResult
from office365.sharepoint.search.result import SearchResult
from office365.sharepoint.client_context import ClientContext
import time

from idat_sync.utils import REQUEST_DELAY

from playwright._impl._api_structures import StorageStateCookie


class SharepointClient:
    def __init__(self, cookies: list[StorageStateCookie]) -> None:
        self.sharepoint_cookies = self.load_auth_cookies(
            cookies, "idat628-my.sharepoint.com", ".sharepoint.com"
        )
        self.onedrive_cookies = self.load_auth_cookies(
            cookies, "idat628-my.sharepoint.com", ".sharepoint.com"
        )
        site_url = "https://idat628.sharepoint.com/sites/MATERIALESTED-ACADEMICOIDAT"
        self.client = ClientContext(site_url).with_cookies(
            lambda: self.sharepoint_cookies
        )
        search_site_url = "https://idat628.sharepoint.com"
        self.search_client = ClientContext(search_site_url).with_cookies(
            lambda: self.sharepoint_cookies
        )

    def load_auth_cookies(
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

    def search_recordings(self, course_name: str):
        result = self.search_client.search.query(
            course_name,
            row_limit=15,
            source_id="8413cd39-2156-4e00-b54d-11efd9abdb89",
        ).execute_query_retry()
        time.sleep(REQUEST_DELAY)
        return result

    def find_recordings_site(
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

    def get_recordings(self, recording_site: str):
        client = ClientContext(recording_site).with_cookies(
            lambda: self.onedrive_cookies
        )
        doc_lib = client.web.default_document_library()
        time.sleep(REQUEST_DELAY)
        items = (
            doc_lib.items.select(["FileSystemObjectType"])
            .expand(["File"])
            .get_all(page_size=50)
            .execute_query_retry()
        )
        time.sleep(REQUEST_DELAY)
        return items

    def find_course_created_at(
        self, course_name: str, search_result: ClientResult[SearchResult]
    ):
        for row in search_result.value.PrimaryQueryResult.RelevantResults.Table.Rows:
            cells = row.Cells or {}
            title: str = cells.get("Title") or ""
            site_name: str = cells.get("SiteName") or ""
            is_container: bool = cells.get("IsContainer") or False
            if (course_name in title) and site_name and is_container:
                client = ClientContext(site_name).with_cookies(
                    lambda: self.sharepoint_cookies
                )
                created = (
                    client.web.get().execute_query_with_incremental_retry().created
                )
                time.sleep(REQUEST_DELAY)
                return created
