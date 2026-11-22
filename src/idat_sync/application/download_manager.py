"""Download manager for per-item pending checks and downloader task creation."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from idat_sync.application.ports import Downloader, DownloadTask, FileSystemPort
from idat_sync.domain.content import Content, StringContent
from idat_sync.domain.sync import is_content_pending


class DownloadManager:
    """Matches Content items to Downloaders and checks whether items are pending."""

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

    def is_pending(self, content: Content, local_mtimes: Mapping[Path, int]) -> bool:
        """Return True when ``content`` must be downloaded or rewritten."""
        if is_content_pending(content, local_mtimes):
            return True
        return (
            isinstance(content, StringContent)
            and not content.last_modified
            and (rendered := content.rendered_bytes()) is not None
            and self.file_system.read_bytes(content.path) != rendered
        )


__all__ = ["DownloadManager"]
