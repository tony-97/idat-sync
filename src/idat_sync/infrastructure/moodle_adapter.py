import html
import logging
from pathlib import Path
from typing import Any
import unicodedata
from urllib.parse import urlparse

from idat_sync.domain.content import MoodleContent, StringContent
from idat_sync.domain.course import (
    Course,
    CourseModule,
    CourseSection,
    ModuleContent,
)
from idat_sync.domain.path_utils import safe_path_segment, safe_suffixed_segment
from idat_sync.infrastructure.moodle_client import MoodleClient, MoodleTokenClient

_logger = logging.getLogger(__name__)


class MoodleTokenAdapter:
    """Adapter implementing MoodleTokenPort over MoodleTokenClient."""

    def __init__(self, client: MoodleTokenClient) -> None:
        self.client = client

    async def fetch_token(self, username: str, password: str) -> str | None:
        payload = await self.client.request_token(username, password)
        if isinstance(payload, dict):
            if "error" in payload:
                _logger.warning("[!] Token error: %s", payload.get("error"))
                return None
            token = payload.get("token")
            if isinstance(token, str) and token:
                return token
        _logger.warning(
            "[!] Could not obtain token. Check URL/credentials or if mobile service is enabled."
        )
        return None


class MoodleAdapter:
    """Anti-Corruption Layer (Adapter) that transforms raw vendor data from

    MoodleClient into strongly-typed domain models.
    """

    def __init__(
        self,
        client: MoodleClient,
        moodle_base_url: str,
    ) -> None:
        self.client = client
        self.moodle_host = (urlparse(moodle_base_url).hostname or "").lower()
        self._assignments_cache: dict[int, dict[int, dict]] | None = None

    def can_handle(self, url: str) -> bool:
        return (urlparse(url).hostname or "").lower() == self.moodle_host

    async def list_url_content(
        self, url: str, filename: str, last_modified: int
    ) -> list[MoodleContent]:
        # TODO: list recursive moodle folder url
        return [MoodleContent(Path(safe_path_segment(filename)), last_modified, url)]

    @staticmethod
    def _safe_suffixed_html(prefix: str, suffix: str) -> str:
        return safe_suffixed_segment(prefix, suffix)

    # -------------------------------------------------------------------------
    # Internal Vendor-to-Domain Mappers
    # -------------------------------------------------------------------------

    def _to_module_content(self, raw_content: dict) -> ModuleContent:
        return ModuleContent(
            filename=raw_content.get("filename", ""),
            fileurl=raw_content.get("fileurl", ""),
            timemodified=int(raw_content.get("timemodified", 0) or 0),
            filesize=int(raw_content.get("filesize", 0) or 0),
            filepath=raw_content.get("filepath") or "/",
        )

    def _to_course_module(
        self, raw_module: dict, assignments: dict[int, Any] | None = None
    ) -> CourseModule:
        modname = raw_module.get("modname", "")
        module_name = raw_module.get("name", "")
        module_id = int(raw_module.get("id", 0))

        inline_contents: list[StringContent] = []
        contents: list[ModuleContent] = []

        if modname == "assign":
            assign = (assignments or {}).get(module_id)
            if assign:
                assign_name = assign.get("name") or module_name
                assign_id = int(assign.get("id", 0))
                assign_intro = assign.get("intro", "")
                inline_contents = [
                    StringContent(
                        Path(
                            self._safe_suffixed_html(
                                assign_name, f"-[{assign_id}]-intro.html"
                            )
                        ),
                        0,
                        [assign_intro],
                    )
                ]
                contents = [
                    self._to_module_content(att)
                    for att in assign.get("introattachments", [])
                    if isinstance(att, dict)
                ]
        else:
            contents = [
                self._to_module_content(item)
                for item in raw_module.get("contents", [])
                if isinstance(item, dict)
            ]

        return CourseModule(
            id=module_id,
            name=module_name,
            inline_contents=inline_contents,
            contents=contents,
        )

    @staticmethod
    def _is_bibliografia_section(section_name: str) -> bool:
        normalized = "".join(
            ch
            for ch in unicodedata.normalize("NFKD", section_name)
            if not unicodedata.combining(ch)
        )
        return normalized.strip().lower() == "bibliografia"

    @staticmethod
    def _format_bibliografia_url(raw_module: dict) -> str | None:
        name = (raw_module.get("name") or "").strip()
        urls = [
            item["fileurl"]
            for item in raw_module.get("contents", [])
            if isinstance(item, dict) and item.get("fileurl")
        ]
        if urls:
            label = html.escape(name)
            return "\n".join(
                f'<p><a href="{html.escape(url, quote=True)}">{label or html.escape(url)}</a></p>'
                for url in urls
            )
        if name:
            return f"<p>{html.escape(name)}</p>"
        return None

    def _to_course_section(
        self, raw_section: dict, assignments: dict[int, Any] | None = None
    ) -> CourseSection:
        sec_id = int(raw_section.get("id", 0))
        sec_name = raw_section.get("name", "")
        inline_contents: list[StringContent] = []
        if "summary" in raw_section:
            inline_contents = [
                StringContent(
                    Path(
                        self._safe_suffixed_html(
                            sec_name, f"-[{sec_id}]-resumen.html"
                        )
                    ),
                    0,
                    [raw_section.get("summary", "")],
                )
            ]

        is_bib = self._is_bibliografia_section(sec_name)
        inline_parts: list[str] = []
        modules: list[CourseModule] = []
        for module in raw_section.get("modules", []):
            if not isinstance(module, dict):
                continue
            modname = module.get("modname")
            if modname == "label":
                if desc := module.get("description", ""):
                    inline_parts.append(desc)
                continue
            if is_bib and modname == "url":
                if entry := self._format_bibliografia_url(module):
                    inline_parts.append(entry)
                continue
            modules.append(self._to_course_module(module, assignments))

        if inline_parts:
            filename = "bibliografia.html" if is_bib else "info.html"
            inline_contents.append(
                StringContent(Path(filename), 0, ["\n".join(inline_parts)])
            )
        return CourseSection(
            id=sec_id,
            name=sec_name,
            inline_contents=inline_contents,
            modules=modules,
        )

    # -------------------------------------------------------------------------
    # Domain-facing Retrieval Methods
    # -------------------------------------------------------------------------

    async def list_courses(self) -> list[Course]:
        """List all enrolled courses with basic metadata (id, name, category)."""
        raw_courses = await self.client.fetch_enrolled_courses()
        courses: list[Course] = []
        for raw in raw_courses:
            if not isinstance(raw, dict):
                continue
            course_id = raw.get("id")
            course_name = raw.get("fullname") or raw.get("shortname") or ""
            course_category = raw.get("coursecategory") or ""
            if course_id:
                courses.append(
                    Course(
                        id=int(course_id),
                        name=str(course_name),
                        category=str(course_category),
                    )
                )
        return courses

    async def _get_assignments_by_course(self) -> dict[int, dict[int, dict]]:
        """Fetch assignments across all enrolled courses organized by course_id -> {cmid: raw_assignment}."""
        raw_courses = await self.client.fetch_assignments()
        courses_assignments: dict[int, dict[int, dict]] = {}
        for raw_course in raw_courses:
            if not isinstance(raw_course, dict):
                continue
            cid = raw_course.get("id")
            if not cid:
                continue
            module_assignments: dict[int, dict] = {}
            for raw_assign in raw_course.get("assignments", []):
                if isinstance(raw_assign, dict):
                    cmid = int(raw_assign.get("cmid", 0))
                    module_assignments[cmid] = raw_assign
            courses_assignments[int(cid)] = module_assignments
        return courses_assignments

    async def get_course_sections(
        self, course_id: int, assignments: dict[int, Any] | None = None
    ) -> list[CourseSection]:
        """Retrieve sections and modules for a specific course as domain models."""
        raw_sections = await self.client.fetch_course_contents(course_id)
        return [
            self._to_course_section(raw_sec, assignments)
            for raw_sec in raw_sections
            if isinstance(raw_sec, dict)
        ]

    async def populate_course_sections(self, course: Course) -> Course:
        """Populate sections and modules for a single course, caching assignments once."""
        if self._assignments_cache is None:
            self._assignments_cache = await self._get_assignments_by_course()
        course_assignments = self._assignments_cache.get(course.id, {})
        course.sections = await self.get_course_sections(
            course.id, assignments=course_assignments
        )
        return course

    def invalidate_cache(self) -> None:
        """Clear cached Moodle assignment metadata so the next sync refreshes it."""
        self._assignments_cache = None
