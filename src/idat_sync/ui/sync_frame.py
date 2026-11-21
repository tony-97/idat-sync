"""Pure-Python course selection and synchronization component controller."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from idat_sync.application.events import (
    CourseFilesDiscovered,
    CourseSyncCompleted,
    CourseSyncStarted,
    SyncEvent,
    TaskDownloadCompleted,
    TaskDownloadFailed,
    TaskDownloadStarted,
)
from idat_sync.application.ports import (
    Credentials,
    CredentialsExpiredError,
    IDATSyncError,
)
from idat_sync.bootstrap import create_sync_service
from idat_sync.domain.course import Course, get_in_progress_course_ids
from idat_sync.ui.base import AppFrame
from idat_sync.ui.course_tree import CourseTree, RowStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

    from idat_sync.application.sync_service import IDATSync
    from idat_sync.config import SyncConfig
    from idat_sync.ui.app import MainApp


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
        self.is_syncing = False
        self.course_tree = CourseTree()

    def _set_is_syncing(self, value: bool) -> None:  # noqa: FBT001
        self.is_syncing = value
        self._notify()

    @property
    def can_sync(self) -> bool:
        """Return True when idle, folder is set, and sync can proceed."""
        return (
            not self.is_syncing
            and bool(self.folder_path.strip())
            and (bool(self.selected_course_ids) or not self.courses_loaded)
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize sync view state for the WebView frontend."""
        return {
            "view": "sync",
            "folder_path": self.folder_path,
            "status": self.status,
            "is_syncing": self.is_syncing,
            "can_sync": self.can_sync,
            "courses": self.course_tree.to_dict(),
        }

    def set_folder_path(self, path: str) -> None:
        """Set the target local sync directory path."""
        self.folder_path = path
        self._notify()

    def browse_folder(self) -> str:
        """Choose a sync directory via desktop dialog or Android Downloads fallback."""
        selection = self.app.pick_folder()
        if selection.available:
            if selection.path:
                self.set_folder_path(selection.path)
                return selection.path
            return self.folder_path

        default_dir = str(
            Path(os.environ.get("EXTERNAL_STORAGE", "/storage/emulated/0"))
            / "Download"
            / "idat-sync"
        )
        self.set_folder_path(default_dir)
        return default_dir

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

    async def _ensure_sync_service(self) -> IDATSync:
        created = self.idat_sync is None
        if created:
            self.idat_sync = await create_sync_service(self.credentials, self.config)
        if self.idat_sync is None:
            raise IDATSyncError("Sync service failed to initialize")
        try:
            if not self.courses_loaded:
                courses = await self.idat_sync.list_courses()
                self._populate_courses(courses)
        except IDATSyncError:
            if created:
                await self.aclose()
            raise
        return self.idat_sync

    def _set_error_status(self, exc: IDATSyncError) -> None:
        if isinstance(exc, CredentialsExpiredError):
            self.status = f"Credentials expired: {exc}. Click Logout to sign in again."
        else:
            self.status = f"Sync error: {exc}"
        self._notify()

    async def load_idat_sync(self, credentials: Credentials) -> None:
        """Initialize the sync service and load the user's course list."""
        self.credentials = credentials
        async with self._busy(self._set_is_syncing):
            try:
                await self._ensure_sync_service()
            except IDATSyncError as exc:
                await self.aclose()
                self.courses_loaded = False
                self._set_error_status(exc)

    def _on_course_started(self, course: Course) -> None:
        self.course_tree.set_course_status(course.id, RowStatus.FETCHING)
        self.status = f"Fetching contents for {course.name}..."
        self._notify()

    def _on_files_discovered(self, event: CourseFilesDiscovered) -> None:
        self.course_tree.show_files(
            event.course.id,
            event.course_dir,
            event.synced_paths,
            event.pending_paths,
        )
        self._notify()

    def _on_download_started(self, path: Path) -> None:
        self.course_tree.set_file_status(path, RowStatus.DOWNLOADING)
        self.status = f"Downloading {path.name}..."
        self._notify()

    def _on_download_completed(self, path: Path) -> None:
        self.course_tree.set_file_status(path, RowStatus.COMPLETED)
        self._notify()

    def _on_download_failed(self, path: Path) -> None:
        self.course_tree.set_file_status(path, RowStatus.FAILED)
        self._notify()

    def _on_course_completed(self, course: Course, error: IDATSyncError | None) -> None:
        status = RowStatus.COMPLETED if error is None else RowStatus.FAILED
        self.course_tree.set_course_status(course.id, status)
        self._notify()

    def _handle_event(self, event: SyncEvent) -> None:
        match event:
            case CourseSyncStarted(course=course):
                self._on_course_started(course)
            case CourseFilesDiscovered():
                self._on_files_discovered(event)
            case TaskDownloadStarted(path=path):
                self._on_download_started(path)
            case TaskDownloadCompleted(path=path):
                self._on_download_completed(path)
            case TaskDownloadFailed(path=path):
                self._on_download_failed(path)
            case CourseSyncCompleted(course=course, error=error):
                self._on_course_completed(course, error)

    async def _run_sync(self, path: str) -> None:
        idat_sync = await self._ensure_sync_service()
        selected = [c for c in self.courses if c.id in self.selected_course_ids]
        if not selected:
            return
        self.course_tree.reset_for_sync([c.id for c in selected])
        self._notify()

        has_errors = False
        async for event in idat_sync.sync_courses(path, selected, refresh=True):
            if isinstance(event, CourseSyncCompleted) and event.error is not None:
                has_errors = True
            self._handle_event(event)
        self.status = "Sync completed with errors." if has_errors else "Sync done."
        self._notify()

    async def do_sync(self) -> None:
        """Run synchronization for all selected courses."""
        path = self.folder_path.strip()
        if not path or self.is_syncing:
            return
        async with self._busy(self._set_is_syncing):
            try:
                await self._run_sync(path)
            except IDATSyncError as exc:
                self._set_error_status(exc)
