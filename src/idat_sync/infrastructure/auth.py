import json
import asyncio
from pathlib import Path
import platform
from typing import Awaitable, Callable, cast

from playwright.async_api import async_playwright

from idat_sync.application.ports import Credentials, StorageStateData


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
                finally:
                    if context is not None:
                        await context.close()
                    if browser is not None:
                        await browser.close()
        except Exception:
            storage_state = None
        return storage_state


class JsonCredentialStore:
    """JSON file store for persisted user Credentials."""

    def __init__(self, session_file: str | Path) -> None:
        self.session_file = Path(session_file)

    def load(self) -> Credentials | None:
        try:
            with open(self.session_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

        if not isinstance(data, dict):
            return None
        token = data.get("token")
        storage_state = data.get("sharepoint_storage_state")
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(storage_state, dict)
        ):
            return None
        return cast(Credentials, data)

    def save(self, credentials: Credentials) -> bool:
        try:
            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.session_file, "w", encoding="utf-8") as f:
                json.dump(credentials, f)
            return True
        except OSError as e:
            print(f"Error: Could not create credentials file: {e}")
            return False

    def clear(self) -> None:
        try:
            if self.session_file.exists():
                self.session_file.unlink()
                print("Success: User has been logged out.")
            else:
                print("Info: Already logged out (no credentials file found).")
        except OSError as e:
            print(f"Error: Could not remove credentials file: {e}")
