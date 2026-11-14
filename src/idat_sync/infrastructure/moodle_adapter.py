from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from idat_sync.domain.content import MoodleContent, StringContent
from idat_sync.domain.course import (
    Course,
    CourseModule,
    CourseSection,
    ModuleContent,
)
from idat_sync.domain.path_utils import safe_path_segment
from idat_sync.infrastructure.moodle_client import MoodleClient, MoodleTokenClient


class MoodleTokenAdapter:
    """Adapter implementing MoodleTokenPort over MoodleTokenClient."""

    def __init__(self, client: MoodleTokenClient) -> None:
        self.client = client

    def fetch_token(self, username: str, password: str) -> str | None:
        payload = self.client.request_token(username, password)
        if isinstance(payload, dict):
            if "error" in payload:
                print(f"[!] Token error: {payload.get('error')}")
                return None
            token = payload.get("token")
            if isinstance(token, str) and token:
                return token
        print(
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

    def list_url_content(
        self, url: str, filename: str, last_modified: int
    ) -> list[MoodleContent]:
        # TODO: list recursive moodle folder url
        return [MoodleContent(Path(safe_path_segment(filename)), last_modified, url)]

    @staticmethod
    def _safe_suffixed_html(prefix: str, suffix: str) -> str:
        budget = max(1, 100 - len(suffix))
        bounded = safe_path_segment(prefix, max_len=budget) if prefix else ""
        return safe_path_segment(f"{bounded}{suffix}")

    # -------------------------------------------------------------------------
    # Internal Vendor-to-Domain Mappers
    # -------------------------------------------------------------------------

    def _to_module_content(self, raw_content: dict) -> ModuleContent:
        return ModuleContent(
            filename=raw_content.get("filename", ""),
            fileurl=raw_content.get("fileurl", ""),
            timemodified=int(raw_content.get("timemodified", 0) or 0),
            filesize=int(raw_content.get("filesize", 0) or 0),
        )

    def _to_course_module(
        self, raw_module: dict, assignments: dict[int, Any] | None = None
    ) -> CourseModule:
        modname = raw_module.get("modname", "")
        module_name = raw_module.get("name", "")
        module_id = int(raw_module.get("id", 0))

        inline_contents: list[StringContent] = []
        contents: list[ModuleContent] = []

        if modname == "label":
            description = raw_module.get("description", "")
            if description:
                inline_contents = [
                    StringContent(
                        Path(safe_path_segment(f"{module_name}.html")),
                        0,
                        [description],
                    )
                ]
        elif modname == "assign":
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

        modules = [
            self._to_course_module(module, assignments)
            for module in raw_section.get("modules", [])
            if isinstance(module, dict)
        ]
        return CourseSection(
            id=sec_id,
            name=sec_name,
            inline_contents=inline_contents,
            modules=modules,
        )

    # -------------------------------------------------------------------------
    # Domain-facing Retrieval Methods
    # -------------------------------------------------------------------------

    def list_courses(self) -> list[Course]:
        """List all enrolled courses with basic metadata (id, name, category)."""
        raw_courses = self.client.fetch_enrolled_courses()
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

    def _get_assignments_by_course(self) -> dict[int, dict[int, dict]]:
        """Fetch assignments across all enrolled courses organized by course_id -> {cmid: raw_assignment}."""
        raw_courses = self.client.fetch_assignments()
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

    def get_course_sections(
        self, course_id: int, assignments: dict[int, Any] | None = None
    ) -> list[CourseSection]:
        """Retrieve sections and modules for a specific course as domain models."""
        raw_sections = self.client.fetch_course_contents(course_id)
        return [
            self._to_course_section(raw_sec, assignments)
            for raw_sec in raw_sections
            if isinstance(raw_sec, dict)
        ]

    def populate_course_sections(self, course: Course) -> Course:
        """Populate sections and modules for a single course, caching assignments once."""
        if self._assignments_cache is None:
            self._assignments_cache = self._get_assignments_by_course()
        course_assignments = self._assignments_cache.get(course.id, {})
        course.sections = self.get_course_sections(
            course.id, assignments=course_assignments
        )
        return course
