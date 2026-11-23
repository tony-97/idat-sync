"""Pure-Python course selection and synchronization component controller."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

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
)
from idat_sync.application.ports import (
    Credentials,
    CredentialsExpiredError,
    IDATSyncError,
)
from idat_sync.bootstrap import create_sync_service
from idat_sync.domain.course import Course, get_course_term, get_in_progress_course_ids
from idat_sync.ui.base import AppFrame
from idat_sync.ui.course_tree import CourseTree, RowStatus
from idat_sync.ui.error_format import format_ui_error

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from idat_sync.application.sync_service import IDATSync
    from idat_sync.config import SyncConfig
    from idat_sync.ui.app import MainApp

_logger = logging.getLogger(__name__)


class SyncFrame(AppFrame):  # pylint: disable=too-many-instance-attributes
    """Component controller for selecting courses and monitoring sync progress."""

    def __init__(
        self,
        app: MainApp,
        credentials: Credentials,
        config: SyncConfig,
    ) -> None:
        """Initialize sync state and empty course tree."""
        super().__init__(app)
        self.credentials = credentials
        self.config = config
        self.idat_sync: IDATSync | None = None
        self.courses: list[Course] = []
        self.in_progress_ids: set[int] = set()
        self.selected_course_ids: set[int] = set()
        self.courses_loaded = False
        self.folder_path = ""
        self.status = "Loading courses..."
        self.error = ""
        self.auth_expired = False
        self.is_syncing = False
        self._cancel_requested = False
        self._sync_task: asyncio.Task[Any] | None = None
        self.course_tree = CourseTree()

    def _set_is_syncing(self, value: bool) -> None:  # noqa: FBT001
        self.is_syncing = value
        self._notify()

    @property
    def can_sync(self) -> bool:
        """Return True when idle and a target folder has been selected."""
        return not self.is_syncing and bool(self.folder_path.strip())

    def to_dict(self) -> dict[str, Any]:
        """Serialize sync view state for the WebView frontend."""
        return {
            "view": "sync",
            "folder_path": self.folder_path,
            "status": self.status,
            "error": self.error,
            "auth_expired": self.auth_expired,
            "courses_loaded": self.courses_loaded,
            "is_syncing": self.is_syncing,
            "can_sync": self.can_sync,
            "courses": self.course_tree.to_dict(),
            "activity": self.course_tree.activity_to_dict(),
        }

    def set_folder_path(self, path: str) -> None:
        """Set the target local sync directory path."""
        self.folder_path = path
        self._notify()

    def browse_folder(self) -> str:
        """Choose a sync directory via the host application's folder picker."""
        if chosen := self.app.pick_folder():
            self.set_folder_path(chosen)
            return chosen
        return self.folder_path

    def open_file(self, file_id: str) -> None:
        """Open a Synced/Completed file in the native OS default application."""
        path = self.course_tree.resolve_openable_file(file_id)
        if path is None:
            return
        try:
            if not path.is_file():
                raise FileNotFoundError(path)
            self.app.open_path(path)
        except OSError as exc:
            _logger.error("Failed to open file %s: %s", path, exc)  # noqa: TRY400
            self.error = "Could not open file"
            self._notify()

    def _refresh_selection(self) -> None:
        self.course_tree.refresh_selection(self.selected_course_ids)
        self._notify()

    def _populate_courses(self, courses: Sequence[Course]) -> None:
        self.in_progress_ids = get_in_progress_course_ids(courses)
        self.selected_course_ids = set(self.in_progress_ids)
        self.courses = self.course_tree.set_courses(
            courses, self.selected_course_ids, in_progress=self.in_progress_ids
        )
        self.courses_loaded = True
        self.error = ""
        self.auth_expired = False
        self.status = f"Loaded {len(self.courses)} courses."
        self._notify()

    def select_in_progress(self) -> None:
        """Select only courses belonging to the active academic term."""
        self.selected_course_ids = set(self.in_progress_ids)
        self._refresh_selection()

    def select_all(self) -> None:
        """Select all loaded courses."""
        self.selected_course_ids = {c.id for c in self.courses}
        self._refresh_selection()

    def clear_selection(self) -> None:
        """Clear all selected courses."""
        self.selected_course_ids.clear()
        self._refresh_selection()

    def toggle_course(self, course_id: int) -> None:
        """Toggle selection state for ``course_id``."""
        if course_id in self.selected_course_ids:
            self.selected_course_ids.remove(course_id)
        else:
            self.selected_course_ids.add(course_id)
        self._refresh_selection()

    def toggle_term(self, term: str) -> None:
        """Tri-state toggle all courses belonging to parsed ``term``."""
        term_ids = {c.id for c in self.courses if get_course_term(c) == term}
        if not term_ids:
            return
        if term_ids.issubset(self.selected_course_ids):
            self.selected_course_ids -= term_ids
        else:
            self.selected_course_ids |= term_ids
        self._refresh_selection()

    def cancel_sync(self) -> None:
        """Cancel the active sync task without closing ``self.idat_sync``."""
        task = self._sync_task
        if task is not None and not task.done():
            self._cancel_requested = True
            task.cancel()

    async def aclose(self) -> None:
        """Cancel active tasks and close the underlying IDATSync service."""
        await super().aclose()
        service, self.idat_sync = self.idat_sync, None
        if service is not None:
            await service.aclose()

    async def do_logout(self) -> None:
        """Close sync resources and return to LoginFrame."""
        if self.is_syncing:
            return
        try:
            await self.aclose()
        finally:
            self.app.switch_to_login()

    async def _init_sync_service(self) -> IDATSync:
        if self.idat_sync is None:
            self.idat_sync = await create_sync_service(self.credentials, self.config)
        if self.idat_sync is None:
            raise IDATSyncError("Sync service failed to initialize")
        if not self.courses_loaded:
            courses = await self.idat_sync.list_courses()
            self._populate_courses(courses)
        return self.idat_sync

    async def _ensure_sync_service(self) -> IDATSync:
        created = self.idat_sync is None
        try:
            return await self._init_sync_service()
        except IDATSyncError as exc:
            _logger.error("Sync setup failed: %s", exc)  # noqa: TRY400
            if created:
                await self.aclose()
            raise
        except asyncio.CancelledError:
            if created:
                await self.aclose()
            raise

    def _with_log_hint(self, message: str) -> str:
        return f"{message}. Check {self.config.log_file.name} for details."

    def _set_error_status(self, exc: IDATSyncError) -> None:
        summary = format_ui_error(exc)
        self.auth_expired = isinstance(exc, CredentialsExpiredError)
        if self.auth_expired:
            self.error = summary
            self.status = "Credentials expired. Sign in again."
        else:
            self.error = self._with_log_hint(summary)
            self.status = f"Sync error: {summary}"
        self._notify()

    async def load_idat_sync(self, credentials: Credentials) -> None:
        """Initialize the sync service and load the user's course list."""
        self.credentials = credentials
        self.error = ""
        self.auth_expired = False
        async with self._busy(self._set_is_syncing):
            try:
                await self._ensure_sync_service()
            except IDATSyncError as exc:
                await self.aclose()
                self.courses_loaded = False
                self._set_error_status(exc)

    async def retry_load(self) -> None:
        """Retry startup initialization after a load error."""
        if self.courses_loaded or self.is_syncing:
            return
        await self.load_idat_sync(self.credentials)

    def _on_course_started(self, course: Course, course_dir: Path) -> None:
        self.course_tree.start_course(course.id, course_dir)
        self.status = f"Fetching contents for {course.name}..."
        self._notify()

    def _on_file_discovered(self, event: CourseFileDiscovered) -> None:
        self.course_tree.add_file(event.course.id, event.path, event.state)
        self._notify()

    def _on_discovery_completed(self, course: Course) -> None:
        self.course_tree.finish_discovery(course.id)
        self._notify()

    def _on_download_started(self, path: Path) -> None:
        self.course_tree.set_file_status(path, RowStatus.DOWNLOADING)
        self.status = f"Downloading {path.name}..."
        self._notify()

    def _on_download_progress(self, event: TaskDownloadProgress) -> None:
        payload = self.course_tree.set_file_progress(
            event.path, event.downloaded, event.total
        )
        if payload is not None and self.app is not None:
            self.app.push_progress(payload)

    def _on_download_completed(self, path: Path) -> None:
        self.course_tree.set_file_status(path, RowStatus.COMPLETED)
        self._notify()

    def _on_download_failed(self, path: Path, error: IDATSyncError) -> None:
        self.course_tree.set_file_status(
            path, RowStatus.FAILED, error=format_ui_error(error)
        )
        self._notify()

    def _on_course_completed(self, course: Course, error: IDATSyncError | None) -> None:
        status = RowStatus.COMPLETED if error is None else RowStatus.FAILED
        self.course_tree.set_course_status(course.id, status)
        self._notify()

    def _handle_event(self, event: SyncEvent) -> None:
        match event:
            case CourseSyncStarted(course=course, course_dir=course_dir):
                self._on_course_started(course, course_dir)
            case CourseFileDiscovered():
                self._on_file_discovered(event)
            case CourseDiscoveryCompleted(course=course):
                self._on_discovery_completed(course)
            case TaskDownloadStarted(path=path):
                self._on_download_started(path)
            case TaskDownloadProgress():
                self._on_download_progress(event)
            case TaskDownloadCompleted(path=path):
                self._on_download_completed(path)
            case TaskDownloadFailed(path=path, error=error):
                self._on_download_failed(path, error)
            case CourseSyncCompleted(course=course, error=error):
                self._on_course_completed(course, error)

    def _resolve_targets(self, target_ids: set[int] | None) -> list[Course]:
        if target_ids is not None:
            return [c for c in self.courses if c.id in target_ids]
        if self.selected_course_ids:
            return [c for c in self.courses if c.id in self.selected_course_ids]
        if self.in_progress_ids:
            return [c for c in self.courses if c.id in self.in_progress_ids]
        return list(self.courses)

    def _prepare_sync_run(self, targets: Sequence[Course]) -> None:
        self.error = ""
        self.auth_expired = False
        self.status = "Starting sync..."
        self.course_tree.reset_for_sync([c.id for c in targets])

    async def _run_sync(
        self,
        path: str,
        target_ids: set[int] | None,
        targets: list[Course] | None,
    ) -> None:
        idat_sync = await self._ensure_sync_service()
        if targets is None:
            targets = self._resolve_targets(target_ids)
            if not targets:
                return
            self._prepare_sync_run(targets)
            self._notify()

        first_error: IDATSyncError | None = None
        async for event in idat_sync.sync_courses(path, targets, refresh=True):
            if (
                isinstance(event, CourseSyncCompleted)
                and event.error is not None
                and first_error is None
            ):
                first_error = event.error
            self._handle_event(event)
        if first_error is not None:
            failed_count = self.course_tree.activity_to_dict()["failed_count"]
            if failed_count > 0:
                noun = "file" if failed_count == 1 else "files"
                self.error = self._with_log_hint(
                    f"{failed_count} {noun} failed to download"
                )
            else:
                self.error = self._with_log_hint(format_ui_error(first_error))
            self.status = "Sync completed with errors."
        else:
            self.error = ""
            self.status = "Sync done."
        self._notify()

    async def _execute_sync(self, target_ids: set[int] | None) -> None:
        path = self.folder_path.strip()
        if not path or self.is_syncing:
            return
        targets: list[Course] | None = None
        if self.courses_loaded:
            targets = self._resolve_targets(target_ids)
            if not targets:
                return
            self._prepare_sync_run(targets)
        else:
            self.error = ""
            self.auth_expired = False
            self.status = "Loading courses..."
        self._cancel_requested = False
        async with self._busy(self._set_is_syncing):
            self._sync_task = asyncio.current_task()
            try:
                await self._run_sync(path, target_ids, targets)
            except asyncio.CancelledError:
                if not self._cancel_requested:
                    raise
                self.course_tree.cancel_sync()
                self.status = "Sync cancelled."
                self._notify()
            except IDATSyncError as exc:
                self._set_error_status(exc)
            finally:
                self._sync_task = None
                self._cancel_requested = False

    async def do_sync(self) -> None:
        """Run synchronization for selected courses (falling back to active/all)."""
        await self._execute_sync(None)

    async def sync_course(self, course_id: int) -> None:
        """Run synchronization for a single ``course_id`` on demand."""
        await self._execute_sync({course_id})
