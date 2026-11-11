from pathlib import Path

from idat_sync.application.ports import FileSystemPort


class LocalFileSystemAdapter(FileSystemPort):
    def scan_mtimes(self, root_dir: Path | str) -> dict[Path, int]:
        root_path = Path(root_dir)
        local_map: dict[Path, int] = {}
        if root_path.exists():
            for p in root_path.rglob("*"):
                if p.is_file():
                    local_map[p] = int(p.stat().st_mtime)
        return local_map

    def ensure_dir(self, path: Path | str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    def exists(self, path: Path | str) -> bool:
        return Path(path).exists()

    def get_mtime(self, path: Path | str) -> int:
        target = Path(path)
        if target.exists():
            return int(target.stat().st_mtime)
        return 0


__all__ = ["LocalFileSystemAdapter"]
