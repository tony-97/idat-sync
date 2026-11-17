"""Immutable synchronization lifecycle events and logging subscriber."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from idat_sync.domain.course import Course

_logger = logging.getLogger("idat_sync")


@dataclass(frozen=True)
class CourseSyncStarted:
    """Emitted when synchronization begins for a course."""

    course: Course


@dataclass(frozen=True)
class CourseFilesDiscovered:
    """Emitted after diffing remote contents against local files for a course."""

    course: Course
    course_dir: Path
    synced_paths: tuple[Path, ...]
    pending_paths: tuple[Path, ...]


@dataclass(frozen=True)
class TaskDownloadStarted:
    """Emitted before downloading a single content item."""

    path: Path


@dataclass(frozen=True)
class TaskDownloadCompleted:
    """Emitted after a single content item finishes downloading."""

    path: Path


@dataclass(frozen=True)
class TaskDownloadFailed:
    """Emitted when downloading a single content item raises an error."""

    path: Path
    error: Exception


@dataclass(frozen=True)
class CourseSyncCompleted:
    """Emitted when synchronization finishes for a course."""

    course: Course
    error: Exception | None = None


SyncEvent = (
    CourseSyncStarted
    | CourseFilesDiscovered
    | TaskDownloadStarted
    | TaskDownloadCompleted
    | TaskDownloadFailed
    | CourseSyncCompleted
)


def log_sync_event(event: SyncEvent) -> None:
    """Log a sync lifecycle event to the application logger."""
    match event:
        case CourseSyncStarted(course=course):
            _logger.info("Syncing course '%s'...", course.name)
        case CourseFilesDiscovered(
            course=course, synced_paths=synced, pending_paths=pending
        ):
            _logger.info(
                "Course '%s': %d synced, %d pending.",
                course.name,
                len(synced),
                len(pending),
            )
        case TaskDownloadStarted(path=path):
            _logger.info("Downloading %s", path)
        case TaskDownloadCompleted(path=path):
            _logger.info("Downloaded %s", path)
        case TaskDownloadFailed(path=path, error=error):
            _logger.error("Failed downloading %s: %s", path, error)
        case CourseSyncCompleted(course=course, error=None):
            _logger.info("Completed course '%s'.", course.name)
        case CourseSyncCompleted(course=course, error=error):
            _logger.error("Failed syncing course '%s': %s", course.name, error)


__all__ = [
    "CourseFilesDiscovered",
    "CourseSyncCompleted",
    "CourseSyncStarted",
    "SyncEvent",
    "TaskDownloadCompleted",
    "TaskDownloadFailed",
    "TaskDownloadStarted",
    "log_sync_event",
]
