"""Rate-limit-group task orchestrator for concurrent downloads."""

from collections import defaultdict
from collections.abc import Awaitable, Callable, Coroutine, Sequence

from idat_sync.application.ports import Downloader, DownloadTask


async def _default_run_task(task: DownloadTask) -> None:
    await task.download()


def _make_group_runner(
    items: Sequence[DownloadTask],
    runner: Callable[[DownloadTask], Awaitable[None]],
) -> Coroutine[object, object, None]:
    async def _run_group() -> None:
        for item in items:
            await runner(item)

    return _run_group()


class TaskOrchestrator:  # pylint: disable=too-few-public-methods
    """Groups DownloadTasks by downloader rate-limit group for execution."""

    def __init__(self, task_group: list[list[Downloader]]) -> None:
        """Initialize the orchestrator with ordered downloader groups."""
        self.task_group = task_group

    def create_coroutines(
        self,
        tasks: Sequence[DownloadTask],
        run_task: Callable[[DownloadTask], Awaitable[None]] | None = None,
    ) -> list[Coroutine[object, object, None]]:
        """Create one sequential coroutine per active downloader group."""
        if not tasks:
            return []
        runner = run_task or _default_run_task
        tasks_by_downloader: defaultdict[Downloader, list[DownloadTask]] = defaultdict(
            list
        )
        for task in tasks:
            tasks_by_downloader[task.downloader].append(task)

        coroutines: list[Coroutine[object, object, None]] = []
        for group in self.task_group:
            group_tasks = [
                task
                for downloader in group
                for task in tasks_by_downloader.get(downloader, [])
            ]
            if group_tasks:
                coroutines.append(_make_group_runner(group_tasks, runner))
        return coroutines
