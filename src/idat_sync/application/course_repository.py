"""Course repository coordinating gateways, URL strategies, and path layout."""

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from idat_sync.application.ports import (
    CourseGateway,
    RecordingGateway,
    RemoteServiceError,
    UrlContentStrategy,
)
from idat_sync.domain.content import Content, RecordingContent
from idat_sync.domain.course import Course, ModuleContent
from idat_sync.domain.path_utils import safe_path_segment, safe_suffixed_segment

_logger = logging.getLogger(__name__)


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
        """Initialize the repository with course/recording gateways and strategies."""
        self.course_gateway = course_gateway
        self.recording_gateway = recording_gateway
        self.url_strategies = list(url_strategies)

    def _get_strategy(self, url: str) -> UrlContentStrategy | None:
        for strategy in self.url_strategies:
            if strategy.can_handle(url):
                return strategy
        return None

    @staticmethod
    def _safe_subfolder(filepath: str) -> Path | None:
        parts: list[str] = []
        for raw_part in filepath.split("/"):
            if not raw_part or raw_part == ".":
                continue
            if raw_part == "..":
                raise ValueError(f"Unsafe content filepath: {filepath}")
            parts.append(safe_path_segment(raw_part))
        return Path(*parts) if parts else None

    async def list_url_content(self, content: ModuleContent) -> list[Content]:
        """Expand a single unresolved ModuleContent link into concrete Content items."""
        resolved_contents: list[Content] = []
        if (
            content.fileurl
            and content.filename
            and (strategy := self._get_strategy(content.fileurl))
        ):
            subfolder = self._safe_subfolder(content.filepath)
            try:
                resolved_contents = (
                    await strategy.list_url_content(
                        content.fileurl,
                        content.filename,
                        content.timemodified,
                    )
                    or []
                )
            except RemoteServiceError as exc:
                _logger.warning(
                    "Skipping unresolved URL '%s': %s",
                    content.fileurl,
                    exc,
                )
            if subfolder is not None:
                resolved_contents = [
                    item.in_folder(subfolder) for item in resolved_contents
                ]
        return resolved_contents

    async def get_recordings(
        self, course_name: str, recordings_folder: str | Path = ""
    ) -> list[RecordingContent]:
        """Fetch class recordings for ``course_name`` under ``recordings_folder``."""
        recordings = await self.recording_gateway.fetch_recordings(course_name)
        folder = Path(recordings_folder)
        return [r.in_folder(folder) for r in recordings]

    async def list_courses(self) -> list[Course]:
        """Retrieve all enrolled courses with basic metadata."""
        return await self.course_gateway.list_courses()

    async def populate_course_sections(self, course: Course) -> Course:
        """Populate sections and modules for a single course."""
        return await self.course_gateway.populate_course_sections(course)

    def invalidate_cache(self) -> None:
        """Invalidate cached course gateway state before a fresh sync."""
        self.course_gateway.invalidate_cache()

    async def _resolve_module(
        self,
        module_contents: Sequence[ModuleContent],
        module_dir: Path,
        course_dir: str | Path,
    ) -> list[Content]:
        resolved: list[Content] = []
        for module_content in module_contents:
            contents = await self.list_url_content(module_content)
            resolved.extend(
                item.in_folder(module_dir).in_folder(course_dir) for item in contents
            )
        return resolved

    @staticmethod
    def _collect_stream_plan(
        course: Course, course_dir: Path | str
    ) -> tuple[list[Content], list[tuple[Sequence[ModuleContent], Path]]]:
        inline_items: list[Content] = []
        jobs: list[tuple[Sequence[ModuleContent], Path]] = []
        for section in course.sections:
            if not section.name:
                continue
            sec_name = safe_path_segment(section.name)
            inline_items.extend(
                item.in_folder(sec_name).in_folder(course_dir)
                for item in section.inline_contents
            )
            for module in section.modules:
                if not module.name:
                    continue
                module_dir = Path(sec_name) / safe_suffixed_segment(
                    module.name, f"-[{module.id}]"
                )
                inline_items.extend(
                    item.in_folder(module_dir).in_folder(course_dir)
                    for item in module.inline_contents
                )
                if module.contents:
                    jobs.append((module.contents, module_dir))
        return inline_items, jobs

    async def stream_course_content(
        self, course: Course, course_dir: Path | str = ""
    ) -> AsyncIterator[Content]:
        """Stream inline and concurrently resolved Content items for ``course``."""
        inline_items, jobs = self._collect_stream_plan(course, course_dir)
        tasks = [
            asyncio.create_task(
                self._resolve_module(module_contents, module_dir, course_dir)
            )
            for module_contents, module_dir in jobs
        ]
        try:
            if tasks:
                await asyncio.sleep(0)
            for item in inline_items:
                yield item
            for coro in asyncio.as_completed(tasks):
                for content in await coro:
                    yield content
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = ["CourseRepository"]
