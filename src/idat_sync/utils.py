import re
import asyncio
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

REQUEST_DELAY = 15

WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(s: str, replacement: str = " ", max_len: int = 100) -> str:
    # Normalize (so accents become plain letters when possible)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    # Replace forbidden characters (Windows + Unix)
    s = re.sub(r'[<>:"/\\|?*\x00-\x1F]', replacement, s)
    # Collapse whitespace and separators
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[ .]+$", "", s)  # no trailing dot/space (Windows)
    s = re.sub(rf"{re.escape(replacement)}+", replacement, s)
    # Default name if empty
    if not s:
        s = "untitled"
    # Avoid reserved device names (Windows)
    base = s
    if base.upper() in WINDOWS_RESERVED:
        s = f"_{base}"
    # Enforce length (typical FS limit is 255 bytes; keep margin)
    s = s[:max_len].strip()
    return s


def save_content(path: str, content: str):
    content = content.strip()
    if not Path(path).exists() and content:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


# TODO maybe raise timeout exception when navigation fails
# TODO handle bad username or password
async def get_sharepoint_cookies(
    username: str, password: str, login_flow_ended: asyncio.Event
):
    site_url = "https://idat628.sharepoint.com/_layouts/15/sharepoint.aspx"
    storage_state = None
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel="msedge")
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
            await page.wait_for_url(
                "https://login.microsoftonline.com/common/SAS/ProcessAuth",
                timeout=timeout,
            )
            await page.click("#KmsiCheckboxField")  # Keep Signed in
            await page.click("#idSIButton9")  # Click Sign in
            login_flow_ended.set()
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
