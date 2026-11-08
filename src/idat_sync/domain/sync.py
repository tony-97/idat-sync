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
