"""Pure synchronization state predicates."""

from collections.abc import Mapping
from pathlib import Path

from idat_sync.domain.content import Content


def is_content_pending(content: Content, local_mtimes: Mapping[Path, int]) -> bool:
    """Return True when ``content`` is missing locally or newer than the local mtime."""
    local_mtime = local_mtimes.get(content.path)
    if local_mtime is None:
        return True
    return content.last_modified > 0 and content.last_modified > local_mtime


__all__ = ["is_content_pending"]
