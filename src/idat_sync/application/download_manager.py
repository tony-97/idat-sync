from pathlib import Path
from typing import Sequence

from idat_sync.domain.content import Content
from idat_sync.domain.sync import SyncDiff, SyncEntry, SyncStatus
from idat_sync.infrastructure.downloaders import DownloadTask, Downloader


class DownloadManager:
    def __init__(self, strategies: Sequence[Downloader]) -> None:
        self.strategies = list(strategies)

    def create_download_task(self, content: Content) -> DownloadTask | None:
        for downloader in self.strategies:
            if task := content.generate_task(downloader):
                return task
        return None

    def diff_trees(
        self, root_dir: str | Path, remote_contents: Sequence[Content]
    ) -> SyncDiff:
        root_path = Path(root_dir)
        remote_map: dict[Path, Content] = {c.path: c for c in remote_contents}

        local_map: dict[Path, int] = {}
        if root_path.exists():
            for p in root_path.rglob("*"):
                if p.is_file():
                    local_map[p] = int(p.stat().st_mtime)

        remote_keys = remote_map.keys()
        local_keys = local_map.keys()

        missing_paths: set[Path] = remote_keys - local_keys
        common_paths: set[Path] = remote_keys & local_keys
        local_only_paths: set[Path] = local_keys - remote_keys

        outdated_paths: set[Path] = {
            path
            for path in common_paths
            if remote_map[path].last_modified > 0
            and remote_map[path].last_modified > local_map[path]
        }
        synced_paths: set[Path] = common_paths - outdated_paths

        entries: list[SyncEntry] = []
        for path in missing_paths:
            entries.append(
                SyncEntry(path, SyncStatus.MISSING, remote_map[path])
            )
        for path in outdated_paths:
            entries.append(
                SyncEntry(path, SyncStatus.OUTDATED, remote_map[path])
            )
        for path in synced_paths:
            entries.append(
                SyncEntry(path, SyncStatus.SYNCED, remote_map[path])
            )
        for path in local_only_paths:
            entries.append(SyncEntry(path, SyncStatus.LOCAL_ONLY, None))

        entries.sort(key=lambda e: e.path)

        return SyncDiff(
            entries=entries,
            missing_paths=missing_paths,
            outdated_paths=outdated_paths,
            synced_paths=synced_paths,
            local_only_paths=local_only_paths,
        )

    def should_download(self, task: DownloadTask) -> bool:
        local_path = Path(task.path)
        if not local_path.exists():
            return True
        if task.last_modified > 0:
            return int(local_path.stat().st_mtime) < int(task.last_modified)
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
