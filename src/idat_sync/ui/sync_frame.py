import asyncio
from collections.abc import Sequence
import os
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk
from typing import cast

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
from idat_sync.application.ports import (
    Credentials,
    CredentialsExpiredError,
)
from idat_sync.application.sync_service import IDATSync
from idat_sync.bootstrap import create_sync_service
from idat_sync.config import SyncConfig
from idat_sync.domain.course import Course, get_in_progress_course_ids
from idat_sync.ui.app import AppFrame, MainApp


def _draw_icon(rows: list[str], palette: dict[str, str]) -> tk.PhotoImage:
    """Build a 16x16 PhotoImage from an ASCII pixel grid."""
    img = tk.PhotoImage(width=16, height=16)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if color := palette.get(ch):
                img.put(color, (x, y))
    return img


def _make_checkbox_icon(checked: bool) -> tk.PhotoImage:
    if checked:
        rows = [
            "................",
            "................",
            "..BBBBBBBBBBBB..",
            ".BFFFFFFFFFFFFB.",
            ".BFFFFFFFFFFFFB.",
            ".BFFFFFFFFFWWFB.",
            ".BFFFFFFFFWWFFB.",
            ".BFFWWFFFWWFFFB.",
            ".BFFWWWFWwFFFFB.",
            ".BFFFWWWWFFFFFB.",
            ".BFFFFWWFFFFFFB.",
            ".BFFFFFFFFFFFFB.",
            ".BFFFFFFFFFFFFB.",
            "..BBBBBBBBBBBB..",
            "................",
            "................",
        ]
        palette = {"B": "#005A9E", "F": "#0078D4", "W": "#FFFFFF", "w": "#E6F2FA"}
    else:
        rows = [
            "................",
            "................",
            "..BBBBBBBBBBBB..",
            ".BFFFFFFFFFFFFB.",
            ".BFFFFFFFFFFFFB.",
            ".BFFFFFFFFFFFFB.",
            ".BFFFFFFFFFFFFB.",
            ".BFFFFFFFFFFFFB.",
            ".BFFFFFFFFFFFFB.",
            ".BFFFFFFFFFFFFB.",
            ".BFFFFFFFFFFFFB.",
            ".BFFFFFFFFFFFFB.",
            ".BFFFFFFFFFFFFB.",
            "..BBBBBBBBBBBB..",
            "................",
            "................",
        ]
        palette = {"B": "#767676", "F": "#FFFFFF"}
    return _draw_icon(rows, palette)


def _make_folder_icon() -> tk.PhotoImage:
    rows = [
        "................",
        "................",
        ".BBBBBB.........",
        ".BTTTTTB........",
        ".BTTTTTTBBBBBBB.",
        ".BFFFFFFFFFFFFB.",
        ".BFLLLLLLLLLLFB.",
        ".BFLLLLLLLLLLFB.",
        ".BFLLLLLLLLLLFB.",
        ".BFLLLLLLLLLLFB.",
        ".BFLLLLLLLLLLFB.",
        ".BFLLLLLLLLLLFB.",
        ".BFFFFFFFFFFFFB.",
        "..BBBBBBBBBBBB..",
        "................",
        "................",
    ]
    palette = {
        "B": "#6E8FAD",
        "T": "#D3E3F3",
        "F": "#B7D0E8",
        "L": "#9FC0E0",
    }
    return _draw_icon(rows, palette)


def _make_file_icon() -> tk.PhotoImage:
    rows = [
        "................",
        "..BBBBBBBB......",
        "..BFFFFFFBB.....",
        "..BFFFFFFBFB....",
        "..BFFFFFFBBBB...",
        "..BFFFFFFFFFB...",
        "..BFFFFFFFFFB...",
        "..BFFFFFFFFFB...",
        "..BFFFFFFFFFB...",
        "..BFFFFFFFFFB...",
        "..BFFFFFFFFFB...",
        "..BFFFFFFFFFB...",
        "..BFFFFFFFFFB...",
        "..BFFFFFFFFFB...",
        "..BBBBBBBBBBB...",
        "................",
    ]
    palette = {"B": "#8E8E8E", "F": "#F7F7F7"}
    return _draw_icon(rows, palette)


class SyncFrame(AppFrame):
    def __init__(
        self,
        master: MainApp,
        credentials: Credentials,
        config: SyncConfig,
        **kwargs,
    ):
        tk.Frame.__init__(self, master, **kwargs)
        self.credentials = credentials
        self.config = config
        master.center_window(700, 500)
        master.title("IDAT Sync")

        self.idat_sync: IDATSync | None = None
        self.courses: list[Course] = []
        self.in_progress_ids: set[int] = set()
        self.selected_course_ids: set[int] = set()
        self.courses_loaded = False
        self._course_iids: dict[int, str] = {}
        self._iid_to_course_id: dict[str, int] = {}
        self._folder_iids: dict[tuple[int, tuple[str, ...]], str] = {}
        self._file_iids: dict[Path, tuple[int, str]] = {}
        self._course_progress: dict[int, list[int]] = {}

        # Icons must be retained on self so Tkinter does not garbage-collect them.
        self._checked_icon = _make_checkbox_icon(checked=True)
        self._unchecked_icon = _make_checkbox_icon(checked=False)
        self._folder_icon = _make_folder_icon()
        self._file_icon = _make_file_icon()

        style = ttk.Style(self)
        style.configure("Sync.Treeview", rowheight=22)

        # Folder selection bar
        folder_frame = tk.Frame(self, padx=10, pady=10)
        folder_frame.pack(fill=tk.X)
        tk.Label(folder_frame, text="Sync Folder:").pack(
            side=tk.LEFT, padx=(0, 5)
        )
        self.folder_path = tk.StringVar()
        tk.Entry(
            folder_frame, textvariable=self.folder_path, state="readonly"
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.browse_button = tk.Button(
            folder_frame, text="Browse...", command=self.browse_folder
        )
        self.browse_button.pack(side=tk.LEFT, padx=(5, 0))

        # Quick-select toolbar
        select_frame = tk.Frame(self, padx=10)
        select_frame.pack(fill=tk.X, pady=(0, 5))
        tk.Label(select_frame, text="Courses:").pack(side=tk.LEFT)
        self.in_progress_button = tk.Button(
            select_frame, text="In Progress", command=self.select_in_progress
        )
        self.in_progress_button.pack(side=tk.LEFT, padx=(5, 0))
        self.select_all_button = tk.Button(
            select_frame, text="Select All", command=self.select_all
        )
        self.select_all_button.pack(side=tk.LEFT, padx=(5, 0))
        self.clear_button = tk.Button(
            select_frame, text="Clear", command=self.clear_selection
        )
        self.clear_button.pack(side=tk.LEFT, padx=(5, 0))

        # Course & file progress treeview
        tree_frame = tk.Frame(self, padx=10)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(
            tree_frame,
            style="Sync.Treeview",
            columns=("status", "progress"),
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="Name", anchor="w")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.heading("progress", text="Progress", anchor="e")
        self.tree.column("#0", width=440, stretch=True)
        self.tree.column("status", width=120, stretch=False, anchor="w")
        self.tree.column("progress", width=80, stretch=False, anchor="e")
        scrollbar = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<Button-1>", self._on_tree_click)

        # Action buttons
        button_frame = tk.Frame(self, padx=10)
        button_frame.pack(fill=tk.X, pady=(10, 5))
        self.logout_button = tk.Button(
            button_frame,
            text="Logout",
            command=lambda: cast(MainApp, self.master).switch_to_login(),
        )
        self.logout_button.pack(side=tk.LEFT)
        self.sync_button = tk.Button(
            button_frame,
            text="Sync",
            command=async_handler(self.do_sync),
            state="disabled",
        )
        self.sync_button.pack(side=tk.RIGHT)

        # Bottom status bar
        self.status_var = tk.StringVar(value="Loading courses...")
        tk.Label(
            self, textvariable=self.status_var, anchor="w", padx=10, pady=5
        ).pack(fill=tk.X)

        self.is_syncing = tk.BooleanVar(value=False)
        self.is_syncing.trace_add("write", self.validate_buttons)
        self.folder_path.trace_add("write", self.validate_buttons)

        self.progress_queue: asyncio.Queue[SyncEvent | tuple[str, str] | None] = (
            asyncio.Queue()
        )
        self.after_idle(
            lambda: master.loop.create_task(self.load_idat_sync(credentials))
        )
        master.loop.create_task(self.update_progress())

    def exit(self):
        self.progress_queue.put_nowait(None)

    def validate_buttons(self, *args):
        is_syncing = self.is_syncing.get()
        folder_is_selected = bool(self.folder_path.get().strip())
        has_selection = bool(self.selected_course_ids) or not self.courses_loaded

        action_state = "disabled" if is_syncing else "normal"
        sync_state = (
            "normal"
            if not is_syncing and folder_is_selected and has_selection
            else "disabled"
        )

        self.browse_button.config(state=action_state)
        self.logout_button.config(state=action_state)
        self.in_progress_button.config(state=action_state)
        self.select_all_button.config(state=action_state)
        self.clear_button.config(state=action_state)
        self.sync_button.config(state=sync_state)

    def _course_icon(self, course_id: int) -> tk.PhotoImage:
        return (
            self._checked_icon
            if course_id in self.selected_course_ids
            else self._unchecked_icon
        )

    def _refresh_course_icons(self) -> None:
        for course in self.courses:
            iid = self._course_iids.get(course.id)
            if iid is not None:
                self.tree.item(iid, image=self._course_icon(course.id))
        self.validate_buttons()

    def _clear_children(self, iid: str) -> None:
        existing = self.tree.get_children(iid)
        if existing:
            self.tree.delete(*existing)

    @staticmethod
    def _format_progress(done: int, total: int) -> str:
        return f"{int(done * 100 / total)}%" if total else "100%"

    def _ensure_folder_node(
        self, course_id: int, course_iid: str, parts: tuple[str, ...]
    ) -> str:
        parent_iid = course_iid
        for depth in range(1, len(parts) + 1):
            key = (course_id, parts[:depth])
            if (cached := self._folder_iids.get(key)) is not None:
                parent_iid = cached
            else:
                parent_iid = self.tree.insert(
                    parent_iid,
                    tk.END,
                    text=parts[depth - 1],
                    image=self._folder_icon,
                    values=("", ""),
                    open=True,
                )
                self._folder_iids[key] = parent_iid
        return parent_iid

    def _insert_file_node(
        self,
        course_id: int,
        course_iid: str,
        course_dir: Path,
        path: Path,
        status: str,
        progress: str,
    ) -> str:
        rel = (
            path.relative_to(course_dir)
            if path.is_relative_to(course_dir)
            else path
        )
        parent_iid = self._ensure_folder_node(
            course_id, course_iid, rel.parts[:-1]
        )
        return self.tree.insert(
            parent_iid,
            tk.END,
            text=rel.name,
            image=self._file_icon,
            values=(status, progress),
        )

    def _populate_courses(self, courses: Sequence[Course]) -> None:
        self.tree.delete(*self.tree.get_children(""))
        self._course_iids.clear()
        self._iid_to_course_id.clear()
        self._folder_iids.clear()
        self._file_iids.clear()
        self._course_progress.clear()

        self.in_progress_ids = get_in_progress_course_ids(courses)
        self.courses = [
            c for c in courses if c.id in self.in_progress_ids
        ] + [c for c in courses if c.id not in self.in_progress_ids]
        self.selected_course_ids = set(self.in_progress_ids)
        self.courses_loaded = True

        for course in self.courses:
            iid = self.tree.insert(
                "",
                tk.END,
                text=course.name,
                image=self._course_icon(course.id),
                values=("Idle", "0%"),
                open=False,
            )
            self._course_iids[course.id] = iid
            self._iid_to_course_id[iid] = course.id

        self.status_var.set(f"Loaded {len(self.courses)} courses.")
        self.validate_buttons()

    def select_in_progress(self) -> None:
        self.selected_course_ids = set(self.in_progress_ids)
        self._refresh_course_icons()

    def select_all(self) -> None:
        self.selected_course_ids = {c.id for c in self.courses}
        self._refresh_course_icons()

    def clear_selection(self) -> None:
        self.selected_course_ids.clear()
        self._refresh_course_icons()

    def toggle_course_row(self, iid: str) -> None:
        course_id = self._iid_to_course_id.get(iid)
        if course_id is None:
            return
        if course_id in self.selected_course_ids:
            self.selected_course_ids.remove(course_id)
        else:
            self.selected_course_ids.add(course_id)
        self._refresh_course_icons()

    def _on_tree_click(self, event: tk.Event) -> None:
        if self.is_syncing.get():
            return
        if self.tree.identify_element(event.x, event.y) == "Treeitem.indicator":
            return
        row_id = self.tree.identify_row(event.y)
        if row_id and self.tree.parent(row_id) == "":
            self.toggle_course_row(row_id)

    async def _ensure_sync_service(self) -> IDATSync:
        if self.idat_sync is None:
            loop = cast(MainApp, self.master).loop
            self.idat_sync = await asyncio.to_thread(
                create_sync_service,
                self.credentials,
                self.config,
                publish=lambda event: loop.call_soon_threadsafe(
                    self.progress_queue.put_nowait, event
                ),
            )
        if not self.courses_loaded:
            courses = await asyncio.to_thread(self.idat_sync.list_courses)
            self._populate_courses(courses)
        return self.idat_sync

    async def _queue_error(self, exc: Exception) -> None:
        if isinstance(exc, CredentialsExpiredError):
            msg = f"Credentials expired: {exc}. Click Logout to sign in again."
        else:
            msg = f"Sync error: {exc}"
        await self.progress_queue.put(("status", msg))

    async def load_idat_sync(self, credentials: Credentials):
        self.credentials = credentials
        self.is_syncing.set(True)
        try:
            await self._ensure_sync_service()
        except Exception as exc:
            self.idat_sync = None
            self.courses_loaded = False
            await self._queue_error(exc)
        finally:
            self.is_syncing.set(False)

    async def update_progress(self):
        while True:
            event = await self.progress_queue.get()
            if event is None:
                return
            match event:
                case ("status", msg):
                    self.status_var.set(msg)
                case CourseSyncStarted(course=course):
                    if iid := self._course_iids.get(course.id):
                        self.tree.item(iid, values=("Fetching contents...", "0%"))
                    self.status_var.set(f"Fetching contents for {course.name}...")
                case CourseFilesDiscovered(
                    course=course,
                    course_dir=course_dir,
                    synced_paths=synced_paths,
                    pending_paths=pending_paths,
                ):
                    if iid := self._course_iids.get(course.id):
                        self._clear_children(iid)
                        for key in [
                            k for k in self._folder_iids if k[0] == course.id
                        ]:
                            del self._folder_iids[key]
                        for p in [
                            p
                            for p, (cid, _) in self._file_iids.items()
                            if cid == course.id
                        ]:
                            del self._file_iids[p]
                        for path in synced_paths:
                            self._insert_file_node(
                                course.id, iid, course_dir, path, "Synced", "100%"
                            )
                        for path in pending_paths:
                            child_iid = self._insert_file_node(
                                course.id, iid, course_dir, path, "Pending", "0%"
                            )
                            self._file_iids[path] = (course.id, child_iid)
                        total = len(synced_paths) + len(pending_paths)
                        done = len(synced_paths)
                        self._course_progress[course.id] = [done, total]
                        self.tree.item(
                            iid,
                            values=("Syncing", self._format_progress(done, total)),
                            open=True,
                        )
                case TaskDownloadStarted(path=path):
                    if entry := self._file_iids.get(path):
                        _, child_iid = entry
                        self.tree.item(child_iid, values=("Downloading", "0%"))
                    self.status_var.set(f"Downloading {path.name}...")
                case TaskDownloadCompleted(path=path):
                    if entry := self._file_iids.get(path):
                        course_id, child_iid = entry
                        self.tree.item(child_iid, values=("Completed", "100%"))
                        if counts := self._course_progress.get(course_id):
                            counts[0] += 1
                            if iid := self._course_iids.get(course_id):
                                self.tree.item(
                                    iid,
                                    values=(
                                        "Syncing",
                                        self._format_progress(
                                            counts[0], counts[1]
                                        ),
                                    ),
                                )
                case TaskDownloadFailed(path=path):
                    if entry := self._file_iids.get(path):
                        _, child_iid = entry
                        self.tree.item(child_iid, values=("Failed", "0%"))
                case CourseSyncCompleted(course=course, error=error):
                    if iid := self._course_iids.get(course.id):
                        counts = self._course_progress.get(course.id)
                        if error is None:
                            self.tree.item(iid, values=("Completed", "100%"))
                        else:
                            pct = (
                                self._format_progress(counts[0], counts[1])
                                if counts
                                else "0%"
                            )
                            self.tree.item(iid, values=("Failed", pct))
            self.progress_queue.task_done()

    async def do_sync(self):
        path = self.folder_path.get().strip()
        if not path or self.is_syncing.get():
            return
        self.is_syncing.set(True)
        try:
            idat_sync = await self._ensure_sync_service()
            selected = [
                c for c in self.courses if c.id in self.selected_course_ids
            ]
            if not selected:
                return

            for course in selected:
                if iid := self._course_iids.get(course.id):
                    self._clear_children(iid)
                    self.tree.item(iid, values=("Queued", "0%"))
                self._course_progress.pop(course.id, None)

            await asyncio.to_thread(
                idat_sync.sync_courses,
                path,
                selected,
            )
            await self.progress_queue.put(("status", "Sync done."))
        except Exception as exc:
            await self._queue_error(exc)
        finally:
            self.is_syncing.set(False)

    def browse_folder(self):
        folder_selected = filedialog.askdirectory(
            initialdir=os.path.expanduser("~"),
            parent=self.master,
            title="Select Sync Folder",
        )
        if folder_selected:
            self.folder_path.set(folder_selected)
