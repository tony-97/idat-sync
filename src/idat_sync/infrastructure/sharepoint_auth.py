import asyncio
import platform
from typing import Awaitable, Callable, cast

from playwright.async_api import async_playwright

from idat_sync.application.ports import StorageStateData


class SharepointBrowserAuth:
    """Playwright SSO/MFA authenticator implementing SharepointAuthPort."""

    def __init__(
        self,
        sharepoint_host: str,
        sharepoint_personal_host: str,
        email_domain: str,
    ) -> None:
        self.sharepoint_host = sharepoint_host
        self.sharepoint_personal_host = sharepoint_personal_host
        self.email_domain = email_domain

    async def fetch_storage_state(
        self,
        username: str,
        password: str,
        get_mfa_code: Callable[[], Awaitable[str]],
    ) -> StorageStateData | None:
        site_url = f"https://{self.sharepoint_host}/_layouts/15/sharepoint.aspx"
        channel = "chromium"
        if (os_name := platform.system()) == "Windows":
            channel = "msedge"
        elif os_name == "Darwin":
            channel = "webkit"

        login_email = (
            username if "@" in username else f"{username}@{self.email_domain}"
        )
        cancelled = False
        storage_state: StorageStateData | None = None
        try:
            async with async_playwright() as p:
                browser = None
                context = None
                try:
                    browser = await p.chromium.launch(headless=True, channel=channel)
                    context = await browser.new_context(
                        **p.devices["Galaxy A55 landscape"]
                    )
                    timeout = 60000 * 3
                    page = await context.new_page()
                    await page.goto(site_url)
                    await page.wait_for_load_state("networkidle", timeout=timeout)
                    await page.wait_for_url(
                        "https://login.microsoftonline.com/**",
                        timeout=timeout,
                    )
                    await page.wait_for_load_state("networkidle", timeout=timeout)
                    await page.fill("#i0116", login_email, force=True)
                    await page.click("#idSIButton9")

                    await page.fill("#i0118", password)
                    await page.click("#idSIButton9")
                    await page.wait_for_url(
                        "https://login.microsoftonline.com/*/login",
                        timeout=timeout,
                    )
                    await page.click("#idDiv_SAOTCS_Proofs > div:nth-child(1)")
                    mfa_code = await get_mfa_code()
                    await page.fill("#idTxtBx_SAOTCC_OTC", mfa_code, force=True)
                    await page.click("#idChkBx_SAOTCC_TD")
                    await page.click("#idSubmit_SAOTCC_Continue")

                    await page.wait_for_url(
                        "https://login.microsoftonline.com/common/SAS/ProcessAuth",
                        timeout=timeout,
                    )
                    await page.click("#KmsiCheckboxField")
                    await page.click("#idSIButton9")
                    await page.wait_for_url(
                        site_url,
                        timeout=timeout,
                    )
                    await page.wait_for_load_state("load", timeout=timeout)
                    await asyncio.sleep(3)
                    await page.goto(
                        f"https://{self.sharepoint_personal_host}/shared",
                    )
                    await page.wait_for_load_state("load", timeout=timeout)
                    await asyncio.sleep(3)
                    storage_state = cast(
                        StorageStateData, await context.storage_state()
                    )
                except asyncio.CancelledError:
                    cancelled = True
                    raise
                finally:
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
        except Exception:
            storage_state = None
        return storage_state
