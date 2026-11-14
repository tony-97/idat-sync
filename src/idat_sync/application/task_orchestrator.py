import asyncio
from collections import defaultdict
from collections.abc import Callable, Sequence
import threading

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
        on_task_start: Callable[[DownloadTask], None] | None = None,
        on_task_complete: Callable[[DownloadTask], None] | None = None,
        on_task_error: Callable[[DownloadTask, Exception], None] | None = None,
    ) -> None:
        for downloader in group:
            for task in tasks_by_downloader.get(downloader, []):
                if abort_event is not None and abort_event.is_set():
                    return
                if on_task_start is not None:
                    on_task_start(task)
                try:
                    task.download()
                except CredentialsExpiredError as exc:
                    if on_task_error is not None:
                        on_task_error(task, exc)
                    if abort_event is not None:
                        abort_event.set()
                    raise
                except Exception as exc:
                    print(f"[!] Failed downloading {task.path}: {exc}")
                    if on_task_error is not None:
                        on_task_error(task, exc)
                else:
                    if on_task_complete is not None:
                        on_task_complete(task)

    async def run_async(
        self,
        tasks: Sequence[DownloadTask],
        on_task_start: Callable[[DownloadTask], None] | None = None,
        on_task_complete: Callable[[DownloadTask], None] | None = None,
        on_task_error: Callable[[DownloadTask, Exception], None] | None = None,
    ) -> None:
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
                    self._run_group,
                    group,
                    tasks_by_downloader,
                    abort_event,
                    on_task_start,
                    on_task_complete,
                    on_task_error,
                )
                for group in active_groups
            )
        )

    def run(
        self,
        tasks: Sequence[DownloadTask],
        on_task_start: Callable[[DownloadTask], None] | None = None,
        on_task_complete: Callable[[DownloadTask], None] | None = None,
        on_task_error: Callable[[DownloadTask, Exception], None] | None = None,
    ) -> None:
        if not tasks:
            return
        asyncio.run(
            self.run_async(
                tasks,
                on_task_start=on_task_start,
                on_task_complete=on_task_complete,
                on_task_error=on_task_error,
            )
        )
