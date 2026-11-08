from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from shlex import quote
from typing import Sequence

from idat_sync.services.downloaders import Content, RecordingContent, StringContent
from idat_sync.services.moodle_adapter import MoodleApiAdapter
from idat_sync.services.sharepoint_adapter import SharepointApiAdapter

from office365.sharepoint.files.file import File


@dataclass
class CourseContent:
    id: int
    course_name: str
    assignments: dict
    contents: dict


class CourseRepository:
    def __init__(
        self, moodle_adapter: MoodleApiAdapter, sharepoint_adapter: SharepointApiAdapter
    ) -> None:
        self.moodle_adapter = moodle_adapter
        self.sharepoint_adapter = sharepoint_adapter
        self.strategies = (moodle_adapter, sharepoint_adapter)

    def __get_strategy(self, url: str):
        for strategy in self.strategies:
            if strategy.can_handle(url):
                return strategy

    def list_url_content(self, contents, parents: list[str]) -> Sequence[Content]:
        for content in contents:
            if (file_url := content.get("fileurl")) and (
                file_name := content.get("filename")
            ):
                last_modified = content.get("timemodified")
                if strategy := self.__get_strategy(file_url):
                    return (
                        strategy.list_url_content(
                            file_url, parents, file_name, last_modified
                        )
                        or []
                    )
        return []

    def get_recordings(
        self, course_name: str, recordings_folder: str = ""
    ) -> list[Content]:
        recordings = self.sharepoint_adapter.fetch_recordings(course_name)
        dates = [file.time_created.date() for file in recordings if file.time_created]
        start = date(2025, month=9, day=22)
        # Compute week index: 1 + floor(days_since_start / 7)
        week_index = [1 + (d - start).days // 7 for d in dates]
        recordings_content = list[Content]()
        # Group by week
        groups: defaultdict[int, list[File]] = defaultdict(list)
        for file, week in zip(recordings, week_index):
            groups[week].append(file)
        for week, files in groups.items():
            week_path = [recordings_folder, f"Semana {week}"]
            for file in files:
                if file.serverRelativeUrl:
                    share_url = f"https://idat628-my.sharepoint.com/:v:/r{quote(file.serverRelativeUrl)}?csf=1&web=1"
                    recordings_content.append(
                        RecordingContent(
                            week_path,
                            self.sharepoint_adapter.__get_name(file),
                            self.sharepoint_adapter.__get_last_modified(file),
                            share_url,
                        )
                    )
        return recordings_content

    def get_courses_raw_contents(self):
        courses_contents = list[CourseContent]()
        courses = self.moodle_adapter.fetch_enrolled_courses()
        assignments_courses = self.moodle_adapter.fetch_assignements_from_courses()
        for course in courses:
            if (cid := course.get("id")) and (course_name := course.get("fullname")):
                print(
                    f"\nFetching contents for: {course.get('fullname') or course.get('shortname')} (id={cid}) ..."
                )
                contents = self.moodle_adapter.fetch_course_contents(cid)
                assignments = assignments_courses.get(cid, {})
                courses_contents.append(
                    CourseContent(cid, course_name, contents, assignments)
                )
        return courses_contents

    def get_course_content(
        self, course_content: CourseContent, course_dir: str = ""
    ) -> list[Content]:
        course_modules_contents = list[Content]()
        contents = course_content.contents
        if contents_name := contents.get("name", ""):
            content_path = [contents_name, course_dir]
            summary_name = f"{contents_name}-[{contents.get("id", "")}]-resumen.html"
            course_modules_contents.append(
                StringContent(
                    content_path,
                    summary_name,
                    0,
                    [contents.get("summary", "")],
                )
            )
            for module in contents.get("modules", []):
                if module_name := module.get("name", ""):
                    module_path = [*content_path, module_name]
                    modname = module.get("modname")
                    module_contents = module.get("contents", [])
                    if modname == "label" and (
                        description := module.get("description", ""),
                    ):
                        course_modules_contents.append(
                            StringContent(
                                module_path, f"{module_name}.html", 0, [description]
                            )
                        )
                    elif modname == "url" and (len(module_contents) == 1):
                        course_modules_contents.extend(
                            self.list_url_content(module_contents, content_path)
                        )
                    elif modname == "assign":
                        cmid = module.get("id", None)
                        assignment = next(
                            (
                                assign
                                for assign in course_content.assignments
                                if assign.get("cmid", None) == cmid
                            ),
                            None,
                        )
                        if assignment:
                            assignment_intro_name = f"{assignment.get("name", "")}-[{assignment.get("id", "")}]-intro.html"
                            assignment_intro_path = [
                                *module_path,
                                assignment_intro_name,
                            ]
                            course_modules_contents.append(
                                StringContent(
                                    assignment_intro_path,
                                    assignment_intro_name,
                                    0,
                                    assignment.get("intro", ""),
                                )
                            )
                            course_modules_contents.extend(
                                self.list_url_content(
                                    assignment.get("introattachments", []),
                                    module_path,
                                )
                            )

                    else:
                        course_modules_contents.extend(
                            self.list_url_content(module_contents, module_path)
                        )
        return course_modules_contents

    def get_all_coruses_contents(self):
        to_download = list[Content]()
        courses_contents = self.get_courses_raw_contents()
        for course_content in courses_contents:
            to_download += self.get_course_content(course_content)
        return to_download
