"""Pure-Python hierarchical course, folder, and file progress tree."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from idat_sync.domain.course import Course


class RowStatus(StrEnum):
    """Display status labels for course and file rows in CourseTree."""

    IDLE = "Idle"
    QUEUED = "Queued"
    FETCHING = "Fetching contents..."
    SYNCING = "Syncing"
    SYNCED = "Synced"
    PENDING = "Pending"
    DOWNLOADING = "Downloading"
    COMPLETED = "Completed"
    FAILED = "Failed"


@dataclass
class Progress:
    """Download progress counter for a single course."""

    done: int = 0
    total: int = 0

    def __str__(self) -> str:
        """Format completion percentage as a whole-number string."""
        return f"{int(self.done * 100 / self.total)}%" if self.total else "100%"


@dataclass
class _FileNode:
    """Leaf file row in the course tree."""

    name: str
    status: RowStatus

    @property
    def progress(self) -> Progress:
        """Return 1/1 when synced or completed, else 0/1."""
        done = 1 if self.status in {RowStatus.SYNCED, RowStatus.COMPLETED} else 0
        return Progress(done=done, total=1)

    def to_dict(self) -> dict[str, Any]:
        """Serialize file node for frontend rendering."""
        return {
            "type": "file",
            "name": self.name,
            "status": str(self.status),
            "progress": str(self.progress),
        }


def _aggregate_folder_state(
    files: list[_FileNode],
) -> tuple[RowStatus, Progress]:
    """Compute aggregate status and progress across a folder's descendant files."""
    total = len(files)
    done = sum(f.progress.done for f in files)
    progress = Progress(done=done, total=total)
    statuses = {f.status for f in files}
    if RowStatus.DOWNLOADING in statuses:
        status = RowStatus.DOWNLOADING
    elif RowStatus.FAILED in statuses:
        status = RowStatus.FAILED
    elif total and statuses == {RowStatus.SYNCED}:
        status = RowStatus.SYNCED
    elif done == total:
        status = RowStatus.COMPLETED
    else:
        status = RowStatus.PENDING
    return status, progress


@dataclass
class _FolderNode:
    """Nested folder node containing child folders and leaf files."""

    id: str
    name: str
    folders: dict[str, _FolderNode] = field(default_factory=dict)
    files: dict[str, _FileNode] = field(default_factory=dict)

    def _iter_files(self) -> list[_FileNode]:
        descendants = list(self.files.values())
        for folder in self.folders.values():
            descendants.extend(folder._iter_files())  # noqa: SLF001  # pylint: disable=protected-access
        return descendants

    def to_dict(self) -> dict[str, Any]:
        """Serialize folder node, aggregate progress/status, and children."""
        status, progress = _aggregate_folder_state(self._iter_files())
        children = [f.to_dict() for f in self.folders.values()] + [
            f.to_dict() for f in self.files.values()
        ]
        return {
            "id": self.id,
            "type": "folder",
            "name": self.name,
            "status": str(status),
            "progress": str(progress),
            "open": True,
            "children": children,
        }


@dataclass
class _CourseNode:  # pylint: disable=too-many-instance-attributes
    """Top-level course row and its nested folder/file state."""

    course_id: int
    name: str
    checked: bool
    status: RowStatus = RowStatus.IDLE
    progress_text: str = "0%"
    open: bool = False
    root_folders: dict[str, _FolderNode] = field(default_factory=dict)
    root_files: dict[str, _FileNode] = field(default_factory=dict)
    file_paths: set[Path] = field(default_factory=set)
    progress: Progress | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize course row and its nested children."""
        children = [f.to_dict() for f in self.root_folders.values()] + [
            f.to_dict() for f in self.root_files.values()
        ]
        return {
            "id": self.course_id,
            "name": self.name,
            "checked": self.checked,
            "status": str(self.status),
            "progress": self.progress_text,
            "open": self.open,
            "children": children,
        }


class CourseTree:
    """Pure-Python tree tracking course selection, nested folders, and progress."""

    def __init__(self) -> None:
        """Initialize an empty course tree."""
        self._nodes: dict[int, _CourseNode] = {}
        self._file_to_course: dict[Path, tuple[int, _FileNode]] = {}

    def set_courses(
        self,
        courses: Sequence[Course],
        selected: set[int],
        in_progress: set[int] | None = None,
    ) -> list[Course]:
        """Replace course rows, ordering ``in_progress`` first when given."""
        ordered = (
            [c for c in courses if c.id in in_progress]
            + [c for c in courses if c.id not in in_progress]
            if in_progress is not None
            else list(courses)
        )
        self._nodes.clear()
        self._file_to_course.clear()
        for course in ordered:
            self._nodes[course.id] = _CourseNode(
                course_id=course.id,
                name=course.name,
                checked=course.id in selected,
            )
        return ordered

    def refresh_selection(self, selected: set[int]) -> None:
        """Update checked state for all loaded course rows."""
        for course_id, node in self._nodes.items():
            node.checked = course_id in selected

    def reset_for_sync(self, course_ids: Sequence[int]) -> None:
        """Clear children and mark ``course_ids`` as Queued at 0%."""
        for course_id in course_ids:
            if (node := self._nodes.get(course_id)) is None:
                continue
            self._clear_course_children(node)
            node.progress = None
            node.status = RowStatus.QUEUED
            node.progress_text = "0%"
            node.open = False

    def show_files(
        self,
        course_id: int,
        course_dir: Path,
        synced: Sequence[Path],
        pending: Sequence[Path],
    ) -> None:
        """Append synced and pending file nodes additively under ``course_id``."""
        if (node := self._nodes.get(course_id)) is None:
            return
        for path in synced:
            self._insert_file(node, course_dir, path, RowStatus.SYNCED)
        for path in pending:
            file_node = self._insert_file(node, course_dir, path, RowStatus.PENDING)
            node.file_paths.add(path)
            self._file_to_course[path] = (course_id, file_node)

        if node.progress is None:
            node.progress = Progress()
        node.progress.done += len(synced)
        node.progress.total += len(synced) + len(pending)
        node.status = RowStatus.SYNCING
        node.progress_text = str(node.progress)
        node.open = True

    def set_file_status(self, path: Path, status: RowStatus) -> None:
        """Update a pending file row and advance parent progress on completion."""
        if (entry := self._file_to_course.get(path)) is None:
            return
        course_id, file_node = entry
        file_node.status = status
        if (
            status is RowStatus.COMPLETED
            and (node := self._nodes.get(course_id)) is not None
            and node.progress is not None
        ):
            node.progress.done += 1
            node.status = RowStatus.SYNCING
            node.progress_text = str(node.progress)

    def set_course_status(self, course_id: int, status: RowStatus) -> None:
        """Update a course row's status and percentage."""
        if (node := self._nodes.get(course_id)) is None:
            return
        if status is RowStatus.FETCHING:
            node.progress = Progress()
        if status is RowStatus.COMPLETED:
            pct = "100%"
        elif (
            status is RowStatus.FAILED
            and node.progress is not None
            and node.progress.total
        ):
            pct = str(node.progress)
        else:
            pct = "0%"
        node.status = status
        node.progress_text = pct

    def to_dict(self) -> list[dict[str, Any]]:
        """Serialize all course rows and their nested folder/file trees."""
        return [node.to_dict() for node in self._nodes.values()]

    def _clear_course_children(self, node: _CourseNode) -> None:
        node.root_folders.clear()
        node.root_files.clear()
        for path in node.file_paths:
            self._file_to_course.pop(path, None)
        node.file_paths.clear()

    def _insert_file(
        self,
        node: _CourseNode,
        course_dir: Path,
        path: Path,
        status: RowStatus,
    ) -> _FileNode:
        rel = path.relative_to(course_dir) if path.is_relative_to(course_dir) else path
        folder_parts = rel.parts[:-1]
        file_node = _FileNode(name=rel.name, status=status)
        if not folder_parts:
            node.root_files[rel.name] = file_node
            return file_node
        prefix = folder_parts[0]
        current = node.root_folders.setdefault(
            prefix,
            _FolderNode(id=f"folder:{node.course_id}:{prefix}", name=prefix),
        )
        for part in folder_parts[1:]:
            prefix = f"{prefix}/{part}"
            current = current.folders.setdefault(
                part,
                _FolderNode(id=f"folder:{node.course_id}:{prefix}", name=part),
            )
        current.files[rel.name] = file_node
        return file_node


__all__ = ["CourseTree", "Progress", "RowStatus"]
