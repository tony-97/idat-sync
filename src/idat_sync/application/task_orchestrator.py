import asyncio
from collections import defaultdict
from collections.abc import Sequence
import threading

from idat_sync.application.events import (
    EventPublisher,
    TaskDownloadCompleted,
    TaskDownloadFailed,
    TaskDownloadStarted,
    noop_publisher,
)
from idat_sync.application.ports import (
    CredentialsExpiredError,
    DownloadTask,
    Downloader,
)


class TaskOrchestrator:
    def __init__(
        self,
        task_group: list[list[Downloader]],
        publish: EventPublisher = noop_publisher,
    ) -> None:
        self.task_group = task_group
        self.publish = publish

    def _run_group(
        self,
        group: Sequence[Downloader],
        tasks_by_downloader: dict[Downloader, list[DownloadTask]],
        abort_event: threading.Event | None = None,
    ) -> list[Exception]:
        errors: list[Exception] = []
        for downloader in group:
            for task in tasks_by_downloader.get(downloader, []):
                if abort_event is not None and abort_event.is_set():
                    return errors
                self.publish(TaskDownloadStarted(task.path))
                try:
                    task.download()
                except CredentialsExpiredError as exc:
                    self.publish(TaskDownloadFailed(task.path, exc))
                    if abort_event is not None:
                        abort_event.set()
                    raise
                except Exception as exc:
                    errors.append(exc)
                    self.publish(TaskDownloadFailed(task.path, exc))
                else:
                    self.publish(TaskDownloadCompleted(task.path))
        return errors

    async def run_async(self, tasks: Sequence[DownloadTask]) -> list[Exception]:
        if not tasks:
            return []
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
            return []

        abort_event = threading.Event()
        group_errors = await asyncio.gather(
            *(
                asyncio.to_thread(
                    self._run_group,
                    group,
                    tasks_by_downloader,
                    abort_event,
                )
                for group in active_groups
            )
        )
        return [err for group in group_errors for err in group]

    def run(self, tasks: Sequence[DownloadTask]) -> list[Exception]:
        if not tasks:
            return []
        return asyncio.run(self.run_async(tasks))
