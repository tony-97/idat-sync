"""Base pure-Python UI component controller with async task lifecycle support."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


class AppFrame:  # pylint: disable=too-few-public-methods
    """Base component controller tracking a single active async task."""

    app: Any = None

    def __init__(self) -> None:
        """Initialize the frame with no active async task."""
        self._active_task: asyncio.Task[Any] | None = None

    def _notify(self) -> None:
        if hasattr(self.app, "push_state"):
            self.app.push_state()

    @contextlib.asynccontextmanager
    async def _busy(self, set_flag: Callable[[bool], None]) -> AsyncIterator[None]:
        """Mark the frame busy for the current task and reset safely on exit."""
        self._active_task = asyncio.current_task()
        set_flag(True)
        try:
            yield
        finally:
            self._active_task = None
            set_flag(False)

    async def aclose(self) -> None:
        """Cancel and await any active async task owned by this frame."""
        task, self._active_task = self._active_task, None
        current = asyncio.current_task()
        if task is not None and task is not current and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
