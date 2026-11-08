from pathlib import Path
from typing import Sequence

from idat_sync.services.downloaders import Content, DownloadTask, Downloader


class DownloadManager:
    def __init__(self, strategies: Sequence[Downloader]) -> None:
        self.strategies = list(strategies)

    def create_download_task(self, content: Content) -> DownloadTask | None:
        for downloader in self.strategies:
            if task := content.generate_task(downloader):
                return task
        return None

    def should_download(self, task: DownloadTask) -> bool:
        local_path = Path(task.path)
        if not local_path.exists():
            return True
        if task.last_modified > 0:
            return int(local_path.stat().st_mtime) < int(task.last_modified)
        return False

    def prepare_download_tasks(
        self, contents: Sequence[Content]
    ) -> list[DownloadTask]:
        tasks: list[DownloadTask] = []
        for content in contents:
            if task := self.create_download_task(content):
                if self.should_download(task):
                    tasks.append(task)
        return tasks
