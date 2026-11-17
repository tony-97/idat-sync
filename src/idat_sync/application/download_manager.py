"""Download manager for tree diffing and downloader task creation."""

from collections.abc import Sequence
from pathlib import Path

from idat_sync.application.ports import Downloader, DownloadTask, FileSystemPort
from idat_sync.domain.content import Content, StringContent
from idat_sync.domain.sync import SyncDiff


class DownloadManager:
    """Matches Content items to Downloaders and diffs remote vs. local trees."""

    def __init__(
        self,
        strategies: Sequence[Downloader],
        file_system: FileSystemPort,
    ) -> None:
        """Initialize the download manager with downloader strategies and filesystem."""
        self.strategies = list(strategies)
        self.file_system = file_system

    def create_download_task(self, content: Content) -> DownloadTask | None:
        """Return a DownloadTask from the first matching strategy, or None."""
        for downloader in self.strategies:
            if task := downloader.create_task(content):
                return task
        return None

    def diff_trees(
        self, root_dir: str | Path, remote_contents: Sequence[Content]
    ) -> SyncDiff:
        """Compare ``remote_contents`` against local files under ``root_dir``."""
        local_map = dict(self.file_system.scan_mtimes(Path(root_dir)))
        changed_paths: set[Path] = set()
        for content in remote_contents:
            if (
                isinstance(content, StringContent)
                and not content.last_modified
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
        """Build DownloadTasks for all supported items in ``contents``."""
        return [
            task
            for content in contents
            if (task := self.create_download_task(content)) is not None
        ]


__all__ = ["DownloadManager"]
