"""Synchronization state and tree-diff domain models."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from idat_sync.domain.content import Content


class SyncStatus(Enum):
    """Synchronization state of a single path."""

    SYNCED = auto()
    MISSING = auto()
    OUTDATED = auto()
    LOCAL_ONLY = auto()


@dataclass
class SyncEntry:
    """Single path paired with its synchronization status and optional content."""

    path: Path
    status: SyncStatus
    content: Content | None = None


@dataclass
class SyncDiff:
    """Comparison between a remote content tree and a local directory tree."""

    entries: list[SyncEntry]
    missing_paths: set[Path]
    outdated_paths: set[Path]
    synced_paths: set[Path]
    local_only_paths: set[Path]

    @property
    def to_download_paths(self) -> set[Path]:
        """Return all remote paths that are missing or outdated locally."""
        return self.missing_paths | self.outdated_paths

    @property
    def pending_contents(self) -> list[Content]:
        """Return remote content items that still need to be downloaded."""
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
        *,
        outdated_paths: Collection[Path] = (),
    ) -> "SyncDiff":
        """Build a SyncDiff by comparing remote contents against local mtimes."""
        remote_map: dict[Path, Content] = {}
        for content in remote_contents:
            if content.path in remote_map:
                raise ValueError(f"Duplicate remote content path: {content.path}")
            remote_map[content.path] = content

        missing_paths = set(remote_map.keys() - local_mtimes.keys())
        common_paths = set(remote_map.keys() & local_mtimes.keys())
        local_only_paths = set(local_mtimes.keys() - remote_map.keys())
        outdated_paths = set(outdated_paths)

        resolved_outdated = {
            path
            for path in common_paths
            if path in outdated_paths
            or (
                remote_map[path].last_modified > 0
                and remote_map[path].last_modified > local_mtimes[path]
            )
        }
        synced_paths = common_paths - resolved_outdated

        entries = sorted(
            [
                *(
                    SyncEntry(path, SyncStatus.MISSING, remote_map[path])
                    for path in missing_paths
                ),
                *(
                    SyncEntry(path, SyncStatus.OUTDATED, remote_map[path])
                    for path in resolved_outdated
                ),
                *(
                    SyncEntry(path, SyncStatus.SYNCED, remote_map[path])
                    for path in synced_paths
                ),
                *(
                    SyncEntry(path, SyncStatus.LOCAL_ONLY, None)
                    for path in local_only_paths
                ),
            ],
            key=lambda entry: entry.path,
        )

        return cls(
            entries=entries,
            missing_paths=missing_paths,
            outdated_paths=resolved_outdated,
            synced_paths=synced_paths,
            local_only_paths=local_only_paths,
        )
