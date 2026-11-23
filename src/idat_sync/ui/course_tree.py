"""Pure-Python hierarchical course, folder, and file progress tree."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from idat_sync.domain.course import course_term_sort_key, get_course_term
from idat_sync.domain.sync import ContentSyncState

if TYPE_CHECKING:
    from collections.abc import Sequence

    from idat_sync.domain.course import Course

_SPEED_ALPHA = 0.35
_FULL_PERCENT = 100


class RowStatus(StrEnum):
    """Display status labels for course and file rows in CourseTree."""

    IDLE = "Idle"
    QUEUED = "Queued"
    FETCHING = "Fetching contents..."
    FETCHING_AND_SYNCING = "Fetching & Syncing"
    SYNCING = "Syncing"
    SYNCED = "Synced"
    NEW = "New"
    OUTDATED = "Outdated"
    DOWNLOADING = "Downloading"
    COMPLETED = "Completed"
    FAILED = "Failed"


_STATE_TO_ROW = {
    ContentSyncState.SYNCED: RowStatus.SYNCED,
    ContentSyncState.NEW: RowStatus.NEW,
    ContentSyncState.OUTDATED: RowStatus.OUTDATED,
}
_ACTIVE_COURSE_STATUSES = {
    RowStatus.QUEUED,
    RowStatus.FETCHING,
    RowStatus.FETCHING_AND_SYNCING,
    RowStatus.SYNCING,
}
_REVERTIBLE_FILE_STATUSES = {
    RowStatus.DOWNLOADING,
    RowStatus.NEW,
    RowStatus.OUTDATED,
}


@dataclass
class Progress:
    """Download progress counter for a single course."""

    done: int = 0
    total: int = 0

    def __str__(self) -> str:
        """Format completion percentage as a whole-number string."""
        return f"{int(self.done * 100 / self.total)}%" if self.total else "100%"


@dataclass
class _FileProgress:
    """In-flight byte progress and smoothed transfer speed for a file row."""

    downloaded: int = 0
    total: int | None = None
    percent: int | None = 0
    speed_bps: float | None = None
    last_time: float | None = None
    last_bytes: int = 0

    def update(self, downloaded: int, total: int | None, now: float) -> None:
        """Record a byte-progress sample, updating smoothed speed and 0..99% clamp."""
        if self.last_time is not None:
            dt = now - self.last_time
            delta = downloaded - self.last_bytes
            if dt > 0 and delta >= 0:
                inst = delta / dt
                self.speed_bps = (
                    inst
                    if self.speed_bps is None
                    else _SPEED_ALPHA * inst + (1.0 - _SPEED_ALPHA) * self.speed_bps
                )
        self.last_time = now
        self.last_bytes = downloaded
        self.downloaded = downloaded
        self.total = total
        if total is not None and total > 0:
            self.percent = min(99, max(0, int(downloaded * 100 / total)))
        else:
            self.percent = None

    def reset(self, *, percent: int | None = 0) -> None:
        """Reset transient transfer metrics."""
        self.downloaded = 0
        self.total = None
        self.percent = percent
        self.speed_bps = None
        self.last_time = None
        self.last_bytes = 0


@dataclass
class _FileNode:
    """Leaf file row in the course tree."""

    id: str
    name: str
    rel_path: str
    status: RowStatus
    discovered_status: RowStatus
    error: str = ""
    dl: _FileProgress = field(default_factory=_FileProgress)

    @property
    def is_done(self) -> bool:
        """Return True when the file is already synced or completed."""
        return self.status in {RowStatus.SYNCED, RowStatus.COMPLETED}

    def to_dict(self) -> dict[str, Any]:
        """Serialize file node for frontend rendering."""
        pct = _FULL_PERCENT if self.is_done else self.dl.percent
        prog_str = (
            "100%"
            if pct == _FULL_PERCENT
            else (f"{pct}%" if pct is not None else "...")
        )
        return {
            "id": self.id,
            "type": "file",
            "name": self.name,
            "rel_path": self.rel_path,
            "status": str(self.status),
            "progress": prog_str,
            "percent": pct,
            "downloaded": self.dl.downloaded,
            "total": self.dl.total,
            "speed_bps": self.dl.speed_bps,
            "error": self.error,
        }


def _aggregate_folder_state(
    files: list[_FileNode],
) -> tuple[RowStatus, Progress]:
    """Compute aggregate status and progress across a folder's descendant files."""
    total = len(files)
    done = sum(1 for f in files if f.is_done)
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
    elif RowStatus.OUTDATED in statuses:
        status = RowStatus.OUTDATED
    else:
        status = RowStatus.NEW
    return status, progress


@dataclass
class _FolderNode:
    """Nested folder node containing child folders and leaf files."""

    id: str
    name: str
    folders: dict[str, _FolderNode] = field(default_factory=dict)
    files: dict[str, _FileNode] = field(default_factory=dict)

    def iter_files(self) -> list[_FileNode]:
        """Return all descendant files in tree order."""
        descendants = list(self.files.values())
        for folder in self.folders.values():
            descendants.extend(folder.iter_files())
        return descendants

    def to_dict(self) -> dict[str, Any]:
        """Serialize folder node, aggregate progress/status, and children."""
        status, progress = _aggregate_folder_state(self.iter_files())
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
    term: str
    in_progress: bool
    checked: bool
    status: RowStatus = RowStatus.IDLE
    progress_text: str = "0%"
    open: bool = False
    course_dir: Path | None = None
    discovering: bool = False
    root_folders: dict[str, _FolderNode] = field(default_factory=dict)
    root_files: dict[str, _FileNode] = field(default_factory=dict)
    file_paths: set[Path] = field(default_factory=set)
    progress: Progress | None = None

    def iter_files(self) -> list[_FileNode]:
        """Return all files under this course in display order."""
        files = list(self.root_files.values())
        for folder in self.root_folders.values():
            files.extend(folder.iter_files())
        return files

    def to_dict(self) -> dict[str, Any]:
        """Serialize course row and its nested children."""
        children = [f.to_dict() for f in self.root_folders.values()] + [
            f.to_dict() for f in self.root_files.values()
        ]
        return {
            "id": self.course_id,
            "name": self.name,
            "term": self.term,
            "in_progress": self.in_progress,
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
        self._activity_course_ids: set[int] = set()

    @staticmethod
    def _format_progress(node: _CourseNode) -> tuple[RowStatus, str]:
        if node.progress is None:
            return (RowStatus.IDLE, "0%")
        if node.discovering:
            return (
                RowStatus.FETCHING_AND_SYNCING,
                f"{node.progress.done}/{node.progress.total}+",
            )
        return (RowStatus.SYNCING, str(node.progress))

    def set_courses(
        self,
        courses: Sequence[Course],
        selected: set[int],
        in_progress: set[int] | None = None,
    ) -> list[Course]:
        """Replace course rows, ordered newest-term-first with in-progress first."""
        in_prog = in_progress or set()
        ordered = sorted(
            courses,
            key=lambda c: (c.id in in_prog, course_term_sort_key(c)),
            reverse=True,
        )
        self._nodes.clear()
        self._file_to_course.clear()
        self._activity_course_ids.clear()
        for course in ordered:
            self._nodes[course.id] = _CourseNode(
                course_id=course.id,
                name=course.name,
                term=get_course_term(course),
                in_progress=course.id in in_prog,
                checked=course.id in selected,
            )
        return ordered

    def refresh_selection(self, selected: set[int]) -> None:
        """Update checked state for all loaded course rows."""
        for course_id, node in self._nodes.items():
            node.checked = course_id in selected

    def reset_for_sync(self, course_ids: Sequence[int]) -> None:
        """Clear children, scope activity to ``course_ids``, and mark Queued at 0%."""
        self._activity_course_ids = set(course_ids)
        for course_id in course_ids:
            if (node := self._nodes.get(course_id)) is None:
                continue
            self._clear_course_children(node)
            node.course_dir = None
            node.discovering = False
            node.progress = None
            node.status = RowStatus.QUEUED
            node.progress_text = "0%"
            node.open = False

    def start_course(self, course_id: int, course_dir: Path) -> None:
        """Initialize incremental discovery state for ``course_id``."""
        if (node := self._nodes.get(course_id)) is None:
            return
        self._activity_course_ids.add(course_id)
        self._clear_course_children(node)
        node.course_dir = course_dir
        node.discovering = True
        node.progress = Progress()
        node.status = RowStatus.FETCHING
        node.progress_text = "..."
        node.open = False

    def add_file(self, course_id: int, path: Path, state: ContentSyncState) -> None:
        """Insert one discovered file under ``course_id`` and refresh progress."""
        if (node := self._nodes.get(course_id)) is None:
            return
        course_dir = node.course_dir or Path()
        if node.progress is None:
            node.progress = Progress()
        row_status = _STATE_TO_ROW[state]
        file_node = self._insert_file(node, course_dir, path, row_status)
        if state is ContentSyncState.SYNCED:
            file_node.dl.percent = 100
            node.progress.done += 1
        else:
            node.file_paths.add(path)
            self._file_to_course[path] = (course_id, file_node)
        node.progress.total += 1
        node.status, node.progress_text = self._format_progress(node)
        node.open = True

    def finish_discovery(self, course_id: int) -> None:
        """Transition ``course_id`` from discovering to percentage Syncing progress."""
        if (node := self._nodes.get(course_id)) is None:
            return
        node.discovering = False
        if node.progress is None:
            node.progress = Progress()
        node.status = RowStatus.SYNCING
        node.progress_text = str(node.progress)

    def set_file_progress(
        self, path: Path, downloaded: int, total: int | None
    ) -> dict[str, Any] | None:
        """Update byte progress for an active file and return targeted UI payload."""
        if (entry := self._file_to_course.get(path)) is None:
            return None
        course_id, file_node = entry
        file_node.dl.update(downloaded, total, time.monotonic())
        pct = file_node.dl.percent
        return {
            "id": file_node.id,
            "course_id": course_id,
            "downloaded": downloaded,
            "total": total,
            "percent": pct,
            "progress": f"{pct}%" if pct is not None else "...",
            "speed_bps": file_node.dl.speed_bps,
        }

    def set_file_status(self, path: Path, status: RowStatus, error: str = "") -> None:
        """Update a pending file row and advance parent progress on completion."""
        if (entry := self._file_to_course.get(path)) is None:
            return
        course_id, file_node = entry
        file_node.status = status
        file_node.error = error
        if status is RowStatus.DOWNLOADING:
            file_node.dl.reset(percent=0)
        elif status is RowStatus.COMPLETED:
            file_node.dl.percent = 100
            file_node.dl.speed_bps = None
            if (
                node := self._nodes.get(course_id)
            ) is not None and node.progress is not None:
                node.progress.done += 1
                node.status, node.progress_text = self._format_progress(node)
        elif status is RowStatus.FAILED:
            file_node.dl.speed_bps = None

    def set_course_status(self, course_id: int, status: RowStatus) -> None:
        """Update a course row's status and percentage."""
        if (node := self._nodes.get(course_id)) is None:
            return
        if status is RowStatus.COMPLETED:
            pct = "100%"
        elif status is RowStatus.FAILED:
            pct = node.progress_text
        else:
            pct = "0%"
        node.status = status
        node.progress_text = pct

    def cancel_sync(self) -> None:
        """Revert in-flight/queued rows to discovered state, preserving completed."""
        for node in self._nodes.values():
            if node.status in _ACTIVE_COURSE_STATUSES:
                node.discovering = False
                node.status = RowStatus.IDLE
                node.progress_text = (
                    str(node.progress)
                    if node.progress is not None and node.progress.total
                    else "0%"
                )
            for path in node.file_paths:
                if (entry := self._file_to_course.get(path)) is None:
                    continue
                _, file_node = entry
                if file_node.status in _REVERTIBLE_FILE_STATUSES:
                    file_node.status = file_node.discovered_status
                    file_node.dl.reset(percent=0)
                    file_node.error = ""

    def activity_to_dict(self) -> dict[str, Any]:
        """Project aggregate activity counters and per-bucket file lists."""
        buckets: dict[str, list[dict[str, Any]]] = {
            "downloading": [],
            "queued": [],
            "finished": [],
            "failed": [],
        }
        skipped = 0
        for course_id, node in self._nodes.items():
            if course_id not in self._activity_course_ids:
                continue
            for f in node.iter_files():
                if f.status is RowStatus.SYNCED:
                    skipped += 1
                    continue
                base = {
                    "id": f.id,
                    "course_id": node.course_id,
                    "course_name": node.name,
                    "name": f.name,
                    "rel_path": f.rel_path,
                }
                if f.status is RowStatus.DOWNLOADING:
                    buckets["downloading"].append(
                        {
                            **base,
                            "downloaded": f.dl.downloaded,
                            "total": f.dl.total,
                            "percent": f.dl.percent,
                            "speed_bps": f.dl.speed_bps,
                        }
                    )
                elif f.status in {RowStatus.NEW, RowStatus.OUTDATED}:
                    buckets["queued"].append({**base, "status": str(f.status)})
                elif f.status is RowStatus.COMPLETED:
                    buckets["finished"].append(
                        {**base, "downloaded": f.dl.downloaded, "total": f.dl.total}
                    )
                elif f.status is RowStatus.FAILED:
                    buckets["failed"].append({**base, "error": f.error})
        completed = len(buckets["finished"])
        failed_count = len(buckets["failed"])
        total_pending = (
            len(buckets["downloading"])
            + len(buckets["queued"])
            + completed
            + failed_count
        )
        return {
            "total_pending": total_pending,
            "completed": completed,
            "skipped": skipped,
            "failed_count": failed_count,
            "percent": int((completed + failed_count) * 100 / total_pending)
            if total_pending
            else 0,
            **buckets,
        }

    def resolve_openable_file(self, file_id: str) -> Path | None:
        """Resolve ``file_id`` to a Path if done and contained in ``course_dir``."""
        for node in self._nodes.values():
            if node.course_dir is None:
                continue
            for file_node in node.iter_files():
                if file_node.id != file_id:
                    continue
                if not file_node.is_done:
                    return None
                base = node.course_dir.resolve()
                target = (node.course_dir / file_node.rel_path).resolve()
                return target if target.is_relative_to(base) else None
        return None

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
        rel_posix = rel.as_posix()
        folder_parts = rel.parts[:-1]
        file_node = _FileNode(
            id=f"file:{node.course_id}:{rel_posix}",
            name=rel.name,
            rel_path=rel_posix,
            status=status,
            discovered_status=status,
        )
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
