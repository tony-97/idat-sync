"""Pixel-art Tkinter PhotoImage icons for the course tree."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass

_CHECKED = [
    "................",
    "................",
    "..BBBBBBBBBBBB..",
    ".BFFFFFFFFFFFFB.",
    ".BFFFFFFFFFFFFB.",
    ".BFFFFFFFFFWWFB.",
    ".BFFFFFFFFWWFFB.",
    ".BFFWWFFFWWFFFB.",
    ".BFFWWWFWwFFFFB.",
    ".BFFFWWWWFFFFFB.",
    ".BFFFFWWFFFFFFB.",
    ".BFFFFFFFFFFFFB.",
    ".BFFFFFFFFFFFFB.",
    "..BBBBBBBBBBBB..",
    "................",
    "................",
]

_UNCHECKED = [
    "................",
    "................",
    "..BBBBBBBBBBBB..",
    ".BFFFFFFFFFFFFB.",
    ".BFFFFFFFFFFFFB.",
    ".BFFFFFFFFFFFFB.",
    ".BFFFFFFFFFFFFB.",
    ".BFFFFFFFFFFFFB.",
    ".BFFFFFFFFFFFFB.",
    ".BFFFFFFFFFFFFB.",
    ".BFFFFFFFFFFFFB.",
    ".BFFFFFFFFFFFFB.",
    ".BFFFFFFFFFFFFB.",
    "..BBBBBBBBBBBB..",
    "................",
    "................",
]

_FOLDER = [
    "................",
    "................",
    ".BBBBBB.........",
    ".BTTTTTB........",
    ".BTTTTTTBBBBBBB.",
    ".BFFFFFFFFFFFFB.",
    ".BFLLLLLLLLLLFB.",
    ".BFLLLLLLLLLLFB.",
    ".BFLLLLLLLLLLFB.",
    ".BFLLLLLLLLLLFB.",
    ".BFLLLLLLLLLLFB.",
    ".BFLLLLLLLLLLFB.",
    ".BFFFFFFFFFFFFB.",
    "..BBBBBBBBBBBB..",
    "................",
    "................",
]

_FILE = [
    "................",
    "..BBBBBBBB......",
    "..BFFFFFFBB.....",
    "..BFFFFFFBFB....",
    "..BFFFFFFBBBB...",
    "..BFFFFFFFFFB...",
    "..BFFFFFFFFFB...",
    "..BFFFFFFFFFB...",
    "..BFFFFFFFFFB...",
    "..BFFFFFFFFFB...",
    "..BFFFFFFFFFB...",
    "..BFFFFFFFFFB...",
    "..BFFFFFFFFFB...",
    "..BFFFFFFFFFB...",
    "..BBBBBBBBBBB...",
    "................",
]


def _draw(rows: list[str], palette: dict[str, str]) -> tk.PhotoImage:
    """Build a 16x16 PhotoImage from an ASCII pixel grid."""
    img = tk.PhotoImage(width=16, height=16)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if color := palette.get(ch):
                img.put(color, (x, y))
    return img


@dataclass(frozen=True)
class Icons:
    """Retained PhotoImage bundle for checkbox, folder, and file tree nodes."""

    checked: tk.PhotoImage
    unchecked: tk.PhotoImage
    folder: tk.PhotoImage
    file: tk.PhotoImage

    @classmethod
    def create(cls) -> Icons:
        """Create all 16x16 icons (requires an active Tk root)."""
        return cls(
            checked=_draw(
                _CHECKED,
                {"B": "#005A9E", "F": "#0078D4", "W": "#FFFFFF", "w": "#E6F2FA"},
            ),
            unchecked=_draw(_UNCHECKED, {"B": "#767676", "F": "#FFFFFF"}),
            folder=_draw(
                _FOLDER,
                {"B": "#6E8FAD", "T": "#D3E3F3", "F": "#B7D0E8", "L": "#9FC0E0"},
            ),
            file=_draw(_FILE, {"B": "#8E8E8E", "F": "#F7F7F7"}),
        )


__all__ = ["Icons"]
