import re
import asyncio
import platform
from typing import Awaitable, Callable
import unicodedata
from pathlib import Path


from playwright.async_api import async_playwright

MOODLE_IDAT = "https://aulavirtual.idat.edu.pe"
RQ_HEADER = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 7.1.1; Moto G Play Build/NPIS26.48-43-2; wv) AppleWebKit/537.36"
        + " (KHTML, like Gecko) Version/4.0 Chrome/71.0.3578.99 Mobile Safari/537.36 MoodleMobile"
    ),
    "Content-Type": "application/x-www-form-urlencoded",
}

REQUEST_DELAY = 30

from idat_sync.domain.path_utils import WINDOWS_RESERVED, safe_filename


def save_content(path: str, content: str):
    content = content.strip()
    if not Path(path).exists() and content:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


# TODO maybe raise timeout exception when navigation fails
# TODO handle bad username or password
async def get_sharepoint_cookies(
    username: str,
    password: str,
    login_flow_ended: asyncio.Event,
    get_mfa_code: Callable[[], Awaitable[str]],
):
    site_url = "https://idat628.sharepoint.com/_layouts/15/sharepoint.aspx"
    channel = "chromium"
    if (os := platform.system()) == "Windows":
        channel = "msedge"
    elif os == "Darwin":
        channel = "webkit"

    storage_state = None
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel=channel)
        context = await browser.new_context(**p.devices["Galaxy A55 landscape"])
        try:
            timeout = 60000 * 3
            page = await context.new_page()
            await page.goto(site_url)
            # Wait for network to be idle; login flow may redirect to Microsoft login pages
            await page.wait_for_load_state("networkidle", timeout=timeout)
            await page.wait_for_url(
                "https://login.microsoftonline.com/**",
                timeout=timeout,
            )
            await page.wait_for_load_state("networkidle", timeout=timeout)
            # --- Fill login form ---
            # Fill username
            await page.fill("#i0116", f"{username}@idat.pe", force=True)
            await page.click("#idSIButton9")  # Click Next

            # Fill password
            await page.fill("#i0118", password)
            await page.click("#idSIButton9")  # Click Sign in
            # Wait MFA
            await page.wait_for_url(
                "https://login.microsoftonline.com/*/login",
                timeout=timeout,
            )
            await page.click("#idDiv_SAOTCS_Proofs > div:nth-child(1)")
            mfa_code = await get_mfa_code()
            await page.fill("#idTxtBx_SAOTCC_OTC", mfa_code, force=True)
            await page.click("#idChkBx_SAOTCC_TD")  # Do not ask for 30 days
            await page.click("#idSubmit_SAOTCC_Continue")  # Click verify
            # MFA ended

            login_flow_ended.set()
            await page.wait_for_url(
                "https://login.microsoftonline.com/common/SAS/ProcessAuth",
                timeout=timeout,
            )
            await page.click("#KmsiCheckboxField")  # Keep Signed in
            await page.click("#idSIButton9")  # Click Sign in
            # Wait for redirection back to SharePoint after successful login
            await page.wait_for_url(
                "https://idat628.sharepoint.com/_layouts/15/sharepoint.aspx",
                timeout=timeout,
            )
            await page.wait_for_load_state("load", timeout=timeout)
            await asyncio.sleep(3)
            await page.goto(
                "https://idat628-my.sharepoint.com/shared",
            )
            await page.wait_for_load_state("load", timeout=timeout)
            await asyncio.sleep(3)
            # Persist cookies and related state
            storage_state = await context.storage_state()

        except:
            login_flow_ended.set()
        finally:
            await context.close()
            await browser.close()
        return storage_state
