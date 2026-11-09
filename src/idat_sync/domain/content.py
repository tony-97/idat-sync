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
        if not folder:
            return self
        return replace(self, path=Path(folder) / self.path)


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

