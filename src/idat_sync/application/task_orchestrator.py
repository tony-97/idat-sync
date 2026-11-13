import asyncio
from collections import defaultdict
import threading
from typing import Sequence

from idat_sync.application.ports import (
    CredentialsExpiredError,
    DownloadTask,
    Downloader,
)


class TaskOrchestrator:
    def __init__(self, task_group: list[list[Downloader]]) -> None:
        self.task_group = task_group

    def _run_group(
        self,
        group: Sequence[Downloader],
        tasks_by_downloader: dict[Downloader, list[DownloadTask]],
        abort_event: threading.Event | None = None,
    ) -> None:
        for downloader in group:
            for task in tasks_by_downloader.get(downloader, []):
                if abort_event is not None and abort_event.is_set():
                    return
                try:
                    task.download()
                except CredentialsExpiredError:
                    if abort_event is not None:
                        abort_event.set()
                    raise
                except Exception as exc:
                    print(f"[!] Failed downloading {task.path}: {exc}")

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

        abort_event = threading.Event()
        await asyncio.gather(
            *(
                asyncio.to_thread(
                    self._run_group, group, tasks_by_downloader, abort_event
                )
                for group in active_groups
            )
        )

    def run(self, tasks: Sequence[DownloadTask]) -> None:
        if not tasks:
            return
        asyncio.run(self.run_async(tasks))
