"""Curriculum domain models and academic term helpers."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from idat_sync.domain.content import StringContent

_TERM_PATTERN = re.compile(r"(\d{4})-(I{1,3}|IV)([A-Z]?)$")
_ROMAN_TERMS = {"I": 1, "II": 2, "III": 3, "IV": 4}


@dataclass
class ModuleContent:
    """Represents a file or content item belonging to a module or assignment."""

    filename: str
    fileurl: str
    timemodified: int = 0
    filepath: str = "/"


@dataclass
class CourseModule:
    """Represents a module / activity within a course section."""

    id: int
    name: str
    inline_contents: list[StringContent] = field(default_factory=list)
    contents: list[ModuleContent] = field(default_factory=list)


@dataclass
class CourseSection:
    """Represents a section (topic / week) within a course."""

    id: int
    name: str
    inline_contents: list[StringContent] = field(default_factory=list)
    modules: list[CourseModule] = field(default_factory=list)


@dataclass
class Course:
    """Domain model representing a course, including its sections."""

    id: int
    name: str
    sections: list[CourseSection] = field(default_factory=list)
    category: str = ""


def _match_course_term(course: Course) -> re.Match[str] | None:
    """Match the academic term token from ``course.category`` or ``course.name``."""
    source = course.category.strip()
    if not source:
        parts = course.name.split(None, 1)
        source = parts[0] if parts else ""
    return _TERM_PATTERN.search(source)


def _parse_course_term(course: Course) -> tuple[int, int, str] | None:
    """Extract a sortable (year, roman_term, suffix) key from category or name."""
    match = _match_course_term(course)
    if not match:
        return None
    year_str, roman, suffix = match.groups()
    return (int(year_str), _ROMAN_TERMS[roman], suffix)


def course_term_sort_key(course: Course) -> tuple[int, int, int, str]:
    """Return a sortable ``(has_term, year, roman_rank, suffix)`` key."""
    parsed = _parse_course_term(course)
    if parsed is None:
        return (0, 0, 0, "")
    year, roman, suffix = parsed
    return (1, year, roman, suffix)


def get_course_term(course: Course) -> str:
    """Return the normalized ``<YEAR>-<ROMAN><SUFFIX>`` term label or ``"Other"``."""
    match = _match_course_term(course)
    if not match:
        return "Other"
    year_str, roman, suffix = match.groups()
    return f"{year_str}-{roman}{suffix}"


def get_in_progress_course_ids(courses: Sequence[Course]) -> set[int]:
    """Return IDs of courses belonging to the highest academic term."""
    ranked = [
        (course.id, term)
        for course in courses
        if (term := _parse_course_term(course)) is not None
    ]
    if not ranked:
        return set()
    max_term = max(term for _, term in ranked)
    return {course_id for course_id, term in ranked if term == max_term}


__all__ = [
    "Course",
    "CourseModule",
    "CourseSection",
    "ModuleContent",
    "course_term_sort_key",
    "get_course_term",
    "get_in_progress_course_ids",
]
