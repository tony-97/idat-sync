from collections import defaultdict
from datetime import date
from pathlib import Path
from shlex import quote
from typing import Any, Sequence

from idat_sync.domain.content import Content, RecordingContent, StringContent
from idat_sync.domain.course import (
    Course,
    CourseModule,
    CourseSection,
    ModuleContent,
)
from idat_sync.infrastructure.moodle_adapter import MoodleAdapter
from idat_sync.infrastructure.sharepoint_adapter import SharepointApiAdapter


class CourseRepository:
    """Repository coordinating access to course data and content generation.

    Operates purely on domain models (Course, CourseSection, CourseModule,
    Assignment, ModuleContent) and ensures the business logic is insulated
    from low-level vendor data representations.
    """

    def __init__(
        self,
        moodle_adapter: MoodleAdapter,
        sharepoint_adapter: SharepointApiAdapter,
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
        self, contents: Sequence[ModuleContent]
    ) -> list[Content]:
        resolved_contents = list[Content]()
        for content in contents:
            if content.fileurl and content.filename:
                if strategy := self.__get_strategy(content.fileurl):
                    resolved_contents.extend(
                        strategy.list_url_content(
                            content.fileurl,
                            content.filename,
                            content.timemodified,
                        )
                        or []
                    )
        return resolved_contents

    def get_recordings(
        self, course_name: str, recordings_folder: str | Path = ""
    ) -> list[Content]:
        recordings = self.sharepoint_adapter.fetch_recordings(course_name)
        dates = [
            file.time_created.date()
            for file in recordings
            if file.time_created
        ]
        start = date(2025, month=9, day=22)
        # Compute week index: 1 + floor(days_since_start / 7)
        week_index = [1 + (d - start).days // 7 for d in dates]
        recordings_content = list[Content]()
        # Group by week
        groups: defaultdict[int, list[Any]] = defaultdict(list)
        for file, week in zip(recordings, week_index):
            groups[week].append(file)
        for week, files in groups.items():
            week_folder = Path(recordings_folder) / f"Semana {week}"
            for file in files:
                if file.serverRelativeUrl:
                    share_url = f"https://idat628-my.sharepoint.com/:v:/r{quote(file.serverRelativeUrl)}?csf=1&web=1"
                    name = self.sharepoint_adapter.get_name(file)
                    last_mod = self.sharepoint_adapter.get_last_modified(file)
                    recordings_content.append(
                        RecordingContent(
                            Path(name),
                            last_mod,
                            share_url,
                        ).in_folder(week_folder)
                    )
        return recordings_content

    def get_courses(self) -> list[Course]:
        """Retrieve all enrolled courses with full domain structure."""
        return self.moodle_adapter.get_courses_with_contents()

    def _get_module_content(
        self, course: Course, module: CourseModule
    ) -> list[Content]:
        items = list[Content]()
        if module.modname == "label":
            if module.description:
                items.append(
                    StringContent(
                        Path(f"{module.name}.html"),
                        0,
                        [module.description],
                    )
                )
        elif module.modname == "assign":
            assignment = course.assignments.get(module.id)
            if assignment:
                assignment_intro_name = (
                    f"{assignment.name}-[{assignment.id}]-intro.html"
                )
                items.append(
                    StringContent(
                        Path(assignment_intro_name),
                        0,
                        [assignment.intro],
                    )
                )
                items.extend(
                    self.list_url_content(assignment.introattachments)
                )
        else:
            items.extend(self.list_url_content(module.contents))

        return [item.in_folder(module.name) for item in items]

    def _get_section_content(
        self, course: Course, section: CourseSection
    ) -> list[Content]:
        summary_name = f"{section.name}-[{section.id}]-resumen.html"
        items: list[Content] = [
            StringContent(
                Path(summary_name),
                0,
                [section.summary],
            )
        ]
        for module in section.modules:
            if module.name:
                items.extend(self._get_module_content(course, module))

        return [item.in_folder(section.name) for item in items]

    def get_course_content(
        self, course: Course, course_dir: str | Path = ""
    ) -> list[Content]:
        """Generate download tasks / Content objects for a course using domain models."""
        course_modules_contents = list[Content]()
        for section in course.sections:
            if section.name:
                course_modules_contents.extend(
                    self._get_section_content(course, section)
                )
        return [
            item.in_folder(course_dir) for item in course_modules_contents
        ]

    def get_all_courses_contents(self) -> list[Content]:
        to_download = list[Content]()
        courses = self.get_courses()
        for course in courses:
            to_download += self.get_course_content(course)
        return to_download
