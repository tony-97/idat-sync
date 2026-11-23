"""Pure synchronization state classification."""

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

from idat_sync.domain.content import Content


class ContentSyncState(StrEnum):
    """Three-state classification of a discovered content item against local files."""

    SYNCED = "Synced"
    NEW = "New"
    OUTDATED = "Outdated"


def classify_content(
    content: Content, local_mtimes: Mapping[Path, int]
) -> ContentSyncState:
    """Classify ``content`` purely in memory against ``local_mtimes``."""
    local_mtime = local_mtimes.get(content.path)
    if local_mtime is None:
        return ContentSyncState.NEW
    if content.last_modified > 0 and content.last_modified > local_mtime:
        return ContentSyncState.OUTDATED
    return ContentSyncState.SYNCED


__all__ = ["ContentSyncState", "classify_content"]
