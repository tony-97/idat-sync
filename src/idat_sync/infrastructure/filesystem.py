from pathlib import Path

from idat_sync.application.ports import FileSystemPort


class LocalFileSystemAdapter(FileSystemPort):
    def scan_mtimes(self, root_dir: Path | str) -> dict[Path, int]:
        root_path = Path(root_dir)
        local_map: dict[Path, int] = {}
        if root_path.exists():
            for p in root_path.rglob("*"):
                if p.is_file() and not p.name.endswith(".part"):
                    local_map[p] = int(p.stat().st_mtime)
        return local_map

    def ensure_dir(self, path: Path | str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    def read_bytes(self, path: Path | str) -> bytes | None:
        try:
            return Path(path).read_bytes()
        except OSError:
            return None


__all__ = ["LocalFileSystemAdapter"]
