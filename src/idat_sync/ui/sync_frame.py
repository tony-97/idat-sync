"""Course selection and synchronization progress UI frame."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import TYPE_CHECKING, Any

from async_tkinter_loop import async_handler

from idat_sync.application.events import (
    CourseFilesDiscovered,
    CourseSyncCompleted,
    CourseSyncStarted,
    SyncEvent,
    TaskDownloadCompleted,
    TaskDownloadFailed,
    TaskDownloadStarted,
)
from idat_sync.application.ports import Credentials, CredentialsExpiredError
from idat_sync.bootstrap import create_sync_service
from idat_sync.domain.course import Course, get_in_progress_course_ids
from idat_sync.ui.base import AppFrame
from idat_sync.ui.course_tree import CourseTree, RowStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from idat_sync.application.sync_service import IDATSync
    from idat_sync.config import SyncConfig
    from idat_sync.ui.app import MainApp

_WINDOW_WIDTH = 700
_WINDOW_HEIGHT = 500


class SyncFrame(AppFrame):  # pylint: disable=too-many-ancestors,too-many-instance-attributes
    """Tkinter frame for selecting courses and monitoring synchronization."""

    def __init__(
        self,
        master: MainApp,
        credentials: Credentials,
        config: SyncConfig,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        """Initialize the sync frame UI and schedule course loading."""
        super().__init__(master, **kwargs)
        self.app = master
        self.credentials = credentials
        self.config = config
        self.app.center_window(_WINDOW_WIDTH, _WINDOW_HEIGHT)
        self.app.title("IDAT Sync")

        self.idat_sync: IDATSync | None = None
        self.courses: list[Course] = []
        self.in_progress_ids: set[int] = set()
        self.selected_course_ids: set[int] = set()
        self.courses_loaded = False

        self.folder_path = tk.StringVar()
        self.status_var = tk.StringVar(value="Loading courses...")
        self.is_syncing = tk.BooleanVar(value=False)

        self._build_folder_bar()
        self._build_toolbar()
        self._build_tree()
        self._build_actions()
        self._build_status()
        self._lockable = [
            self.browse_button,
            self.logout_button,
            self.in_progress_button,
            self.select_all_button,
            self.clear_button,
        ]

        self.is_syncing.trace_add("write", self._update_buttons)
        self.folder_path.trace_add("write", self._update_buttons)

        self.after_idle(
            lambda: self.app.loop.create_task(self.load_idat_sync(credentials))
        )

    @staticmethod
    def _button(
        parent: tk.Misc,
        text: str,
        command: Callable[[], object],
        **pack: Any,  # noqa: ANN401
    ) -> tk.Button:
        btn = tk.Button(parent, text=text, command=command)
        btn.pack(**pack)
        return btn

    def _build_folder_bar(self) -> None:
        frame = tk.Frame(self, padx=10, pady=10)
        frame.pack(fill=tk.X)
        tk.Label(frame, text="Sync Folder:").pack(side=tk.LEFT, padx=(0, 5))
        tk.Entry(frame, textvariable=self.folder_path, state="readonly").pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        self.browse_button = self._button(
            frame, "Browse...", self.browse_folder, side=tk.LEFT, padx=(5, 0)
        )

    def _build_toolbar(self) -> None:
        frame = tk.Frame(self, padx=10)
        frame.pack(fill=tk.X, pady=(0, 5))
        tk.Label(frame, text="Courses:").pack(side=tk.LEFT)
        self.in_progress_button = self._button(
            frame, "In Progress", self.select_in_progress, side=tk.LEFT, padx=(5, 0)
        )
        self.select_all_button = self._button(
            frame, "Select All", self.select_all, side=tk.LEFT, padx=(5, 0)
        )
        self.clear_button = self._button(
            frame, "Clear", self.clear_selection, side=tk.LEFT, padx=(5, 0)
        )

    def _build_tree(self) -> None:
        tree_frame = tk.Frame(self, padx=10)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.course_tree = CourseTree(tree_frame, on_toggle=self.toggle_course)
        self.course_tree.pack(fill=tk.BOTH, expand=True)

    def _build_actions(self) -> None:
        frame = tk.Frame(self, padx=10)
        frame.pack(fill=tk.X, pady=(10, 5))
        self.logout_button = self._button(
            frame, "Logout", async_handler(self.do_logout), side=tk.LEFT
        )
        self.sync_button = self._button(
            frame, "Sync", async_handler(self.do_sync), side=tk.RIGHT
        )
        self.sync_button.config(state="disabled")

    def _build_status(self) -> None:
        tk.Label(self, textvariable=self.status_var, anchor="w", padx=10, pady=5).pack(
            fill=tk.X
        )

    def _update_buttons(self, *_: object) -> None:
        if not self.winfo_exists():
            return
        busy = self.is_syncing.get()
        state = "disabled" if busy else "normal"
        for btn in self._lockable:
            btn.config(state=state)
        self.course_tree.set_interactive(enabled=not busy)
        can_sync = (
            not busy
            and bool(self.folder_path.get().strip())
            and (bool(self.selected_course_ids) or not self.courses_loaded)
        )
        self.sync_button.config(state="normal" if can_sync else "disabled")

    def _refresh_selection(self) -> None:
        self.course_tree.refresh_selection(self.selected_course_ids)
        self._update_buttons()

    def _populate_courses(self, courses: Sequence[Course]) -> None:
        self.in_progress_ids = get_in_progress_course_ids(courses)
        self.courses = [c for c in courses if c.id in self.in_progress_ids] + [
            c for c in courses if c.id not in self.in_progress_ids
        ]
        self.selected_course_ids = set(self.in_progress_ids)
        self.courses_loaded = True
        self.course_tree.set_courses(self.courses, self.selected_course_ids)
        self.status_var.set(f"Loaded {len(self.courses)} courses.")
        self._update_buttons()

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
        if self.is_syncing.get():
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
            raise RuntimeError("Sync service failed to initialize")
        try:
            if not self.courses_loaded:
                courses = await self.idat_sync.list_courses()
                self._populate_courses(courses)
        except Exception:
            if created:
                await self.aclose()
            raise
        return self.idat_sync

    def _set_error_status(self, exc: Exception) -> None:
        if isinstance(exc, CredentialsExpiredError):
            msg = f"Credentials expired: {exc}. Click Logout to sign in again."
        else:
            msg = f"Sync error: {exc}"
        self.status_var.set(msg)

    async def load_idat_sync(self, credentials: Credentials) -> None:
        """Initialize the sync service and load the user's course list."""
        self.credentials = credentials
        async with self._busy(self.is_syncing):
            try:
                await self._ensure_sync_service()
            except Exception as exc:
                await self.aclose()
                self.courses_loaded = False
                self._set_error_status(exc)

    def _on_course_started(self, course: Course) -> None:
        self.course_tree.set_course_status(course.id, RowStatus.FETCHING)
        self.status_var.set(f"Fetching contents for {course.name}...")

    def _on_files_discovered(self, event: CourseFilesDiscovered) -> None:
        self.course_tree.show_files(
            event.course.id,
            event.course_dir,
            event.synced_paths,
            event.pending_paths,
        )

    def _on_download_started(self, path: Path) -> None:
        self.course_tree.set_file_status(path, RowStatus.DOWNLOADING)
        self.status_var.set(f"Downloading {path.name}...")

    def _on_download_completed(self, path: Path) -> None:
        self.course_tree.set_file_status(path, RowStatus.COMPLETED)

    def _on_download_failed(self, path: Path) -> None:
        self.course_tree.set_file_status(path, RowStatus.FAILED)

    def _on_course_completed(self, course: Course, error: Exception | None) -> None:
        status = RowStatus.COMPLETED if error is None else RowStatus.FAILED
        self.course_tree.set_course_status(course.id, status)

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

        has_errors = False
        async for event in idat_sync.sync_courses(path, selected, refresh=True):
            if isinstance(event, CourseSyncCompleted) and event.error is not None:
                has_errors = True
            self._handle_event(event)
        self.status_var.set(
            "Sync completed with errors." if has_errors else "Sync done."
        )

    async def do_sync(self) -> None:
        """Run synchronization for all selected courses."""
        path = self.folder_path.get().strip()
        if not path or self.is_syncing.get():
            return
        async with self._busy(self.is_syncing):
            try:
                await self._run_sync(path)
            except Exception as exc:
                self._set_error_status(exc)

    def browse_folder(self) -> None:
        """Open a folder picker dialog and store the chosen sync directory."""
        folder_selected = filedialog.askdirectory(
            initialdir=str(Path.home()),
            parent=self.master,
            title="Select Sync Folder",
        )
        if folder_selected:
            self.folder_path.set(folder_selected)
