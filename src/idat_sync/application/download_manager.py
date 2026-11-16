from pathlib import Path
from typing import Sequence

from idat_sync.application.ports import DownloadTask, Downloader, FileSystemPort
from idat_sync.domain.content import Content, StringContent
from idat_sync.domain.sync import SyncDiff


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
        changed_paths: set[Path] = set()
        for content in remote_contents:
            if (
                isinstance(content, StringContent)
                and content.last_modified == 0
                and content.path in local_map
                and (rendered := content.rendered_bytes()) is not None
                and self.file_system.read_bytes(content.path) != rendered
            ):
                changed_paths.add(content.path)
        return SyncDiff.from_trees(
            remote_contents, local_map, outdated_paths=changed_paths
        )

    def prepare_download_tasks(
        self,
        contents: Sequence[Content],
    ) -> list[DownloadTask]:
        tasks: list[DownloadTask] = []
        for content in contents:
            if task := self.create_download_task(content):
                tasks.append(task)
        return tasks


__all__ = ["DownloadManager"]
