from collections import defaultdict
from datetime import date
from shlex import quote
from typing import Any, Sequence

from idat_sync.models import (
    Assignment,
    Course,
    CourseContent,
    CourseModule,
    CourseSection,
    ModuleContent,
)
from idat_sync.services.downloaders import Content, RecordingContent, StringContent
from idat_sync.services.moodle_adapter import MoodleAdapter
from idat_sync.services.sharepoint_adapter import SharepointApiAdapter


class CourseRepository:
    """Repository coordinating access to course data and content generation.

    Operates purely on domain models (Course, CourseSection, CourseModule,
    Assignment, ModuleContent) and ensures the business logic is insulated
    from low-level vendor data representations.
    """

    def __init__(
        self, moodle_adapter: MoodleAdapter, sharepoint_adapter: SharepointApiAdapter
    ) -> None:
        self.moodle_adapter = moodle_adapter
        self.sharepoint_adapter = sharepoint_adapter
        self.strategies = (moodle_adapter, sharepoint_adapter)

    def __get_strategy(self, url: str):
        for strategy in self.strategies:
            if strategy.can_handle(url):
                return strategy
        return None

    def list_url_content(
        self, contents: Sequence[ModuleContent | Any], parents: list[str]
    ) -> Sequence[Content]:
        for content in contents:
            # Access domain model attributes safely, supporting legacy dicts if encountered
            file_url = getattr(content, "fileurl", None) or (
                content.get("fileurl") if isinstance(content, dict) else None
            )
            file_name = getattr(content, "filename", None) or (
                content.get("filename") if isinstance(content, dict) else None
            )
            last_modified = getattr(content, "timemodified", 0) or (
                content.get("timemodified", 0) if isinstance(content, dict) else 0
            )

            if file_url and file_name:
                if strategy := self.__get_strategy(file_url):
                    return (
                        strategy.list_url_content(
                            file_url, parents, file_name, int(last_modified)
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
        groups: defaultdict[int, list[Any]] = defaultdict(list)
        for file, week in zip(recordings, week_index):
            groups[week].append(file)
        for week, files in groups.items():
            week_path = [recordings_folder, f"Semana {week}"]
            for file in files:
                if file.serverRelativeUrl:
                    share_url = f"https://idat628-my.sharepoint.com/:v:/r{quote(file.serverRelativeUrl)}?csf=1&web=1"
                    name = self.sharepoint_adapter.get_name(file)
                    last_mod = self.sharepoint_adapter.get_last_modified(file)
                    recordings_content.append(
                        RecordingContent(
                            week_path,
                            name,
                            last_mod,
                            share_url,
                        )
                    )
        return recordings_content

    def get_courses(self) -> list[Course]:
        """Retrieve all enrolled courses with full domain structure."""
        return self.moodle_adapter.get_courses()

    def get_courses_raw_contents(self) -> list[Course]:
        """Backward-compatible alias for get_courses.

        Previously returned raw vendor dicts; now returns clean domain Course models.
        """
        return self.get_courses()

    def get_course_content(
        self, course_content: Course, course_dir: str = ""
    ) -> list[Content]:
        """Generate download tasks / Content objects for a course using domain models."""
        course_modules_contents = list[Content]()

        # Support both domain Course (with sections) and legacy CourseContent
        sections: list[CourseSection] = []
        if hasattr(course_content, "sections") and isinstance(course_content.sections, list):
            sections = course_content.sections
        elif hasattr(course_content, "contents"):
            raw_contents = course_content.contents
            if isinstance(raw_contents, list):
                sections = raw_contents
            elif isinstance(raw_contents, CourseSection):
                sections = [raw_contents]

        # Assignments map: cmid -> Assignment
        assignments: dict[int, Assignment] = (
            course_content.assignments
            if isinstance(course_content.assignments, dict)
            else {}
        )

        for section in sections:
            section_name = section.name if hasattr(section, "name") else ""
            if not section_name:
                continue

            content_path = [section_name, course_dir]
            section_id = section.id if hasattr(section, "id") else ""
            summary_name = f"{section_name}-[{section_id}]-resumen.html"
            section_summary = section.summary if hasattr(section, "summary") else ""

            course_modules_contents.append(
                StringContent(
                    content_path,
                    summary_name,
                    0,
                    [section_summary],
                )
            )

            modules: list[CourseModule] = getattr(section, "modules", [])
            for module in modules:
                module_name = getattr(module, "name", "")
                if not module_name:
                    continue

                module_path = [*content_path, module_name]
                modname = getattr(module, "modname", "")
                module_contents = getattr(module, "contents", [])

                if modname == "label":
                    description = getattr(module, "description", "")
                    if description:
                        course_modules_contents.append(
                            StringContent(
                                module_path, f"{module_name}.html", 0, [description]
                            )
                        )
                elif modname == "url" and len(module_contents) == 1:
                    course_modules_contents.extend(
                        self.list_url_content(module_contents, content_path)
                    )
                elif modname == "assign":
                    cmid = getattr(module, "id", None)
                    # Lookup assignment by cmid
                    assignment = assignments.get(cmid)
                    if not assignment and isinstance(assignments, (list, tuple)):
                        assignment = next(
                            (a for a in assignments if getattr(a, "cmid", None) == cmid),
                            None,
                        )

                    if assignment:
                        assignment_id = getattr(assignment, "id", "")
                        assignment_name = getattr(assignment, "name", "")
                        assignment_intro = getattr(assignment, "intro", "")
                        assignment_attachments = getattr(assignment, "introattachments", [])

                        assignment_intro_name = (
                            f"{assignment_name}-[{assignment_id}]-intro.html"
                        )
                        assignment_intro_path = [
                            *module_path,
                            assignment_intro_name,
                        ]
                        course_modules_contents.append(
                            StringContent(
                                assignment_intro_path,
                                assignment_intro_name,
                                0,
                                [assignment_intro],
                            )
                        )
                        course_modules_contents.extend(
                            self.list_url_content(
                                assignment_attachments,
                                module_path,
                            )
                        )
                else:
                    course_modules_contents.extend(
                        self.list_url_content(module_contents, module_path)
                    )

        return course_modules_contents

    def get_all_courses_contents(self) -> list[Content]:
        to_download = list[Content]()
        courses = self.get_courses()
        for course in courses:
            to_download += self.get_course_content(course)
        return to_download

    # Backward-compatible typo alias
    get_all_coruses_contents = get_all_courses_contents
