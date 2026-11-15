from collections.abc import Sequence
from pathlib import Path

from idat_sync.application.course_repository import CourseRepository
from idat_sync.application.download_manager import DownloadManager
from idat_sync.application.events import (
    CourseFilesDiscovered,
    CourseSyncCompleted,
    CourseSyncStarted,
    EventPublisher,
    noop_publisher,
)
from idat_sync.application.ports import (
    CredentialsExpiredError,
    FileSystemPort,
)
from idat_sync.application.task_orchestrator import TaskOrchestrator
from idat_sync.domain.content import Content
from idat_sync.domain.course import Course
from idat_sync.domain.path_utils import safe_filename
from idat_sync.domain.sync import SyncDiff


class IDATSync:
    def __init__(
        self,
        course_repository: CourseRepository,
        download_manager: DownloadManager,
        task_orchestrator: TaskOrchestrator,
        file_system: FileSystemPort,
        recordings_folder_name: str,
        publish: EventPublisher = noop_publisher,
    ) -> None:
        self.course_repository = course_repository
        self.download_manager = download_manager
        self.task_orchestrator = task_orchestrator
        self.file_system = file_system
        self.recordings_folder_name = recordings_folder_name
        self.publish = publish

    def list_courses(self) -> list[Course]:
        return self.course_repository.list_courses()

    def get_course_remote_tree(
        self, course: Course, course_dir: str | Path
    ) -> list[Content]:
        course_path = Path(course_dir)
        recordings_folder = course_path / self.recordings_folder_name
        remote_tree = self.course_repository.get_course_content(
            course, course_path
        )
        recordings_content = self.course_repository.get_recordings(
            course.name, recordings_folder
        )
        remote_tree.extend(recordings_content)
        return remote_tree

    def check_course_sync(
        self, course: Course, course_dir: str | Path
    ) -> SyncDiff:
        remote_tree = self.get_course_remote_tree(course, course_dir)
        return self.download_manager.diff_trees(course_dir, remote_tree)

    def sync_course(
        self,
        course: Course,
        course_dir: str | Path,
    ) -> SyncDiff:
        course_path = Path(course_dir)
        recordings_folder = course_path / self.recordings_folder_name
        self.publish(CourseSyncStarted(course))

        try:
            if not course.sections:
                self.course_repository.populate_course_sections(course)
            self.file_system.ensure_dir(course_path)
            self.file_system.ensure_dir(recordings_folder)

            diff = self.check_course_sync(course, course_path)
            tasks = self.download_manager.prepare_download_tasks(
                diff.pending_contents
            )
            self.publish(
                CourseFilesDiscovered(
                    course,
                    course_path,
                    tuple(sorted(diff.synced_paths)),
                    tuple(t.path for t in tasks),
                )
            )
            errors = self.task_orchestrator.run(tasks)
        except Exception as exc:
            self.publish(CourseSyncCompleted(course, exc))
            raise

        self.publish(CourseSyncCompleted(course, errors[0] if errors else None))
        return diff

    def sync_courses(
        self,
        root_folder: str | Path,
        courses: Sequence[Course],
    ) -> None:
        root_path = Path(root_folder)
        for course in courses:
            course_dir = root_path / safe_filename(course.name)
            try:
                self.sync_course(course, course_dir)
            except CredentialsExpiredError:
                raise
            except Exception:
                pass


__all__ = ["IDATSync"]
