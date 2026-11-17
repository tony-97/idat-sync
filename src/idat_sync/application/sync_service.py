"""Course synchronization application service."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Self

from idat_sync.application.course_repository import CourseRepository
from idat_sync.application.download_manager import DownloadManager
from idat_sync.application.events import (
    CourseFilesDiscovered,
    CourseSyncCompleted,
    CourseSyncStarted,
    SyncEvent,
    TaskDownloadCompleted,
    TaskDownloadFailed,
    TaskDownloadStarted,
    log_sync_event,
)
from idat_sync.application.ports import (
    CredentialsExpiredError,
    DownloadTask,
    FileSystemPort,
)
from idat_sync.application.task_orchestrator import TaskOrchestrator
from idat_sync.domain.content import Content
from idat_sync.domain.course import Course
from idat_sync.domain.path_utils import (
    safe_filename,
    safe_suffixed_segment,
    split_extension,
)
from idat_sync.domain.sync import SyncDiff


def _emit(event: SyncEvent) -> SyncEvent:
    log_sync_event(event)
    return event


def _disambiguate_paths(items: Sequence[Content]) -> list[Content]:
    seen_keys: set[str] = set()
    result: list[Content] = []
    for item in items:
        candidate = item.path
        key = candidate.as_posix().casefold()
        resolved = item
        if key in seen_keys:
            stem, ext = split_extension(item.path.name)
            counter = 2
            while key in seen_keys:
                new_name = safe_suffixed_segment(stem, f" ({counter}){ext}")
                candidate = item.path.with_name(new_name)
                key = candidate.as_posix().casefold()
                counter += 1
            resolved = replace(item, path=candidate)
        seen_keys.add(key)
        result.append(resolved)
    return result


class IDATSync:
    """Application service coordinating course discovery, diffing, and downloads."""

    def __init__(  # noqa: PLR0913  # pylint: disable=too-many-arguments
        self,
        course_repository: CourseRepository,
        download_manager: DownloadManager,
        task_orchestrator: TaskOrchestrator,
        file_system: FileSystemPort,
        recordings_folder_name: str,
        *,
        closers: Sequence[Callable[[], Awaitable[None]]] = (),
    ) -> None:
        """Initialize the sync service with its repository, managers, and closers."""
        self.course_repository = course_repository
        self.download_manager = download_manager
        self.task_orchestrator = task_orchestrator
        self.file_system = file_system
        self.recordings_folder_name = recordings_folder_name
        self._closers = list(closers)

    async def aclose(self) -> None:
        """Close all registered async resources, raising the first error if any."""
        closers, self._closers = self._closers, []
        first_error: BaseException | None = None
        for closer in closers:
            try:
                await closer()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    async def __aenter__(self) -> Self:
        """Enter the async context manager."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit the async context manager and close resources."""
        await self.aclose()

    async def list_courses(self) -> list[Course]:
        """Return all enrolled courses."""
        return await self.course_repository.list_courses()

    async def get_course_remote_tree(
        self, course: Course, course_dir: str | Path
    ) -> list[Content]:
        """Collect and disambiguate all remote Content items for ``course``."""
        course_path = Path(course_dir)
        recordings_folder = course_path / self.recordings_folder_name
        remote_tree = list(
            await self.course_repository.get_course_content(course, course_path)
        )
        recordings_content = await self.course_repository.get_recordings(
            course.name, recordings_folder
        )
        remote_tree.extend(recordings_content)
        return _disambiguate_paths(remote_tree)

    async def check_course_sync(
        self, course: Course, course_dir: str | Path
    ) -> SyncDiff:
        """Diff remote course contents against local files in ``course_dir``."""
        remote_tree = await self.get_course_remote_tree(course, course_dir)
        return self.download_manager.diff_trees(course_dir, remote_tree)

    async def _execute_tasks(
        self,
        tasks: Sequence[DownloadTask],
        errors: list[Exception],
    ) -> AsyncIterator[SyncEvent]:
        event_queue: asyncio.Queue[SyncEvent | None] = asyncio.Queue()

        async def _run_task(task: DownloadTask) -> None:
            await event_queue.put(TaskDownloadStarted(task.path))
            try:
                await task.download()
            except CredentialsExpiredError as exc:
                await event_queue.put(TaskDownloadFailed(task.path, exc))
                raise
            except Exception as exc:
                errors.append(exc)
                await event_queue.put(TaskDownloadFailed(task.path, exc))
            else:
                await event_queue.put(TaskDownloadCompleted(task.path))

        async def _run_all() -> None:
            try:
                async with asyncio.TaskGroup() as tg:
                    for coro in self.task_orchestrator.create_coroutines(
                        tasks, _run_task
                    ):
                        tg.create_task(coro)
            finally:
                await event_queue.put(None)

        runner = asyncio.create_task(_run_all())
        try:
            try:
                while (event := await event_queue.get()) is not None:
                    yield _emit(event)
                await runner
            except* CredentialsExpiredError as eg:
                raise eg.exceptions[0] from eg
        finally:
            if not runner.done():
                runner.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runner

    async def sync_course(
        self,
        course: Course,
        course_dir: str | Path,
    ) -> AsyncIterator[SyncEvent]:
        """Synchronize a single ``course`` into ``course_dir``, yielding SyncEvents."""
        course_path = Path(course_dir)
        recordings_folder = course_path / self.recordings_folder_name
        yield _emit(CourseSyncStarted(course))
        errors: list[Exception] = []

        try:
            if not course.sections:
                await self.course_repository.populate_course_sections(course)
            self.file_system.ensure_dir(course_path)
            self.file_system.ensure_dir(recordings_folder)

            diff = await self.check_course_sync(course, course_path)
            tasks = self.download_manager.prepare_download_tasks(diff.pending_contents)
            yield _emit(
                CourseFilesDiscovered(
                    course,
                    course_path,
                    tuple(sorted(diff.synced_paths)),
                    tuple(t.path for t in tasks),
                )
            )
            async for event in self._execute_tasks(tasks, errors):
                yield event
        except Exception as exc:
            yield _emit(CourseSyncCompleted(course, exc))
            raise

        yield _emit(CourseSyncCompleted(course, errors[0] if errors else None))

    async def sync_courses(
        self,
        root_folder: str | Path,
        courses: Sequence[Course],
    ) -> AsyncIterator[SyncEvent]:
        """Synchronize all ``courses`` under ``root_folder``, yielding SyncEvents."""
        self.course_repository.invalidate_cache()
        root_path = Path(root_folder)
        for course in courses:
            course_dir = root_path / safe_filename(course.name)
            try:
                async for event in self.sync_course(course, course_dir):
                    yield event
            except CredentialsExpiredError:
                raise
            except Exception:  # noqa: S110  # Logged via CourseSyncCompleted
                pass


__all__ = ["IDATSync"]
