"""Local filesystem adapter and Windows extended-length path helpers."""

import os
import platform
from pathlib import Path

from idat_sync.application.ports import FileSystemPort


def windows_long_path(path: Path | str) -> Path:
    r"""Return a ``\\?\`` extended-length Path on Windows, or Path(path) elsewhere."""
    if platform.system() != "Windows":
        return Path(path)
    raw = str(path)
    if raw.startswith("\\\\?\\"):
        return Path(raw)
    abspath = os.path.abspath(raw)  # noqa: PTH100
    if abspath.startswith("\\\\?\\"):
        resolved = abspath
    elif abspath.startswith("\\\\"):
        resolved = f"\\\\?\\UNC\\{abspath[2:]}"
    else:
        resolved = f"\\\\?\\{abspath}"
    return Path(resolved)


class LocalFileSystemAdapter(FileSystemPort):
    """Filesystem port implementation backed by local disk."""

    def scan_mtimes(self, root_dir: Path | str) -> dict[Path, int]:
        """Scan ``root_dir`` recursively and return relative-path mtime map."""
        caller_root = Path(root_dir)
        os_root = windows_long_path(caller_root)
        local_map: dict[Path, int] = {}
        if os_root.exists():
            for path in os_root.rglob("*"):
                if path.is_file() and not path.name.endswith(".part"):
                    local_map[caller_root / path.relative_to(os_root)] = int(
                        path.stat().st_mtime
                    )
        return local_map

    def ensure_dir(self, path: Path | str) -> None:
        """Ensure directory ``path`` exists."""
        windows_long_path(path).mkdir(parents=True, exist_ok=True)

    def read_bytes(self, path: Path | str) -> bytes | None:
        """Read bytes from ``path``, or return None on OSError."""
        try:
            return windows_long_path(path).read_bytes()
        except OSError:
            return None


__all__ = ["LocalFileSystemAdapter", "windows_long_path"]
