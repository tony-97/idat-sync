"""Course synchronization application service."""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Self

from idat_sync.application.course_repository import CourseRepository
from idat_sync.application.download_manager import DownloadManager
from idat_sync.application.events import (
    CourseDiscoveryCompleted,
    CourseFileDiscovered,
    CourseSyncCompleted,
    CourseSyncStarted,
    SyncEvent,
    TaskDownloadCompleted,
    TaskDownloadFailed,
    TaskDownloadProgress,
    TaskDownloadStarted,
    log_sync_event,
)
from idat_sync.application.ports import (
    CredentialsExpiredError,
    Downloader,
    DownloadTask,
    FileSystemError,
    FileSystemPort,
    IDATSyncError,
    RemoteServiceError,
)
from idat_sync.application.task_orchestrator import TaskChannel
from idat_sync.domain.content import Content
from idat_sync.domain.course import Course
from idat_sync.domain.path_utils import PathDisambiguator, safe_filename
from idat_sync.domain.sync import ContentSyncState

_logger = logging.getLogger("idat_sync")


def _emit(event: SyncEvent) -> SyncEvent:
    log_sync_event(event)
    return event


def _first_credentials_expired(exc: BaseException) -> CredentialsExpiredError | None:
    if isinstance(exc, CredentialsExpiredError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            if (found := _first_credentials_expired(sub)) is not None:
                return found
    return None


class _CourseStreamRun:  # pylint: disable=too-few-public-methods,too-many-instance-attributes
    """Per-course streaming state shared between discovery tasks and group workers."""

    def __init__(
        self,
        sync: "IDATSync",
        course: Course,
        course_path: Path,
        local_mtimes: Mapping[Path, int],
        errors: list[IDATSyncError],
    ) -> None:
        self._sync = sync
        self.course = course
        self.course_path = course_path
        self.recordings_folder = course_path / sync.recordings_folder_name
        self.local_mtimes = local_mtimes
        self.errors = errors
        self.channel = TaskChannel(sync.downloader_groups)
        self.event_queue: asyncio.Queue[SyncEvent | None] = asyncio.Queue()
        self._disambiguator = PathDisambiguator()
        self._synced_count = 0
        self._pending_count = 0

    def _on_item(self, item: Content) -> None:
        new_path = self._disambiguator.disambiguate(item.path)
        if new_path != item.path:
            item = replace(item, path=new_path)
        task = self._sync.download_manager.create_download_task(item)
        if task is None:
            return
        state = self._sync.download_manager.classify_content(item, self.local_mtimes)
        self.event_queue.put_nowait(CourseFileDiscovered(self.course, item.path, state))
        if state is not ContentSyncState.SYNCED:
            self._pending_count += 1
            self.channel.enqueue(task)
        else:
            self._synced_count += 1

    async def _consume_content(self) -> None:
        async for item in self._sync.course_repository.stream_course_content(
            self.course, self.course_path
        ):
            self._on_item(item)

    async def _consume_recordings(self) -> None:
        try:
            recordings = await self._sync.course_repository.get_recordings(
                self.course.name, self.recordings_folder
            )
        except RemoteServiceError as exc:
            _logger.exception("Failed fetching recordings for '%s'", self.course.name)
            self.errors.append(exc)
            return
        for recording in recordings:
            self._on_item(recording)

    async def _discover(self) -> None:
        try:
            async with asyncio.TaskGroup() as discovery_tg:
                discovery_tg.create_task(self._consume_content())
                discovery_tg.create_task(self._consume_recordings())
            self.event_queue.put_nowait(
                CourseDiscoveryCompleted(
                    self.course, self._synced_count, self._pending_count
                )
            )
        finally:
            self.channel.close()

    async def _run_single_task(self, task: DownloadTask) -> None:
        self.event_queue.put_nowait(TaskDownloadStarted(task.path))
        finished = False
        last_emit: float | None = None
        interval = self._sync.progress_interval

        def _on_progress(downloaded: int, total: int | None) -> None:
            nonlocal last_emit
            if finished:
                return
            now = time.monotonic()
            if last_emit is not None and now - last_emit < interval:
                return
            last_emit = now
            self.event_queue.put_nowait(
                TaskDownloadProgress(task.path, downloaded, total)
            )

        try:
            await task.download(on_progress=_on_progress)
        except CredentialsExpiredError as exc:
            finished = True
            self.event_queue.put_nowait(TaskDownloadFailed(task.path, exc))
            raise
        except (RemoteServiceError, FileSystemError) as exc:
            finished = True
            self.errors.append(exc)
            self.event_queue.put_nowait(TaskDownloadFailed(task.path, exc))
        else:
            finished = True
            self.event_queue.put_nowait(TaskDownloadCompleted(task.path))
        finally:
            finished = True

    async def run(self) -> None:
        """Execute discovery and all group workers concurrently until completion."""
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._discover())
                for worker in self.channel.create_workers(self._run_single_task):
                    tg.create_task(worker)
        finally:
            self.event_queue.put_nowait(None)


class IDATSync:
    """Application service coordinating streaming course discovery and downloads."""

    def __init__(  # noqa: PLR0913  # pylint: disable=too-many-arguments
        self,
        course_repository: CourseRepository,
        download_manager: DownloadManager,
        downloader_groups: Sequence[Sequence[Downloader]],
        file_system: FileSystemPort,
        recordings_folder_name: str,
        *,
        closers: Sequence[Callable[[], Awaitable[None]]] = (),
        progress_interval: float = 0.15,
    ) -> None:
        """Initialize the sync service with its repository, managers, and closers."""
        self.course_repository = course_repository
        self.download_manager = download_manager
        self.downloader_groups = downloader_groups
        self.file_system = file_system
        self.recordings_folder_name = recordings_folder_name
        self._closers = list(closers)
        self.progress_interval = progress_interval

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

    async def _stream_course(
        self,
        course: Course,
        course_path: Path,
        local_mtimes: Mapping[Path, int],
        errors: list[IDATSyncError],
    ) -> AsyncIterator[SyncEvent]:
        run = _CourseStreamRun(self, course, course_path, local_mtimes, errors)
        runner = asyncio.create_task(run.run())
        try:
            cred_error: CredentialsExpiredError | None = None
            try:
                while (event := await run.event_queue.get()) is not None:
                    yield _emit(event)
                await runner
            except* CredentialsExpiredError as eg:
                cred_error = _first_credentials_expired(eg)
            if cred_error is not None:
                raise cred_error
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
        yield _emit(CourseSyncStarted(course, course_path))
        errors: list[IDATSyncError] = []

        try:
            if not course.sections:
                await self.course_repository.populate_course_sections(course)
            self.file_system.ensure_dir(course_path)
            self.file_system.ensure_dir(recordings_folder)
            local_mtimes = self.file_system.scan_mtimes(course_path)

            async for event in self._stream_course(
                course, course_path, local_mtimes, errors
            ):
                yield event
        except IDATSyncError as exc:
            yield _emit(CourseSyncCompleted(course, exc))
            raise

        yield _emit(CourseSyncCompleted(course, errors[0] if errors else None))

    async def sync_courses(
        self,
        root_folder: str | Path,
        courses: Sequence[Course],
        *,
        refresh: bool = False,
    ) -> AsyncIterator[SyncEvent]:
        """Synchronize all ``courses`` under ``root_folder``, yielding SyncEvents."""
        self.course_repository.invalidate_cache()
        root_path = Path(root_folder)
        for course in courses:
            if refresh:
                course.sections.clear()
            course_dir = root_path / safe_filename(course.name)
            try:
                async for event in self.sync_course(course, course_dir):
                    yield event
            except CredentialsExpiredError:
                raise
            except IDATSyncError:  # Logged via CourseSyncCompleted
                pass


__all__ = ["IDATSync"]
