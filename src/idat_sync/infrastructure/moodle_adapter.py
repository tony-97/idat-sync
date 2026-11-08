from pathlib import Path
from urllib.parse import urlparse

from idat_sync.domain.content import MoodleContent
from idat_sync.domain.course import (
    Assignment,
    Course,
    CourseModule,
    CourseSection,
    ModuleContent,
)
from idat_sync.infrastructure.moodle_client import MoodleClient


class MoodleAdapter:
    """Anti-Corruption Layer (Adapter) that transforms raw vendor data from

    MoodleClient into strongly-typed domain models.
    """

    def __init__(self, client: MoodleClient) -> None:
        self.client = client

    @property
    def token(self) -> str:
        return self.client.token

    def can_handle(self, url: str) -> bool:
        return "aulavirtual" in urlparse(url).netloc

    def list_url_content(
        self, url: str, filename: str, last_modified: int
    ) -> list[MoodleContent]:
        # TODO: list recursive moodle folder url
        return [MoodleContent(Path(filename), last_modified, url)]

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

    def _to_assignment(self, raw_assign: dict) -> Assignment:
        intro_attachments = [
            self._to_module_content(att)
            for att in raw_assign.get("introattachments", [])
            if isinstance(att, dict)
        ]
        return Assignment(
            id=int(raw_assign.get("id", 0)),
            cmid=int(raw_assign.get("cmid", 0)),
            name=raw_assign.get("name", ""),
            intro=raw_assign.get("intro", ""),
            introattachments=intro_attachments,
        )

    def _to_course_module(self, raw_module: dict) -> CourseModule:
        contents = [
            self._to_module_content(item)
            for item in raw_module.get("contents", [])
            if isinstance(item, dict)
        ]
        return CourseModule(
            id=int(raw_module.get("id", 0)),
            name=raw_module.get("name", ""),
            modname=raw_module.get("modname", ""),
            description=raw_module.get("description", ""),
            contents=contents,
        )

    def _to_course_section(self, raw_section: dict) -> CourseSection:
        modules = [
            self._to_course_module(module)
            for module in raw_section.get("modules", [])
            if isinstance(module, dict)
        ]
        return CourseSection(
            id=int(raw_section.get("id", 0)),
            name=raw_section.get("name", ""),
            summary=raw_section.get("summary", ""),
            modules=modules,
        )

    # -------------------------------------------------------------------------
    # Domain-facing Retrieval Methods
    # -------------------------------------------------------------------------

    def list_courses(self) -> list[Course]:
        """List all enrolled courses with basic metadata (id and name)."""
        raw_courses = self.client.fetch_enrolled_courses()
        courses: list[Course] = []
        for raw in raw_courses:
            if not isinstance(raw, dict):
                continue
            course_id = raw.get("id")
            course_name = raw.get("fullname") or raw.get("shortname") or ""
            if course_id:
                courses.append(Course(id=int(course_id), name=str(course_name)))
        return courses

    def get_assignments_by_course(self) -> dict[int, dict[int, Assignment]]:
        """Fetch assignments across all enrolled courses organized by course_id -> {cmid: Assignment}."""
        raw_courses = self.client.fetch_assignments()
        courses_assignments: dict[int, dict[int, Assignment]] = {}
        for raw_course in raw_courses:
            if not isinstance(raw_course, dict):
                continue
            cid = raw_course.get("id")
            if not cid:
                continue
            module_assignments: dict[int, Assignment] = {}
            for raw_assign in raw_course.get("assignments", []):
                if isinstance(raw_assign, dict):
                    assignment = self._to_assignment(raw_assign)
                    module_assignments[assignment.cmid] = assignment
            courses_assignments[int(cid)] = module_assignments
        return courses_assignments

    def get_course_sections(self, course_id: int) -> list[CourseSection]:
        """Retrieve sections and modules for a specific course as domain models."""
        raw_sections = self.client.fetch_course_contents(course_id)
        return [
            self._to_course_section(raw_sec)
            for raw_sec in raw_sections
            if isinstance(raw_sec, dict)
        ]

    def get_courses_with_contents(self) -> list[Course]:
        """Retrieve all enrolled courses fully populated with sections, modules, and assignments."""
        courses = self.list_courses()
        assignments_by_course = self.get_assignments_by_course()

        for course in courses:
            print(f"\nFetching contents for: {course.name} (id={course.id}) ...")
            course.sections = self.get_course_sections(course.id)
            course.assignments = assignments_by_course.get(course.id, {})

        return courses
