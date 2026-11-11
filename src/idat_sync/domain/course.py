from dataclasses import dataclass, field

from idat_sync.domain.content import StringContent


@dataclass
class ModuleContent:
    """Represents a file or content item belonging to a module or assignment."""

    filename: str
    fileurl: str
    timemodified: int = 0
    filesize: int = 0


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


__all__ = [
    "Course",
    "CourseModule",
    "CourseSection",
    "ModuleContent",
]
