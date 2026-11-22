"""Immutable synchronization lifecycle events and logging subscriber."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from idat_sync.application.exceptions import IDATSyncError
    from idat_sync.domain.course import Course

_logger = logging.getLogger("idat_sync")


@dataclass(frozen=True)
class CourseSyncStarted:
    """Emitted when synchronization begins for a course."""

    course: Course
    course_dir: Path


@dataclass(frozen=True)
class CourseFileDiscovered:
    """Emitted when a single downloadable file is discovered for a course."""

    course: Course
    path: Path
    is_pending: bool


@dataclass(frozen=True)
class CourseDiscoveryCompleted:
    """Emitted once all content and recording sources finish discovery."""

    course: Course
    synced_count: int
    pending_count: int


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
    error: IDATSyncError


@dataclass(frozen=True)
class CourseSyncCompleted:
    """Emitted when synchronization finishes for a course."""

    course: Course
    error: IDATSyncError | None = None


SyncEvent = (
    CourseSyncStarted
    | CourseFileDiscovered
    | CourseDiscoveryCompleted
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
        case CourseFileDiscovered(course=course, path=path, is_pending=is_pending):
            _logger.debug(
                "Course '%s': discovered %s (%s)",
                course.name,
                path,
                "pending" if is_pending else "synced",
            )
        case CourseDiscoveryCompleted(
            course=course, synced_count=synced_count, pending_count=pending_count
        ):
            _logger.info(
                "Course '%s': %d synced, %d pending.",
                course.name,
                synced_count,
                pending_count,
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
    "CourseDiscoveryCompleted",
    "CourseFileDiscovered",
    "CourseSyncCompleted",
    "CourseSyncStarted",
    "SyncEvent",
    "TaskDownloadCompleted",
    "TaskDownloadFailed",
    "TaskDownloadStarted",
    "log_sync_event",
]
