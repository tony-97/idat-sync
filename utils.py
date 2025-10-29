import re
import sys
import unicodedata
import time
import requests
from pathlib import Path

from playwright._impl._api_structures import StorageState
from playwright.sync_api import sync_playwright

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


def get_sharepoint_cookies():
    site_url = "https://idat628.sharepoint.com/_layouts/15/sharepoint.aspx"

    with sync_playwright() as p:
        timeout = 60000 * 3
        browser = p.chromium.launch(headless=False, channel="msedge")
        context = browser.new_context()
        page = context.new_page()
        page.goto(site_url)
        # Wait for network to be idle; login flow may redirect to Microsoft login pages
        page.wait_for_load_state("networkidle", timeout=timeout)
        page.wait_for_url(
            "https://login.microsoftonline.com/**",
            timeout=timeout,
        )
        page.wait_for_load_state("networkidle", timeout=timeout)
        page.wait_for_url(
            "https://idat628.sharepoint.com/_layouts/15/sharepoint.aspx",
            timeout=timeout,
        )
        page.wait_for_load_state("networkidle", timeout=timeout)
        page.goto(
            "https://idat628-my.sharepoint.com/shared",
        )
        page.wait_for_load_state("networkidle", timeout=timeout)
        # Persist cookies and related state
        storage_state = context.storage_state()

        context.close()
        browser.close()

        return storage_state


def netscape_cookies_format(storage_state: StorageState):
    lines = []
    lines.append("# Netscape HTTP Cookie File")

    for c in storage_state.get("cookies", []):
        to_bool_str = lambda value: "TRUE" if value else "FALSE"
        # Required fields with sane defaults
        name = c.get("name", "")
        value = c.get("value", "")
        domain = c.get("domain", "")
        path = c.get("path", "/")
        secure = bool(c.get("secure", False))
        expires = c.get("expires", 0)  # Playwright uses Unix seconds, 0/-1 for session
        try:
            expires_int = int(expires if expires and expires > 0 else 0)
        except Exception:
            expires_int = 0

        if not domain or not name:
            # Skip malformed cookies
            continue

        # include_subdomains flag:
        # TRUE if domain starts with a dot (host-only cookies typically won’t)
        include_subdomains = domain.startswith(".")

        # Netscape format columns:
        # domain, include_subdomains(TRUE|FALSE), path, secure(TRUE|FALSE),
        # expiration (Unix epoch), name, value
        line = "\t".join(
            [
                domain,
                to_bool_str(include_subdomains),
                path,
                to_bool_str(secure),
                str(expires_int),
                name,
                value,
            ]
        )
        lines.append(line)
    return "\n".join(lines)


def load_cookies_from_storage_state(
    storage_state: StorageState, domain: str, subdomain: str = ""
):
    cookies = {}
    for cookie in storage_state.get("cookies", []):
        name = cookie.get("name")
        cookie_domain = cookie.get("domain", "")
        if (name in {"FedAuth", "rtFa", "SPOIDCRL"}) and (
            domain == cookie_domain or (subdomain in cookie_domain)
        ):
            cookies[name] = cookie.get("value", "")
    return cookies


def get_token(user: str, password: str):
    response = requests.get(
        f"{MOODLE_IDAT}/login/token.php",
        params={
            "username": user,
            "password": password,
            "service": "moodle_mobile_app",
        },
        timeout=60,
        headers=RQ_HEADER,
    )
    response.raise_for_status()
    login = response.json()
    if "error" in login:
        print(f"[!] Token error: {login.get('error')}")
        sys.exit(1)
    token = login.get("token")
    if not token:
        print(
            "[!] Could not obtain token. Check URL/credentials or if mobile service is enabled."
        )
        sys.exit(1)
    return token


def call_ws(token, function: str, **kwargs):
    url = f"{MOODLE_IDAT}/webservice/rest/server.php"
    payload = {
        "wstoken": token,
        "wsfunction": function,
        "moodlewsrestformat": "json",
        **kwargs,
    }
    response = requests.post(url, data=payload, timeout=45)
    response.raise_for_status()
    j = response.json()
    if isinstance(j, dict) and j.get("exception"):
        raise RuntimeError(f"{j.get('errorcode')}: {j.get('message')}")
    time.sleep(REQUEST_DELAY)
    return j


def print_courses(courses):
    print("\nYour Moodle courses:")
    for i, c in enumerate(courses, 1):
        fullname = c.get("fullname") or c.get("shortname")
        print(f"{i:>2}. [{c.get('id')}] {fullname}")


def choose_course(courses):
    while True:
        s = input("\nSelect course number: ").strip()
        if not s.isdigit():
            print("Enter a number from the list.")
            continue
        idx = int(s)
        if 1 <= idx <= len(courses):
            return courses[idx - 1]
        print("Out of range.")


def get_course_contents(token, course_id):
    return call_ws(
        token,
        "core_course_get_contents",
        courseid=f"{course_id}",
    )
