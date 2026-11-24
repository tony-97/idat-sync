"""Streaming TaskChannel grouped by upstream rate-limit boundaries."""

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Sequence

from idat_sync.application.ports import Downloader, DownloadTask


def _make_queue_worker(
    queue: asyncio.Queue[DownloadTask | None],
    runner: Callable[[DownloadTask], Awaitable[None]],
) -> Coroutine[object, object, None]:
    async def _run_group() -> None:
        while (task := await queue.get()) is not None:
            await runner(task)

    return _run_group()


class TaskChannel:
    """Per-run streaming task channel with one FIFO queue per downloader group."""

    def __init__(self, groups: Sequence[Sequence[Downloader]]) -> None:
        """Initialize unbounded group queues and downloader-to-group routing."""
        self._queues: list[asyncio.Queue[DownloadTask | None]] = [
            asyncio.Queue() for _ in groups
        ]
        self._group_by_downloader: dict[Downloader, int] = {
            downloader: idx for idx, group in enumerate(groups) for downloader in group
        }
        self._closed = False

    def enqueue(self, task: DownloadTask) -> None:
        """Route ``task`` into its downloader's rate-limit group queue."""
        group_idx = self._group_by_downloader.get(task.downloader)
        if group_idx is None:
            raise ValueError(f"Unregistered downloader: {task.downloader!r}")
        self._queues[group_idx].put_nowait(task)

    def close(self) -> None:
        """Idempotently signal all group workers to drain and exit."""
        if self._closed:
            return
        self._closed = True
        for queue in self._queues:
            queue.put_nowait(None)

    def create_workers(
        self,
        run_task: Callable[[DownloadTask], Awaitable[None]],
    ) -> list[Coroutine[object, object, None]]:
        """Create one sequential FIFO worker coroutine per downloader group."""
        return [_make_queue_worker(queue, run_task) for queue in self._queues]


__all__ = ["TaskChannel"]
