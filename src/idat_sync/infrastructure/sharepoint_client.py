"""Low-level SharePoint REST SDK client with cookie injection and rate limiting."""

import contextlib
import re
import urllib.parse
from collections.abc import Awaitable, Callable, Coroutine
from functools import partial
from typing import TYPE_CHECKING, BinaryIO, ClassVar, Self, TypeVar

import httpx
from office365.runtime.client_request_exception import ClientRequestException
from office365.runtime.client_result import ClientResult
from office365.runtime.http.http_method import HttpMethod
from office365.runtime.http.request_options import RequestOptions
from office365.runtime.transport.httpx_transport import HttpxTransport
from office365.sharepoint.client_context import ClientContext
from office365.sharepoint.files.file import File
from office365.sharepoint.folders.folder import Folder
from office365.sharepoint.listitems.collection import ListItem
from office365.sharepoint.request import SharePointRequest
from office365.sharepoint.search.result import SearchResult

from idat_sync.application.ports import (
    CookieData,
    CredentialsExpiredError,
    ProgressCallback,
    RemoteServiceError,
)
from idat_sync.infrastructure.rate_limiter import AsyncRateLimiter

if TYPE_CHECKING:
    from office365.sharepoint.webs.context_web_information import (
        ContextWebInformation,
    )

_EntityT = TypeVar("_EntityT", File, Folder)
_ResultT = TypeVar("_ResultT")


class SharepointClient:
    """Low-level client wrapping the Office365 SharePoint REST SDK."""

    properties_to_retrieve: ClassVar[list[str]] = [
        "Name",
        "ServerRelativeUrl",
        "TimeLastModified",
    ]

    def __init__(
        self,
        cookies: list[CookieData],
        sharepoint_personal_host: str,
        academic_site_url: str,
        rate_limiter: AsyncRateLimiter,
    ) -> None:
        """Initialize the SharePoint client with cookies, hosts, and limiter."""
        self.rate_limiter = rate_limiter
        self._cookies = list(cookies)
        self._transport = HttpxTransport()
        self._web_infos: dict[str, ContextWebInformation] = {}
        self._academic_site_url = academic_site_url.rstrip("/")
        personal_sharepoint_url = f"https://{sharepoint_personal_host}"
        self.personal_client = self.get_client(personal_sharepoint_url)
        self.search_client: ClientContext | None = None

    @classmethod
    async def create(
        cls,
        cookies: list[CookieData],
        sharepoint_personal_host: str,
        academic_site_url: str,
        rate_limiter: AsyncRateLimiter,
    ) -> Self:
        """Create and initialize a SharepointClient, resolving the search context."""
        client = cls(
            cookies=cookies,
            sharepoint_personal_host=sharepoint_personal_host,
            academic_site_url=academic_site_url,
            rate_limiter=rate_limiter,
        )
        try:
            personal_site = await client._await_query(
                client.personal_client.web.current_user.get_personal_site().execute_query_async()
            )
            if personal_site.url:
                client.search_client = client.get_client(personal_site.url)
        except BaseException:
            await client.aclose()
            raise
        if not personal_site.url:
            await client.aclose()
            raise CredentialsExpiredError("SharePoint login expired")
        return client

    async def aclose(self) -> None:
        """Close the underlying async HTTPX transport."""
        await self._transport.aclose()

    async def __aenter__(self) -> Self:
        """Enter the async context manager."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit the async context manager and close the transport."""
        await self.aclose()

    def get_client(self, site_url: str) -> ClientContext:
        """Create an authenticated SharePoint ClientContext for ``site_url``."""
        target_host = urllib.parse.urlparse(site_url).hostname or ""
        ctx = (
            ClientContext(site_url)
            .with_cookies(lambda host=target_host: self.cookies_for_host(host))
            .with_auto_retry()
        )
        pending = ctx.pending_request()
        pending.with_async_transport(self._transport)
        pending.beforeExecute -= pending.ensure_form_digest
        pending.beforeExecute += partial(self._ensure_form_digest, pending, site_url)
        return ctx

    def _ensure_form_digest(
        self,
        pending: SharePointRequest,
        site_url: str,
        request: RequestOptions,
    ) -> None:
        if request.method == HttpMethod.Get:
            return
        web_info = self._web_infos.get(site_url)
        if web_info is None or not web_info.is_valid:
            pending.ensure_form_digest(request)
            web_info = pending.context_info
            self._web_infos[site_url] = web_info
        request.set_header("X-RequestDigest", web_info.FormDigestValue)

    def get_client_from_url(self, url: str) -> ClientContext:
        """Resolve the site ClientContext corresponding to ``url``."""
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
        return self.get_client(self._academic_site_url)

    def cookies_for_host(self, host: str) -> dict[str, str]:
        """Return matching SharePoint auth cookies for ``host``."""
        target_host = host.lower()
        if not target_host:
            return {}
        wildcard_cookies: dict[str, str] = {}
        exact_cookies: dict[str, str] = {}
        for cookie in self._cookies:
            name = cookie.get("name")
            if name not in {"FedAuth", "rtFa", "SPOIDCRL"}:
                continue
            cookie_domain = cookie.get("domain", "").lower()
            if cookie_domain == target_host:
                exact_cookies[name] = cookie.get("value", "")
            elif cookie_domain.startswith(".") and (
                target_host == cookie_domain.lstrip(".")
                or target_host.endswith(cookie_domain)
            ):
                wildcard_cookies[name] = cookie.get("value", "")
        return {**wildcard_cookies, **exact_cookies}

    async def _await_query(self, coro: Awaitable[_ResultT]) -> _ResultT:
        entered = False
        try:
            async with self.rate_limiter:
                entered = True
                return await coro
        except ClientRequestException as exc:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            if status_code in (401, 403):
                raise CredentialsExpiredError(
                    f"HTTP {status_code}: SharePoint credentials expired."
                ) from exc
            raise RemoteServiceError(f"SharePoint error: {exc}") from exc
        except httpx.HTTPError as exc:
            raise RemoteServiceError(f"SharePoint request failed: {exc}") from exc
        except BaseException:
            if not entered and isinstance(coro, Coroutine):
                coro.close()
            raise

    async def load_entity(self, entity: File | Folder, ctx: ClientContext) -> None:
        """Load standard metadata properties onto a SharePoint File or Folder."""
        entity.select(self.properties_to_retrieve)

        async def _exec() -> None:
            # odata.null guest URL responses leave entity.resource_url as None
            with contextlib.suppress(TypeError):
                await ctx.execute_query_with_incremental_retry_async()

        await self._await_query(_exec())

    async def search_for_recordings(
        self, course_name: str
    ) -> ClientResult[SearchResult]:
        """Search personal SharePoint for recordings matching ``course_name``."""
        if self.search_client is None:
            raise CredentialsExpiredError("SharePoint login expired")
        return await self._await_query(
            self.search_client.search.query(
                course_name,
                row_limit=15,
            ).execute_query_async()
        )

    async def fetch_document_library_items(self, site_url: str) -> list[ListItem]:
        """Fetch all items from the default document library of ``site_url``."""
        client = self.get_client(site_url)
        doc_lib = client.web.default_document_library()
        items = await self._await_query(
            doc_lib.items.select(
                [
                    "File/Name",
                    "File/ServerRelativeUrl",
                    "File/TimeCreated",
                    "File/TimeLastModified",
                ]
            )
            .expand(["File"])
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
        """Resolve and load a SharePoint Folder from a guest sharing URL."""
        return await self._load_entity_at_url(
            url,
            lambda ctx: ctx.web.get_folder_by_guest_url_extended(
                url, ensure_access=True
            ),
        )

    async def fetch_file_by_guest_url(self, url: str) -> File:
        """Resolve and load a SharePoint File from a guest sharing URL."""
        return await self._load_entity_at_url(
            url,
            lambda ctx: ctx.web.get_file_by_guest_url_extended(url, ensure_access=True),
        )

    async def fetch_file_by_id(self, url: str, file_id: str) -> File:
        """Resolve and load a SharePoint File by its unique ID."""

        def _resolve(ctx: ClientContext) -> File:
            file = ctx.web.get_file_by_id(file_id)
            ctx.load(file, self.properties_to_retrieve)
            return file

        return await self._load_entity_at_url(url, _resolve)

    async def fetch_folder_files(self, folder: Folder) -> list[File]:
        """Return all File children of ``folder``."""
        files = await self._await_query(
            folder.get_files().select(self.properties_to_retrieve).execute_query_async()
        )
        return list(files)

    async def fetch_subfolders(self, folder: Folder) -> list[Folder]:
        """Return all Folder children of ``folder``."""
        folders = await self._await_query(folder.get_folders().execute_query_async())
        return list(folders)

    async def download_file(
        self,
        file: File,
        dest: BinaryIO,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        """Stream ``file`` contents into ``dest``."""
        file.set_property("Id", file.unique_id, persist_changes=False)
        progress = (
            (lambda snap: on_progress(snap.done, snap.total))
            if on_progress is not None
            else None
        )
        await self._await_query(
            file.download_session_async(
                dest,
                chunk_size=16 * 1024,
                use_path=False,
                progress=progress,
            )
        )
