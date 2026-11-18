"""Base Tkinter frame with async task lifecycle support."""

import asyncio
import contextlib
import tkinter as tk
from collections.abc import AsyncIterator
from typing import Any


class AppFrame(tk.Frame):
    """Base frame tracking a single active async task cancelled on close."""

    def __init__(self, master: tk.Misc, **kwargs: Any) -> None:  # noqa: ANN401
        """Initialize the frame with no active async task."""
        super().__init__(master, **kwargs)
        self._active_task: asyncio.Task[Any] | None = None

    @contextlib.asynccontextmanager
    async def _busy(self, flag: tk.BooleanVar) -> AsyncIterator[None]:
        """Mark the frame busy for the current task and reset safely on exit."""
        self._active_task = asyncio.current_task()
        flag.set(True)
        try:
            yield
        finally:
            self._active_task = None
            if self.winfo_exists():
                flag.set(False)

    async def aclose(self) -> None:
        """Cancel and await any active async task owned by this frame."""
        task, self._active_task = self._active_task, None
        current = asyncio.current_task()
        if task is not None and task is not current and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
