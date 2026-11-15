from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from idat_sync.domain.content import Content


class SyncStatus(Enum):
    SYNCED = auto()
    MISSING = auto()
    OUTDATED = auto()
    LOCAL_ONLY = auto()


@dataclass
class SyncEntry:
    path: Path
    status: SyncStatus
    content: Content | None = None


@dataclass
class SyncDiff:
    entries: list[SyncEntry]
    missing_paths: set[Path]
    outdated_paths: set[Path]
    synced_paths: set[Path]
    local_only_paths: set[Path]

    @property
    def to_download_paths(self) -> set[Path]:
        return self.missing_paths | self.outdated_paths

    @property
    def pending_contents(self) -> list[Content]:
        pending = self.to_download_paths
        return [
            entry.content
            for entry in self.entries
            if entry.content is not None and entry.path in pending
        ]

    @classmethod
    def from_trees(
        cls,
        remote_contents: Sequence[Content],
        local_mtimes: Mapping[Path, int],
    ) -> "SyncDiff":
        remote_map: dict[Path, Content] = {}
        for content in remote_contents:
            if content.path in remote_map:
                raise ValueError(f"Duplicate remote content path: {content.path}")
            remote_map[content.path] = content

        remote_keys = remote_map.keys()
        local_keys = local_mtimes.keys()

        missing_paths: set[Path] = set(remote_keys - local_keys)
        common_paths: set[Path] = set(remote_keys & local_keys)
        local_only_paths: set[Path] = set(local_keys - remote_keys)

        outdated_paths: set[Path] = {
            path
            for path in common_paths
            if remote_map[path].last_modified > 0
            and remote_map[path].last_modified > local_mtimes[path]
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

        return cls(
            entries=entries,
            missing_paths=missing_paths,
            outdated_paths=outdated_paths,
            synced_paths=synced_paths,
            local_only_paths=local_only_paths,
        )
