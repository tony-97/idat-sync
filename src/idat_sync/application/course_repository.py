from pathlib import Path
from typing import Sequence

from idat_sync.application.ports import (
    CourseGateway,
    RecordingGateway,
    UrlContentStrategy,
)
from idat_sync.domain.content import Content, RecordingContent
from idat_sync.domain.course import (
    Course,
    CourseModule,
    CourseSection,
    ModuleContent,
)


class CourseRepository:
    """Repository coordinating access to course data and content generation.

    Operates purely on domain models and ports without importing infrastructure.
    """

    def __init__(
        self,
        course_gateway: CourseGateway,
        recording_gateway: RecordingGateway,
        url_strategies: Sequence[UrlContentStrategy],
    ) -> None:
        self.course_gateway = course_gateway
        self.recording_gateway = recording_gateway
        self.url_strategies = list(url_strategies)

    def _get_strategy(self, url: str) -> UrlContentStrategy | None:
        for strategy in self.url_strategies:
            if strategy.can_handle(url):
                return strategy
        return None

    def list_url_content(
        self, contents: Sequence[ModuleContent]
    ) -> list[Content]:
        resolved_contents = list[Content]()
        for content in contents:
            if content.fileurl and content.filename:
                if strategy := self._get_strategy(content.fileurl):
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
    ) -> list[RecordingContent]:
        recordings = self.recording_gateway.fetch_recordings(course_name)
        folder = Path(recordings_folder)
        return [r.in_folder(folder) for r in recordings]

    def get_courses(self) -> list[Course]:
        """Retrieve all enrolled courses with full domain structure."""
        return self.course_gateway.get_courses_with_contents()

    def _get_module_content(self, module: CourseModule) -> list[Content]:
        items: list[Content] = list(module.inline_contents)
        items.extend(self.list_url_content(module.contents))
        return [item.in_folder(module.name) for item in items]

    def _get_section_content(self, section: CourseSection) -> list[Content]:
        items: list[Content] = list(section.inline_contents)
        for module in section.modules:
            if module.name:
                items.extend(self._get_module_content(module))
        return [item.in_folder(section.name) for item in items]

    def get_course_content(
        self, course: Course, course_dir: str | Path = ""
    ) -> list[Content]:
        """Generate download tasks / Content objects for a course using domain models."""
        course_modules_contents = list[Content]()
        for section in course.sections:
            if section.name:
                course_modules_contents.extend(
                    self._get_section_content(section)
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


__all__ = ["CourseRepository"]
