"""Treeview widget wrapper and per-course progress state for SyncFrame."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from enum import StrEnum
from tkinter import ttk
from typing import TYPE_CHECKING

from idat_sync.ui.icons import Icons

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from idat_sync.domain.course import Course


class RowStatus(StrEnum):
    """Display status labels for course and file rows in CourseTree."""

    IDLE = "Idle"
    QUEUED = "Queued"
    FETCHING = "Fetching contents..."
    SYNCING = "Syncing"
    SYNCED = "Synced"
    PENDING = "Pending"
    DOWNLOADING = "Downloading"
    COMPLETED = "Completed"
    FAILED = "Failed"


@dataclass
class Progress:
    """Download progress counter for a single course."""

    done: int = 0
    total: int = 0

    def __str__(self) -> str:
        """Format completion percentage as a whole-number string."""
        return f"{int(self.done * 100 / self.total)}%" if self.total else "100%"


@dataclass
class _CourseNodes:
    """Treeview item IDs and progress state owned by one course row."""

    iid: str
    folder_iids: dict[tuple[str, ...], str] = field(default_factory=dict)
    file_iids: dict[Path, str] = field(default_factory=dict)
    progress: Progress | None = None


class CourseTree(ttk.Frame):  # pylint: disable=too-many-ancestors
    """Scrollable Treeview managing course selection, nested folders, and progress."""

    def __init__(
        self,
        master: tk.Misc,
        on_toggle: Callable[[int], None],
        icons: Icons | None = None,
    ) -> None:
        """Initialize the Treeview, columns, scrollbar, and icon bundle."""
        super().__init__(master)
        self.icons = icons or Icons.create()
        self._on_toggle = on_toggle
        self._interactive = True
        self._nodes: dict[int, _CourseNodes] = {}
        self._iid_to_course_id: dict[str, int] = {}
        self._file_to_course: dict[Path, tuple[int, str]] = {}
        self.tree = self._build_tree()

    def _build_tree(self) -> ttk.Treeview:
        style = ttk.Style(self)
        style.configure("Sync.Treeview", rowheight=22)

        tree = ttk.Treeview(
            self,
            style="Sync.Treeview",
            columns=("status", "progress"),
            show="tree headings",
            selectmode="browse",
        )
        tree.heading("#0", text="Name", anchor="w")
        tree.heading("status", text="Status", anchor="w")
        tree.heading("progress", text="Progress", anchor="e")
        tree.column("#0", width=440, stretch=True)
        tree.column("status", width=120, stretch=False, anchor="w")
        tree.column("progress", width=80, stretch=False, anchor="e")

        scrollbar = ttk.Scrollbar(self, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree.bind("<Button-1>", self._on_click)
        return tree

    def set_interactive(self, *, enabled: bool) -> None:
        """Enable or disable row checkbox toggling."""
        self._interactive = enabled

    def set_courses(self, courses: Sequence[Course], selected: set[int]) -> None:
        """Replace all tree rows with ``courses`` and apply initial ``selected``."""
        self.tree.delete(*self.tree.get_children(""))
        self._nodes.clear()
        self._iid_to_course_id.clear()
        self._file_to_course.clear()

        for course in courses:
            icon = self.icons.checked if course.id in selected else self.icons.unchecked
            iid = self.tree.insert(
                "",
                tk.END,
                text=course.name,
                image=icon,
                values=(RowStatus.IDLE, "0%"),
                open=False,
            )
            self._nodes[course.id] = _CourseNodes(iid=iid)
            self._iid_to_course_id[iid] = course.id

    def refresh_selection(self, selected: set[int]) -> None:
        """Update checkbox icons for all loaded course rows."""
        for course_id, node in self._nodes.items():
            icon = self.icons.checked if course_id in selected else self.icons.unchecked
            self.tree.item(node.iid, image=icon)

    def reset_for_sync(self, course_ids: Sequence[int]) -> None:
        """Clear children and mark ``course_ids`` as Queued at 0%."""
        for course_id in course_ids:
            if (node := self._nodes.get(course_id)) is None:
                continue
            self._clear_course_children(node)
            node.progress = None
            self.tree.item(node.iid, values=(RowStatus.QUEUED, "0%"))

    def show_files(
        self,
        course_id: int,
        course_dir: Path,
        synced: Sequence[Path],
        pending: Sequence[Path],
    ) -> None:
        """Populate synced and pending file nodes under ``course_id``."""
        if (node := self._nodes.get(course_id)) is None:
            return
        self._clear_course_children(node)
        for path in synced:
            self._insert_file(node, course_dir, path, (RowStatus.SYNCED, "100%"))
        for path in pending:
            child_iid = self._insert_file(
                node, course_dir, path, (RowStatus.PENDING, "0%")
            )
            node.file_iids[path] = child_iid
            self._file_to_course[path] = (course_id, child_iid)

        node.progress = Progress(done=len(synced), total=len(synced) + len(pending))
        self.tree.item(
            node.iid, values=(RowStatus.SYNCING, str(node.progress)), open=True
        )

    def set_file_status(self, path: Path, status: RowStatus) -> None:
        """Update a pending file row and advance parent progress on completion."""
        if (entry := self._file_to_course.get(path)) is None:
            return
        course_id, child_iid = entry
        pct = "100%" if status is RowStatus.COMPLETED else "0%"
        self.tree.item(child_iid, values=(status, pct))
        if (
            status is RowStatus.COMPLETED
            and (node := self._nodes.get(course_id)) is not None
            and node.progress is not None
        ):
            node.progress.done += 1
            self.tree.item(node.iid, values=(RowStatus.SYNCING, str(node.progress)))

    def set_course_status(self, course_id: int, status: RowStatus) -> None:
        """Update a course row's status and percentage."""
        if (node := self._nodes.get(course_id)) is None:
            return
        if status is RowStatus.COMPLETED:
            pct = "100%"
        elif status is RowStatus.FAILED and node.progress is not None:
            pct = str(node.progress)
        else:
            pct = "0%"
        self.tree.item(node.iid, values=(status, pct))

    def _clear_course_children(self, node: _CourseNodes) -> None:
        if existing := self.tree.get_children(node.iid):
            self.tree.delete(*existing)
        node.folder_iids.clear()
        for path in node.file_iids:
            self._file_to_course.pop(path, None)
        node.file_iids.clear()

    def _ensure_folder(self, node: _CourseNodes, parts: tuple[str, ...]) -> str:
        parent_iid = node.iid
        for depth in range(1, len(parts) + 1):
            key = parts[:depth]
            if (cached := node.folder_iids.get(key)) is not None:
                parent_iid = cached
            else:
                parent_iid = self.tree.insert(
                    parent_iid,
                    tk.END,
                    text=parts[depth - 1],
                    image=self.icons.folder,
                    values=("", ""),
                    open=True,
                )
                node.folder_iids[key] = parent_iid
        return parent_iid

    def _insert_file(
        self,
        node: _CourseNodes,
        course_dir: Path,
        path: Path,
        state: tuple[str, str],
    ) -> str:
        rel = path.relative_to(course_dir) if path.is_relative_to(course_dir) else path
        parent_iid = self._ensure_folder(node, rel.parts[:-1])
        return self.tree.insert(
            parent_iid,
            tk.END,
            text=rel.name,
            image=self.icons.file,
            values=state,
        )

    def _on_click(self, event: tk.Event) -> None:
        if not self._interactive:
            return
        if self.tree.identify_element(event.x, event.y) == "Treeitem.indicator":
            return
        row_id = self.tree.identify_row(event.y)
        if (
            row_id
            and not self.tree.parent(row_id)
            and (course_id := self._iid_to_course_id.get(row_id)) is not None
        ):
            self._on_toggle(course_id)


__all__ = ["CourseTree", "Progress", "RowStatus"]
