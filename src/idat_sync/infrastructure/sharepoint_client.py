import asyncio
from collections.abc import Awaitable, Callable
import re
from typing import BinaryIO, Self, TypeVar
import urllib.parse

from office365.runtime.client_request_exception import ClientRequestException
from office365.runtime.client_result import ClientResult
from office365.runtime.transport.httpx_transport import HttpxTransport
from office365.sharepoint.client_context import ClientContext
from office365.sharepoint.files.file import File
from office365.sharepoint.folders.folder import Folder
from office365.sharepoint.listitems.collection import ListItem
from office365.sharepoint.search.result import SearchResult

from idat_sync.application.ports import CookieData, CredentialsExpiredError

_EntityT = TypeVar("_EntityT", File, Folder)
_ResultT = TypeVar("_ResultT")


class SharepointClient:
    """Low-level client wrapping the Office365 SharePoint REST SDK."""

    def __init__(
        self,
        cookies: list[CookieData],
        sharepoint_personal_host: str,
        academic_site_url: str,
        request_delay: float,
    ) -> None:
        self.request_delay = request_delay
        self.properties_to_retrieve = [
            "Name",
            "ServerRelativeUrl",
            "TimeLastModified",
        ]
        host_parts = sharepoint_personal_host.split(".", 1)
        subdomain = f".{host_parts[1]}" if len(host_parts) > 1 else ""
        self.sharepoint_cookies = self._load_auth_cookies(
            cookies, sharepoint_personal_host, subdomain
        )
        self._transport = HttpxTransport()
        self.client = self.get_client(academic_site_url.rstrip("/"))
        personal_sharepoint_url = f"https://{sharepoint_personal_host}"
        self.personal_client = self.get_client(personal_sharepoint_url)
        self.search_client: ClientContext | None = None

    @classmethod
    async def create(
        cls,
        cookies: list[CookieData],
        sharepoint_personal_host: str,
        academic_site_url: str,
        request_delay: float,
    ) -> Self:
        client = cls(
            cookies=cookies,
            sharepoint_personal_host=sharepoint_personal_host,
            academic_site_url=academic_site_url,
            request_delay=request_delay,
        )
        try:
            personal_site = await client._await_query(
                client.personal_client.web.current_user.get_personal_site().execute_query_async()
            )
            if not personal_site.url:
                raise CredentialsExpiredError("SharePoint login expired")
            client.search_client = client.get_client(personal_site.url)
            return client
        except Exception:
            await client.aclose()
            raise

    async def aclose(self) -> None:
        await self._transport.aclose()

    def get_client(self, site_url: str) -> ClientContext:
        ctx = (
            ClientContext(site_url)
            .with_cookies(lambda: self.sharepoint_cookies)
            .with_auto_retry()
        )
        ctx.pending_request().with_async_transport(self._transport)
        return ctx

    def get_client_from_url(self, url: str) -> ClientContext:
        parsed_url = urllib.parse.urlparse(url)
        site_url = None
        path_match = re.search(
            r"/(sites|teams|s|personal)/([^/]+)", parsed_url.path, re.IGNORECASE
        )
        if path_match:
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            if path_match.group(1).lower() == "s":
                site_url = f"{base_url}/sites/{path_match.group(2)}"
            else:
                site_url = f"{base_url}{path_match.group(0)}"

        if site_url:
            return self.get_client(site_url)
        return self.client

    def _load_auth_cookies(
        self,
        cookies: list[CookieData],
        domain: str,
        subdomain: str = "",
    ) -> dict[str, str]:
        auth_cookies: dict[str, str] = {}
        for cookie in cookies:
            name = cookie.get("name")
            cookie_domain = cookie.get("domain", "")
            if (name in {"FedAuth", "rtFa", "SPOIDCRL"}) and (
                domain == cookie_domain or (bool(subdomain) and subdomain in cookie_domain)
            ):
                auth_cookies[name] = cookie.get("value", "")
        return auth_cookies

    async def _await_query(self, coro: Awaitable[_ResultT]) -> _ResultT:
        try:
            result = await coro
        except ClientRequestException as exc:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            if status_code in (401, 403):
                raise CredentialsExpiredError(
                    f"HTTP {status_code}: SharePoint credentials expired."
                ) from exc
            raise
        await asyncio.sleep(self.request_delay)
        return result

    async def load_entity(self, entity: File | Folder, ctx: ClientContext) -> None:
        ctx.load(entity, self.properties_to_retrieve)
        try:
            await self._await_query(
                ctx.execute_query_with_incremental_retry_async()
            )
        except TypeError:
            # odata.null guest URL responses leave entity.resource_url as None
            await asyncio.sleep(self.request_delay)

    async def search_for_recordings(
        self, course_name: str
    ) -> ClientResult[SearchResult]:
        if self.search_client is None:
            raise CredentialsExpiredError("SharePoint login expired")
        return await self._await_query(
            self.search_client.search.query(
                course_name,
                row_limit=15,
            ).execute_query_async()
        )

    async def fetch_document_library_items(self, site_url: str) -> list[ListItem]:
        client = self.get_client(site_url)
        doc_lib = client.web.default_document_library()
        items = await self._await_query(
            doc_lib.items.select(["FileSystemObjectType"])
            .expand(["File"])
            .select(self.properties_to_retrieve)
            .get_all_async(page_size=50)
        )
        return list(items)

    async def _load_entity_at_url(
        self, url: str, resolver: Callable[[ClientContext], _EntityT]
    ) -> _EntityT:
        ctx = self.get_client_from_url(url)
        entity = resolver(ctx)
        await self.load_entity(entity, ctx)
        return entity

    async def fetch_folder_by_guest_url(self, url: str) -> Folder:
        return await self._load_entity_at_url(
            url, lambda ctx: ctx.web.get_folder_by_guest_url(url)
        )

    async def fetch_file_by_guest_url(self, url: str) -> File:
        return await self._load_entity_at_url(
            url, lambda ctx: ctx.web.get_file_by_guest_url(url)
        )

    async def fetch_file_by_id(self, url: str, file_id: str) -> File:
        return await self._load_entity_at_url(
            url, lambda ctx: ctx.web.get_file_by_id(file_id)
        )

    async def fetch_folder_files(self, folder: Folder) -> list[File]:
        files = await self._await_query(
            folder.get_files()
            .select(self.properties_to_retrieve)
            .execute_query_async()
        )
        return list(files)

    async def fetch_subfolders(self, folder: Folder) -> list[Folder]:
        folders = await self._await_query(
            folder.get_folders().execute_query_async()
        )
        return list(folders)

    async def download_file(self, file: File, dest: BinaryIO) -> None:
        await self._await_query(
            file.download_session_async(dest, use_path=False)
        )
