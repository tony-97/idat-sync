from collections import defaultdict
from datetime import date
from shlex import quote
from typing import Any, Sequence

from idat_sync.models import (
    Course,
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
        self, contents: Sequence[ModuleContent], parents: list[str]
    ) -> Sequence[Content]:
        for content in contents:
            if content.fileurl and content.filename:
                if strategy := self.__get_strategy(content.fileurl):
                    return (
                        strategy.list_url_content(
                            content.fileurl,
                            parents,
                            content.filename,
                            content.timemodified,
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
        return self.moodle_adapter.get_courses_with_contents()

    def get_course_content(self, course: Course, course_dir: str = "") -> list[Content]:
        """Generate download tasks / Content objects for a course using domain models."""
        course_modules_contents = list[Content]()

        for section in course.sections:
            if not section.name:
                continue

            content_path = [section.name, course_dir]
            summary_name = f"{section.name}-[{section.id}]-resumen.html"

            course_modules_contents.append(
                StringContent(
                    content_path,
                    summary_name,
                    0,
                    [section.summary],
                )
            )

            for module in section.modules:
                if not module.name:
                    continue

                module_path = [*content_path, module.name]

                if module.modname == "label":
                    if module.description:
                        course_modules_contents.append(
                            StringContent(
                                module_path,
                                f"{module.name}.html",
                                0,
                                [module.description],
                            )
                        )
                elif module.modname == "url" and len(module.contents) == 1:
                    course_modules_contents.extend(
                        self.list_url_content(module.contents, content_path)
                    )
                elif module.modname == "assign":
                    assignment = course.assignments.get(module.id)
                    if assignment:
                        assignment_intro_name = (
                            f"{assignment.name}-[{assignment.id}]-intro.html"
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
                                [assignment.intro],
                            )
                        )
                        course_modules_contents.extend(
                            self.list_url_content(
                                assignment.introattachments,
                                module_path,
                            )
                        )
                else:
                    course_modules_contents.extend(
                        self.list_url_content(module.contents, module_path)
                    )

        return course_modules_contents

    def get_all_courses_contents(self) -> list[Content]:
        to_download = list[Content]()
        courses = self.get_courses()
        for course in courses:
            to_download += self.get_course_content(course)
        return to_download
