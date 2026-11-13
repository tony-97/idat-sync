from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Self


@dataclass
class Content(ABC):
    path: Path
    last_modified: int

    @property
    def filename(self) -> str:
        return self.path.name

    def in_folder(self, folder: str | Path) -> Self:
        if (
            self.path.is_absolute()
            or bool(self.path.anchor)
            or ".." in self.path.parts
        ):
            raise ValueError(f"Unsafe content path: {self.path}")
        if not folder:
            return self
        folder_path = Path(folder)
        if ".." in folder_path.parts:
            raise ValueError(f"Unsafe folder path: {folder}")
        return replace(self, path=folder_path / self.path)


@dataclass
class MoodleContent(Content):
    url: str


@dataclass
class SharepointContent(Content):
    file: Any


@dataclass
class StringContent(Content):
    contents: list[str]


@dataclass
class RecordingContent(Content):
    url: str

