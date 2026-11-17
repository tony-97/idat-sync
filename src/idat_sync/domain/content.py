"""Domain content types representing downloadable and inline artifacts."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Self


@dataclass
class Content(ABC):
    """Base downloadable item with a relative path and modification timestamp."""

    path: Path
    last_modified: int

    @property
    def filename(self) -> str:
        """Return the base filename of the content path."""
        return self.path.name

    def in_folder(self, folder: str | Path) -> Self:
        """Return a copy of this content nested under ``folder``."""
        if self.path.is_absolute() or bool(self.path.anchor) or ".." in self.path.parts:
            raise ValueError(f"Unsafe content path: {self.path}")
        if not folder:
            return self
        folder_path = Path(folder)
        if ".." in folder_path.parts:
            raise ValueError(f"Unsafe folder path: {folder}")
        return replace(self, path=folder_path / self.path)


@dataclass
class MoodleContent(Content):
    """Downloadable file hosted on Moodle."""

    url: str


@dataclass
class SharepointContent(Content):
    """Downloadable file hosted on SharePoint or OneDrive."""

    file: Any


@dataclass
class StringContent(Content):
    """In-memory text artifact rendered to UTF-8 bytes on disk."""

    contents: list[str]

    def rendered_bytes(self) -> bytes | None:
        """Encode non-empty joined text as UTF-8 bytes, or return None."""
        text = "".join(self.contents).strip()
        return text.encode("utf-8") if text else None


@dataclass
class RecordingContent(Content):
    """Remote class video recording hosted on SharePoint or Microsoft Teams."""

    url: str
