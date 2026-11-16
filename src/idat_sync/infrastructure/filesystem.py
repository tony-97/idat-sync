import os
from pathlib import Path
import platform

from idat_sync.application.ports import FileSystemPort


def windows_long_path(path: Path | str) -> Path:
    """Return a \\\\?\\ extended-length Path on Windows, or Path(path) elsewhere."""
    if platform.system() != "Windows":
        return Path(path)
    raw = str(path)
    if raw.startswith("\\\\?\\"):
        return Path(raw)
    abspath = os.path.abspath(raw)
    if abspath.startswith("\\\\?\\"):
        return Path(abspath)
    if abspath.startswith("\\\\"):
        return Path(f"\\\\?\\UNC\\{abspath[2:]}")
    return Path(f"\\\\?\\{abspath}")


class LocalFileSystemAdapter(FileSystemPort):
    def scan_mtimes(self, root_dir: Path | str) -> dict[Path, int]:
        caller_root = Path(root_dir)
        os_root = windows_long_path(caller_root)
        local_map: dict[Path, int] = {}
        if os_root.exists():
            for p in os_root.rglob("*"):
                if p.is_file() and not p.name.endswith(".part"):
                    local_map[caller_root / p.relative_to(os_root)] = int(p.stat().st_mtime)
        return local_map

    def ensure_dir(self, path: Path | str) -> None:
        windows_long_path(path).mkdir(parents=True, exist_ok=True)

    def read_bytes(self, path: Path | str) -> bytes | None:
        try:
            return windows_long_path(path).read_bytes()
        except OSError:
            return None


__all__ = ["LocalFileSystemAdapter", "windows_long_path"]
