from pathlib import Path
from typing import Sequence

from idat_sync.application.ports import DownloadTask, Downloader, FileSystemPort
from idat_sync.domain.content import Content
from idat_sync.domain.sync import SyncDiff, SyncEntry, SyncStatus


class DownloadManager:
    def __init__(
        self,
        strategies: Sequence[Downloader],
        file_system: FileSystemPort,
    ) -> None:
        self.strategies = list(strategies)
        self.file_system = file_system

    def create_download_task(self, content: Content) -> DownloadTask | None:
        for downloader in self.strategies:
            if task := downloader.create_task(content):
                return task
        return None

    def diff_trees(
        self, root_dir: str | Path, remote_contents: Sequence[Content]
    ) -> SyncDiff:
        local_map = dict(self.file_system.scan_mtimes(Path(root_dir)))
        return SyncDiff.from_trees(remote_contents, local_map)

    def should_download(self, task: DownloadTask) -> bool:
        local_path = Path(task.path)
        if not self.file_system.exists(local_path):
            return True
        if task.last_modified > 0:
            return self.file_system.get_mtime(local_path) < int(
                task.last_modified
            )
        return False

    def prepare_download_tasks(
        self,
        contents: Sequence[Content],
        root_dir: str | Path | None = None,
    ) -> list[DownloadTask]:
        if root_dir is not None:
            diff = self.diff_trees(root_dir, contents)
            target_contents = diff.pending_contents
        else:
            target_contents = list(contents)

        tasks: list[DownloadTask] = []
        for content in target_contents:
            if task := self.create_download_task(content):
                if root_dir is not None or self.should_download(task):
                    tasks.append(task)
        return tasks


__all__ = ["DownloadManager", "SyncDiff", "SyncEntry", "SyncStatus"]
