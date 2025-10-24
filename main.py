import re
import os
import io
import sys
import time
import requests
import unicodedata
from pathlib import Path
from collections import defaultdict
from urllib.parse import urlparse
from urllib.parse import quote

from playwright._impl._api_structures import StorageState
from playwright.sync_api import sync_playwright

from office365.sharepoint.client_context import ClientContext

import yt_dlp
import json

MOODLE_IDAT = "https://aulavirtual.idat.edu.pe"
RQ_HEADER = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 7.1.1; Moto G Play Build/NPIS26.48-43-2; wv) AppleWebKit/537.36"
        + " (KHTML, like Gecko) Version/4.0 Chrome/71.0.3578.99 Mobile Safari/537.36 MoodleMobile"
    ),
    "Content-Type": "application/x-www-form-urlencoded",
}
ROOT_FOLDER = "C:\\Users\\User\\Desktop\\src\\idat-sync\\sync_folder"
cookies_path = "./storage_state.json"

REQUEST_DELAY = 15

WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

SHARE_URL = re.compile(r"^(https://idat628\.sharepoint\.com/)(:\w:)(?=/)")


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
        storage_state = context.storage_state(path=cookies_path)

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

        if not domain or not name or not value:
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


def get_token():
    response = requests.get(
        f"{MOODLE_IDAT}/login/token.php",
        params={
            "username": "iv71430260",
            "password": "-TS7dp^$9G>BR82",
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


class IDATSync:
    def __init__(self) -> None:
        if os.path.exists(cookies_path):
            with open(cookies_path, "r", encoding="utf-8") as f:
                sharepoint_storage_state = json.load(f)
        else:
            sharepoint_storage_state = get_sharepoint_cookies()
        self.sharepoint_cookies = load_cookies_from_storage_state(
            sharepoint_storage_state, "idat628.sharepoint.com", ".sharepoint.com"
        )
        self.onedrive_cookies = load_cookies_from_storage_state(
            sharepoint_storage_state, "idat628-my.sharepoint.com", ".sharepoint.com"
        )
        self.netscape_cookies_format = netscape_cookies_format(sharepoint_storage_state)
        site_url = "https://idat628.sharepoint.com/sites/MATERIALESTED-ACADEMICOIDAT"
        self.client = ClientContext(site_url).with_cookies(
            lambda: self.sharepoint_cookies
        )
        time.sleep(REQUEST_DELAY)
        search_site_url = "https://idat628.sharepoint.com"
        self.search_client = ClientContext(search_site_url).with_cookies(
            lambda: self.sharepoint_cookies
        )
        self.token = get_token()

    def find_recordings_site(self, course_name: str):
        result = self.search_client.search.query(
            course_name,
            row_limit=5,
            source_id="8413cd39-2156-4e00-b54d-11efd9abdb89",
        ).execute_query_retry()
        time.sleep(REQUEST_DELAY)
        for row in result.value.PrimaryQueryResult.RelevantResults.Table.Rows:
            title: str = (row.Cells or {}).get("Title", "") or ""
            parent_link: str = (row.Cells or {}).get("ParentLink", "") or ""
            site_name: str = (row.Cells or {}).get("SiteName", "") or ""
            if (
                title.startswith(course_name)
                and parent_link.lower().endswith("grabaciones")
                and site_name
            ):
                return site_name

    def download_moodle_link(self, file_url: str, file_path: str):
        with requests.get(
            file_url,
            params={"wstoken": self.token, "token": self.token},
            stream=True,
            allow_redirects=True,
            timeout=60,
            headers=RQ_HEADER,
        ) as r:
            if r.status_code == 403:
                raise RuntimeError(
                    "403 Forbidden: your session cookie is missing/expired."
                )
            r.raise_for_status()
            with open(file_path, "wb") as f:
                for part in r.iter_content(chunk_size=1024 * 256):
                    if part:
                        f.write(part)
            time.sleep(REQUEST_DELAY)

    def download_sharepoint_link(
        self,
        share_url: str,
        name: str,
        download_path: str,
    ):
        share_url = re.sub(
            r"^(https://idat628\.sharepoint\.com/):\w:(?=/)", r"\1:b:", share_url
        )
        file = self.client.web.get_file_by_guest_url(share_url)
        self.client.load(file, ["Name"])
        self.client.execute_query()
        time.sleep(1)
        file_name = file.name or name

        with open(os.path.join(download_path, file_name), "wb") as local_file:
            file.download(local_file).execute_query()
        time.sleep(0.5)

    def download_contents(self, contents, folder: str):
        for content in contents:
            if (file_url := content.get("fileurl")) and (
                file_name := content.get("filename")
            ):
                if (file_path := Path(os.path.join(folder, file_name))).exists():
                    continue
                netloc = urlparse(file_url).netloc

                if "aulavirtual" in netloc:
                    self.download_moodle_link(file_url, str(file_path))
                elif "idat628.sharepoint" in netloc:
                    self.download_sharepoint_link(file_url, file_name, folder)

    def download_recordings(self, course_name: str, recordings_folder: str):
        recording_site = self.find_recordings_site(course_name)
        if recording_site:
            client = ClientContext(recording_site).with_cookies(
                lambda: self.onedrive_cookies
            )
            doc_lib = client.web.default_document_library()
            time.sleep(REQUEST_DELAY)
            items = (
                doc_lib.items.select(["FileSystemObjectType"])
                .expand(["File", "Folder"])
                .get_all()
                .execute_query_retry()
            )
            time.sleep(REQUEST_DELAY)
            dates = [item.file.time_created.date() for item in items]
            start = min(dates)
            # Compute week index: 1 + floor(days_since_start / 7)
            week_index = [1 + (d - start).days // 7 for d in dates]
            # Group
            groups = defaultdict(list)
            for item, week in zip(items, week_index):
                groups[week].append(item.file)
            for week, files in groups.items():
                week_path = os.path.join(recordings_folder, f"Semana {week}")
                os.makedirs(week_path, exist_ok=True)
                output_path_template = os.path.join(week_path, "%(title)s.%(ext)s")
                ydl_opts = {
                    "cookiefile": io.StringIO(self.netscape_cookies_format),
                    "outtmpl": output_path_template,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
                    for file in files:
                        share_url = f"https://idat628-my.sharepoint.com/:v:/r{quote(file.serverRelativeUrl)}?csf=1&web=1"
                        print(f"downloading share url: {share_url}")
                        ydl.download([share_url])

    def sync_courses(self, contents, assignments, course_name: str, course_path: str):
        recordings_folder = os.path.join(course_path, "Grabaciones")
        os.makedirs(course_path, exist_ok=True)
        os.makedirs(recordings_folder, exist_ok=True)
        self.download_recordings(course_name, recordings_folder)
        for content in contents:
            if content_name := safe_filename(content.get("name", "")):
                content_path = os.path.join(course_path, content_name)
                Path(content_path).mkdir(parents=True, exist_ok=True)
                summary_name = f"{content_name}-[{content.get("id", "")}]-resumen.html"
                summary_path = os.path.join(content_path, summary_name)
                save_content(summary_path, content.get("summary", ""))
                for module in content.get("modules", []):
                    if module_name := safe_filename(module.get("name", "")):
                        module_path: str = os.path.join(content_path, module_name)
                        modname = module.get("modname")
                        module_contents = module.get("contents", [])
                        if modname == "label" and (
                            description := module.get("description", ""),
                        ):
                            save_content(f"{module_path}.html", description)
                        elif modname == "url" and (len(module_contents) == 1):
                            self.download_contents(module_contents, content_path)
                        elif modname == "assign":
                            Path(module_path).mkdir(parents=True, exist_ok=True)
                            cmid = module.get("id", None)
                            assignment = next(
                                (
                                    assign
                                    for assign in assignments
                                    if assign.get("cmid", None) == cmid
                                ),
                                None,
                            )
                            if assignment:
                                assignment_intro_name = f"{assignment.get("name", "")}-[{assignment.get("id", "")}]-intro.html"
                                assignment_intro_path = os.path.join(
                                    module_path, assignment_intro_name
                                )
                                save_content(
                                    assignment_intro_path, assignment.get("intro", "")
                                )
                                self.download_contents(
                                    assignment.get("introattachments", []),
                                    module_path,
                                )
                        else:
                            Path(module_path).mkdir(parents=True, exist_ok=True)
                            self.download_contents(module_contents, module_path)

    def list_my_courses(self):
        try:
            return call_ws(
                self.token,
                "core_course_get_enrolled_courses_by_timeline_classification",
                classification="all",
                limit=100,
                offset=0,
            ).get("courses", [])
        except Exception as e:
            print("[!] Could not list courses via core_enrol_get_users_courses.")
            raise e


def main():
    print("== Moodle Course Contents Viewer ==")
    idat_sync = IDATSync()
    courses = idat_sync.list_my_courses()
    if not courses:
        print("No courses found for this user.")
        return
    print_courses(courses)
    for chosen in courses:
        if (cid := chosen.get("id")) and (course_name := chosen.get("fullname")):
            print(
                f"\nFetching contents for: {chosen.get('fullname') or chosen.get('shortname')} (id={cid}) ..."
            )
            contents = get_course_contents(idat_sync.token, cid)
            if isinstance(contents, dict) and contents.get("exception"):
                print("[!] Error:", contents)
                return
            assignments_courses = call_ws(
                idat_sync.token, "mod_assign_get_assignments"
            ).get("courses", [])
            assignments = (
                next(
                    (
                        course
                        for course in assignments_courses
                        if course.get("id") == cid
                    ),
                    None,
                )
                or {}
            ).get("assignments", [])
            course_folder = os.path.join(ROOT_FOLDER, safe_filename(course_name))

            idat_sync.sync_courses(contents, assignments, course_name, course_folder)


if __name__ == "__main__":
    main()
    # try:
    #    main()
    # except KeyboardInterrupt:
    #    print("\nAborted by user.")
    # except Exception as e:
    #    print(f"\n[!] Error: {e}")
    #    sys.exit(1)
