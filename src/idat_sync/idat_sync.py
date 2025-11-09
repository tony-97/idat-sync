import os
import io
import re
import time
import requests
from datetime import date

from office365.runtime.client_result import ClientResult
from office365.sharepoint.search.result import SearchResult

from idat_sync.utils import (
    load_cookies_from_storage_state,
    netscape_cookies_format,
    safe_filename,
    save_content,
    call_ws,
    get_course_contents,
    REQUEST_DELAY,
    RQ_HEADER,
)

from idat_sync.auth import Credentials

from pathlib import Path
from collections import defaultdict
from urllib.parse import urlparse
from urllib.parse import quote

from office365.sharepoint.client_context import ClientContext
from office365.sharepoint.files.file import File, Folder
from office365.sharepoint.listitems.collection import ListItem

import yt_dlp


class IDATSync:
    def __init__(self, credentials: Credentials) -> None:
        self.token = credentials.get("token")
        sharepoint_storage_state = credentials.get("sharepoint_storage_state")

        self.sharepoint_cookies = load_cookies_from_storage_state(
            sharepoint_storage_state, "idat628-my.sharepoint.com", ".sharepoint.com"
        )
        self.onedrive_cookies = load_cookies_from_storage_state(
            sharepoint_storage_state, "idat628-my.sharepoint.com", ".sharepoint.com"
        )
        self.netscape_cookies_format = netscape_cookies_format(sharepoint_storage_state)
        site_url = "https://idat628.sharepoint.com/sites/MATERIALESTED-ACADEMICOIDAT"
        self.client = ClientContext(site_url).with_cookies(
            lambda: self.sharepoint_cookies
        )
        search_site_url = "https://idat628.sharepoint.com"
        self.search_client = ClientContext(search_site_url).with_cookies(
            lambda: self.sharepoint_cookies
        )

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

    def download_sharepoint_file(self, file: File, download_path: str, name: str):
        file_name = file.name or name
        file_path = os.path.join(download_path, file_name)
        if Path(file_path).exists():
            return
        with open(file_path, "wb") as local_file:
            file.download(local_file).execute_query_with_incremental_retry()
            time.sleep(REQUEST_DELAY)

    def download_sharepoint_folder(self, folder: Folder, download_path: str):
        get_name = lambda f: f.name or safe_filename(folder.serverRelativeUrl or "")
        files = folder.files.get_all().execute_query_retry()
        time.sleep(REQUEST_DELAY)
        folders = folder.folders.get_all().execute_query_retry()
        time.sleep(REQUEST_DELAY)
        for file in files:
            self.download_sharepoint_file(file, download_path, get_name(file))
        for folder in folders:
            if save_name := get_name(folder):
                sub_folder_path = os.path.join(download_path, save_name)
                if Path(sub_folder_path).exists:
                    continue
                else:
                    os.makedirs(sub_folder_path, exist_ok=True)
                    self.download_sharepoint_folder(folder, sub_folder_path)

    def download_sharepoint_link(
        self,
        share_url: str,
        name: str,
        download_path: str,
    ):
        print(f"downloading share url1: {share_url}")

        if share_url.startswith("https://idat628.sharepoint.com/:f:"):
            print(f"Skipping url: {share_url}")
            folder = self.client.web.get_folder_by_guest_url(
                share_url
            ).execute_query_with_incremental_retry()
            time.sleep(REQUEST_DELAY)
            folder_path = os.path.join(download_path, name)
            self.download_sharepoint_folder(folder, folder_path)
        else:
            print(f"downloading share url2: {share_url}")
            file = self.client.web.get_file_by_guest_url(share_url)
            self.client.load(file, ["Name"])
            self.client.execute_query_with_incremental_retry()
            time.sleep(REQUEST_DELAY)
            self.download_sharepoint_file(file, download_path, name)

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
                    print(f"downloading file url: {file_url}")
                    print(f"downloading file name: {file_name}")
                    self.download_sharepoint_link(file_url, file_name, folder)

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

    def download_recording(self, url: str, folder: str):
        print(
            f"downloading share url: {url}",
        )
        output_path_template = os.path.join(
            f"\\\\?\\{os.path.abspath(folder)}", "%(title)s %(id)s.%(ext)s"
        )
        ydl_opts = {
            "cookiefile": io.StringIO(self.netscape_cookies_format),
            "format_sort": ["proto:dash"],
            "postprocessors": [{"key": "FFmpegMetadata"}],
            "format": "bestvideo+bestaudio/bestvideo",
            "outtmpl": output_path_template,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
            ydl.download([url])

    def download_recordings(self, course_name: str, recordings_folder: str):
        search_recordings = self.search_recordings(course_name)
        recording_site = self.find_recordings_site(course_name, search_recordings)
        if recording_site:
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

            def is_recording(item: ListItem):
                return (
                    Path(urlparse(item.file.serverRelativeUrl).path).parent.name.lower()
                    == "grabaciones"
                )

            recordings = [item.file for item in filter(is_recording, items)]
            dates = [file.time_created.date() for file in recordings]
            start = date(2025, month=9, day=22)
            # Compute week index: 1 + floor(days_since_start / 7)
            week_index = [1 + (d - start).days // 7 for d in dates]
            # Group
            groups = defaultdict(list)
            for file, week in zip(recordings, week_index):
                groups[week].append(file)
            for week, files in groups.items():
                week_path = os.path.join(
                    os.path.abspath(recordings_folder), f"Semana {week}"
                )
                os.makedirs(week_path, exist_ok=True)
                for file in files:
                    share_url = f"https://idat628-my.sharepoint.com/:v:/r{quote(file.serverRelativeUrl)}?csf=1&web=1"
                    self.download_recording(share_url, week_path)

    def sync_course(self, contents, assignments, course_name: str, course_path: str):
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

    def sync_courses(self, root_folder: str):
        courses = self.list_my_courses()
        assignments_courses = call_ws(self.token, "mod_assign_get_assignments").get(
            "courses", []
        )
        for course in courses:
            if (cid := course.get("id")) and (course_name := course.get("fullname")):
                print(
                    f"\nFetching contents for: {course.get('fullname') or course.get('shortname')} (id={cid}) ..."
                )
                contents = get_course_contents(self.token, cid)
                if isinstance(contents, dict) and contents.get("exception"):
                    print("[!] Error:", contents)
                    return
                assignments = (
                    next(
                        (
                            course_assignment
                            for course_assignment in assignments_courses
                            if course_assignment.get("id") == cid
                        ),
                        {},
                    )
                ).get("assignments", [])
                course_folder = os.path.join(root_folder, safe_filename(course_name))
                self.sync_course(contents, assignments, course_name, course_folder)

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
