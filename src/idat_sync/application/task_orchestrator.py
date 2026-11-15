from collections import defaultdict
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from typing import Any

from idat_sync.application.ports import DownloadTask, Downloader


class TaskOrchestrator:
    def __init__(self, task_group: list[list[Downloader]]) -> None:
        self.task_group = task_group

    def create_coroutines(
        self,
        tasks: Sequence[DownloadTask],
        run_task: Callable[[DownloadTask], Awaitable[None]],
    ) -> list[Coroutine[Any, Any, None]]:
        if not tasks:
            return []
        tasks_by_downloader: defaultdict[Downloader, list[DownloadTask]] = (
            defaultdict(list)
        )
        for task in tasks:
            tasks_by_downloader[task.downloader].append(task)

        coroutines: list[Coroutine[Any, Any, None]] = []
        for group in self.task_group:
            group_tasks = [
                task
                for downloader in group
                for task in tasks_by_downloader.get(downloader, [])
            ]
            if not group_tasks:
                continue

            async def _run_group(
                items: Sequence[DownloadTask] = group_tasks,
            ) -> None:
                for item in items:
                    await run_task(item)

            coroutines.append(_run_group())
        return coroutines
