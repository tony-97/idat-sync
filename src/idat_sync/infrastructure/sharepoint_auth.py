"""Playwright-based interactive SharePoint/Microsoft SSO authentication."""

from __future__ import annotations

import asyncio
import logging
import platform
from typing import TYPE_CHECKING, cast

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from idat_sync.application.ports import AuthenticationError, StorageStateData

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_logger = logging.getLogger(__name__)
_TIMEOUT_MS = 180_000


async def _launch_browser(playwright: Playwright) -> Browser:
    os_name = platform.system()
    if os_name == "Darwin":
        return await playwright.webkit.launch(headless=False)
    channel = "msedge" if os_name == "Windows" else "chromium"
    return await playwright.chromium.launch(headless=False, channel=channel)


async def _close_resources(
    context: BrowserContext | None,
    browser: Browser | None,
    *,
    cancelled: bool,
) -> None:
    cleanup_error: Exception | None = None
    try:
        if context is not None:
            await context.close()
    except Exception as exc:
        cleanup_error = exc
    finally:
        try:
            if browser is not None:
                await browser.close()
        except Exception as exc:
            cleanup_error = cleanup_error or exc
    if cleanup_error is not None and not cancelled:
        raise cleanup_error


class SharepointBrowserAuth:  # pylint: disable=too-few-public-methods
    """Playwright SSO/MFA authenticator implementing SharepointAuthPort."""

    def __init__(
        self,
        sharepoint_host: str,
        sharepoint_personal_host: str,
        email_domain: str,
    ) -> None:
        """Initialize the authenticator with SharePoint hosts and email domain."""
        self.sharepoint_host = sharepoint_host
        self.sharepoint_personal_host = sharepoint_personal_host
        self.email_domain = email_domain

    async def _complete_login(
        self,
        page: Page,
        site_url: str,
        login_email: str,
        password: str,
        get_mfa_code: Callable[[], Awaitable[str]],
    ) -> None:
        await page.goto(site_url)
        await page.wait_for_load_state("networkidle", timeout=_TIMEOUT_MS)
        await page.wait_for_url(
            "https://login.microsoftonline.com/**",
            timeout=_TIMEOUT_MS,
        )
        await page.wait_for_load_state("networkidle", timeout=_TIMEOUT_MS)
        await page.fill("#i0116", login_email, force=True)
        await page.click("#idSIButton9")

        await page.fill("#i0118", password)
        await page.click("#idSIButton9")
        await page.wait_for_url(
            "https://login.microsoftonline.com/*/login",
            timeout=_TIMEOUT_MS,
        )
        await page.click("#idDiv_SAOTCS_Proofs > div:nth-child(2)")
        mfa_code = (await get_mfa_code()).strip()
        if not mfa_code:
            raise AuthenticationError("MFA code is required")
        await page.fill("#idTxtBx_SAOTCC_OTC", mfa_code, force=True)
        await page.click("#idChkBx_SAOTCC_TD")
        await page.click("#idSubmit_SAOTCC_Continue")

        await page.wait_for_url(
            "https://login.microsoftonline.com/common/SAS/ProcessAuth",
            timeout=_TIMEOUT_MS,
        )
        await page.wait_for_selector(
            "#skipMfaRegistrationLink, #KmsiCheckboxField",
            timeout=_TIMEOUT_MS,
        )
        if await page.is_visible("#skipMfaRegistrationLink"):
            await page.click("#skipMfaRegistrationLink")
        await page.click("#KmsiCheckboxField")
        await page.click("#idSIButton9")
        await page.wait_for_url(site_url, timeout=_TIMEOUT_MS)
        await page.wait_for_load_state("load", timeout=_TIMEOUT_MS)
        await asyncio.sleep(3)
        await page.goto(f"https://{self.sharepoint_personal_host}/shared")
        await page.wait_for_load_state("load", timeout=_TIMEOUT_MS)
        await asyncio.sleep(3)

    async def fetch_storage_state(
        self,
        username: str,
        password: str,
        get_mfa_code: Callable[[], Awaitable[str]],
    ) -> StorageStateData:
        """Authenticate interactively via browser/MFA and return storage state."""
        site_url = f"https://{self.sharepoint_host}/_layouts/15/sharepoint.aspx"
        login_email = username if "@" in username else f"{username}@{self.email_domain}"
        cancelled = False
        try:
            async with async_playwright() as p:
                browser: Browser | None = None
                context: BrowserContext | None = None
                try:
                    browser = await _launch_browser(p)
                    context = await browser.new_context(
                        **p.devices["Galaxy A55 landscape"]
                    )
                    page = await context.new_page()
                    await self._complete_login(
                        page, site_url, login_email, password, get_mfa_code
                    )
                    return cast("StorageStateData", await context.storage_state())
                except asyncio.CancelledError:
                    cancelled = True
                    raise
                finally:
                    await _close_resources(context, browser, cancelled=cancelled)
        except AuthenticationError:
            raise
        except PlaywrightTimeoutError as exc:
            _logger.exception("SharePoint sign-in timed out")
            raise AuthenticationError("SharePoint sign-in timed out.") from exc
        except Exception as exc:
            _logger.exception("SharePoint browser authentication failed")
            raise AuthenticationError(
                "SharePoint browser authentication failed"
            ) from exc
