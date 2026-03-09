import os
from pathlib import Path
from datetime import date
from collections import defaultdict
from urllib.parse import quote, urlparse

from office365.sharepoint.listitems.collection import ListItem

from idat_sync.auth import Credentials
from idat_sync.download_manager import DownloadManager
from idat_sync.services.moodle_client import MoodleClient
from idat_sync.services.sharepoint_client import SharepointClient
from idat_sync.services.downloaders import (
    MoodleDownloader,
    SharepointDownloader,
    MemoryDownloader,
    RecordingDownloader,
    FileDownloadInfoBase,
)
from idat_sync.utils import (
    save_content,
    safe_filename,
)


class IDATSync:
    def __init__(self, credentials: Credentials) -> None:
        self.moodle_client = MoodleClient(credentials.get("token"))
        sharepoint_storage_state = credentials.get("sharepoint_storage_state")
        cookies = sharepoint_storage_state.get("cookies", [])
        self.sharepoint_client = SharepointClient(cookies)
        self.download_manager = DownloadManager(
            [
                MoodleDownloader(self.moodle_client),
                SharepointDownloader(self.sharepoint_client),
                RecordingDownloader(cookies),
            ]
        )
        self.memory_downloader = MemoryDownloader()

    def download_recordings(self, course_name: str, recordings_folder: str):
        search_recordings_result = self.sharepoint_client.search_recordings(course_name)
        recording_site = self.sharepoint_client.find_recordings_site(
            course_name, search_recordings_result
        )
        if recording_site:
            items = self.sharepoint_client.get_recordings(recording_site)

            def is_course_recording(item: ListItem):
                relative_url = item.file.serverRelativeUrl or ""
                return (
                    Path(urlparse(relative_url).path).parent.name.lower()
                    == "grabaciones"
                    and course_name in relative_url
                )

            recordings = [item.file for item in filter(is_course_recording, items)]
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

    def build_remote_tree(
        self, contents, assignments: dict[int, dict], course_path: str
    ):
        to_download = list[FileDownloadInfoBase]()
        for content in contents:
            if content_name := safe_filename(content.get("name", "")):
                content_path = os.path.join(course_path, content_name)
                summary_name = f"{content_name}-[{content.get("id", "")}]-resumen.html"
                summary_path = os.path.join(content_path, summary_name)
                save_content(summary_path, content.get("summary", ""))
                for module in content.get("modules", []):
                    if module_name := safe_filename(module.get("name", "")):
                        module_path: str = os.path.join(content_path, module_name)
                        modname = module.get("modname")
                        module_contents = module.get("contents", [])
                        download_path = module_path
                        download_content = module_contents
                        if modname == "label" and (
                            description := module.get("description", ""),
                        ):
                            save_content(f"{module_path}.html", description)
                            continue
                        elif modname == "url" and (len(module_contents) == 1):
                            download_path = content_path
                        elif modname == "assign":
                            cmid = module.get("id", None)
                            assignment = assignments.get(cmid)
                            if assignment:
                                assignment_intro_name = f"{assignment.get("name", "")}-[{assignment.get("id", "")}]-intro.html"
                                assignment_intro_path = os.path.join(
                                    module_path, assignment_intro_name
                                )
                                save_content(
                                    assignment_intro_path, assignment.get("intro", "")
                                )
                                download_content = assignment.get(
                                    "introattachments", []
                                )
                        to_download += self.download_manager.list_files(
                            download_path, download_content
                        )
        return to_download

    def sync_course(
        self, contents, assignments: dict[int, dict], course_name: str, course_path: str
    ):
        recordings_folder = os.path.join(course_path, "Grabaciones")
        os.makedirs(course_path, exist_ok=True)
        os.makedirs(recordings_folder, exist_ok=True)
        self.download_recordings(course_name, recordings_folder)
        remote_tree = self.build_remote_tree(contents, assignments, course_path)

    def sync_courses(self, root_folder: str):
        courses = self.moodle_client.list_my_courses()
        assignments_courses = self.moodle_client.get_assignements_from_courses()
        for course in courses:
            if (cid := course.get("id")) and (course_name := course.get("fullname")):
                print(
                    f"\nFetching contents for: {course.get('fullname') or course.get('shortname')} (id={cid}) ..."
                )
                contents = self.moodle_client.get_course_contents(cid)
                course_folder = os.path.join(root_folder, safe_filename(course_name))
                self.sync_course(
                    contents,
                    assignments_courses.get(cid, {}),
                    course_name,
                    course_folder,
                )
