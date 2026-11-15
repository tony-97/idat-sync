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
from idat_sync.domain.path_utils import safe_path_segment


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

    async def list_url_content(
        self, contents: Sequence[ModuleContent]
    ) -> list[Content]:
        resolved_contents = list[Content]()
        for content in contents:
            if content.fileurl and content.filename:
                if strategy := self._get_strategy(content.fileurl):
                    resolved_contents.extend(
                        await strategy.list_url_content(
                            content.fileurl,
                            content.filename,
                            content.timemodified,
                        )
                        or []
                    )
        return resolved_contents

    async def get_recordings(
        self, course_name: str, recordings_folder: str | Path = ""
    ) -> list[RecordingContent]:
        recordings = await self.recording_gateway.fetch_recordings(course_name)
        folder = Path(recordings_folder)
        return [r.in_folder(folder) for r in recordings]

    async def list_courses(self) -> list[Course]:
        """Retrieve all enrolled courses with basic metadata."""
        return await self.course_gateway.list_courses()

    async def populate_course_sections(self, course: Course) -> Course:
        """Populate sections and modules for a single course."""
        return await self.course_gateway.populate_course_sections(course)

    async def _get_module_content(self, module: CourseModule) -> list[Content]:
        items: list[Content] = list(module.inline_contents)
        items.extend(await self.list_url_content(module.contents))
        safe_name = safe_path_segment(module.name)
        return [item.in_folder(safe_name) for item in items]

    async def _get_section_content(
        self, section: CourseSection
    ) -> list[Content]:
        items: list[Content] = list(section.inline_contents)
        for module in section.modules:
            if module.name:
                items.extend(await self._get_module_content(module))
        safe_name = safe_path_segment(section.name)
        return [item.in_folder(safe_name) for item in items]

    async def get_course_content(
        self, course: Course, course_dir: str | Path = ""
    ) -> list[Content]:
        """Generate download tasks / Content objects for a course using domain models."""
        course_modules_contents = list[Content]()
        for section in course.sections:
            if section.name:
                course_modules_contents.extend(
                    await self._get_section_content(section)
                )
        return [
            item.in_folder(course_dir) for item in course_modules_contents
        ]


__all__ = ["CourseRepository"]
