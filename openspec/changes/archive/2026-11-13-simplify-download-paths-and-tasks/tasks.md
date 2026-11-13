# Tasks

## 1. RecordingDownloader Target File Path

- [x] 1.1 Write a failing test at the `RecordingDownloader` seam in `tests/test_sync_pipeline.py` verifying that `RecordingDownloader.download` unconditionally creates `Path(content.path).parent` and sets `yt_dlp` `outtmpl` to the exact target file path even when `Path(content.path).suffix == ""`, then remove the `if target_path.suffix:` branch in `src/idat_sync/infrastructure/downloaders.py` and verify the test passes.

## 2. DownloadManager, FileSystemPort, and DownloadTask Surface

- [x] 2.1 Write failing tests at the `DownloadManager` and `FileSystemPort` / `LocalFileSystemAdapter` seams in `tests/test_sync_pipeline.py` verifying that `prepare_download_tasks(contents)` maps `Content` to `DownloadTask` without filesystem calls and rejects `root_dir`, and that `should_download`, `FileSystemPort.exists`, `FileSystemPort.get_mtime`, `LocalFileSystemAdapter.exists`, `LocalFileSystemAdapter.get_mtime`, and `DownloadTask.last_modified` do not exist; then remove those methods/parameters from `src/idat_sync/application/download_manager.py`, `src/idat_sync/application/ports.py`, and `src/idat_sync/infrastructure/filesystem.py` and verify `tests/test_sync_pipeline.py` passes.

## 3. Final Verification

- [x] 3.1 Run the full test suite (`python -m unittest discover -s tests`), typecheck, and lint to verify zero regressions across all seams.
