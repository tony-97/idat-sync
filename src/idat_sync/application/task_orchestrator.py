import asyncio
from collections import defaultdict
from typing import Sequence

from idat_sync.infrastructure.downloaders import DownloadTask, Downloader


class TaskOrchestrator:
    def __init__(self, task_group: list[list[Downloader]]) -> None:
        self.task_group = task_group

    def _run_group(
        self,
        group: Sequence[Downloader],
        tasks_by_downloader: dict[Downloader, list[DownloadTask]],
    ) -> None:
        for downloader in group:
            for task in tasks_by_downloader.get(downloader, []):
                task.download()

    async def run_async(self, tasks: Sequence[DownloadTask]) -> None:
        if not tasks:
            return
        tasks_by_downloader: defaultdict[Downloader, list[DownloadTask]] = (
            defaultdict(list)
        )
        for task in tasks:
            tasks_by_downloader[task.downloader].append(task)

        active_groups = [
            group
            for group in self.task_group
            if any(tasks_by_downloader.get(d) for d in group)
        ]
        if not active_groups:
            return

        await asyncio.gather(
            *(
                asyncio.to_thread(self._run_group, group, tasks_by_downloader)
                for group in active_groups
            )
        )

    def run(self, tasks: Sequence[DownloadTask]) -> None:
        if not tasks:
            return
        asyncio.run(self.run_async(tasks))
