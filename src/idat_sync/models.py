from dataclasses import dataclass, field


@dataclass
class ModuleContent:
    """Represents a file or content item belonging to a module or assignment."""

    filename: str
    fileurl: str
    timemodified: int = 0
    filesize: int = 0


@dataclass
class Assignment:
    """Represents a Moodle assignment."""

    id: int
    cmid: int
    name: str
    intro: str = ""
    introattachments: list[ModuleContent] = field(default_factory=list)


@dataclass
class CourseModule:
    """Represents a module / activity within a course section (e.g. resource, assign, url, label)."""

    id: int
    name: str
    modname: str
    description: str = ""
    contents: list[ModuleContent] = field(default_factory=list)


@dataclass
class CourseSection:
    """Represents a section (topic / week) within a course."""

    id: int
    name: str
    summary: str = ""
    modules: list[CourseModule] = field(default_factory=list)


@dataclass
class Course:
    """Domain model representing a course, including its sections and assignments."""

    id: int
    name: str
    sections: list[CourseSection] = field(default_factory=list)
    assignments: dict[int, Assignment] = field(default_factory=dict)
