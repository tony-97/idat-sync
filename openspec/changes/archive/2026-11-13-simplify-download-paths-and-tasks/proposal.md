# Proposal

## Why

`RecordingDownloader.download` retained a legacy `if target_path.suffix:` branch that falls back to a `yt-dlp` title/ID filename template when `RecordingContent.path` lacks a file extension, even though `SharepointApiAdapter` always resolves the target file path and `SyncDiff` requires exact file paths to match local files on subsequent runs. At the same time, `DownloadManager` duplicated sync-filtering responsibility between `diff_trees` and `should_download` while keeping an unused `root_dir` parameter on `prepare_download_tasks` and single-file `exists`/`get_mtime` methods on `FileSystemPort`.

## What Changes

- **BREAKING**: Remove `DownloadManager.should_download(task)` and the `root_dir` parameter from `DownloadManager.prepare_download_tasks(contents)` so `diff_trees(root_dir, remote_contents)` is the sole place comparing remote `Content` against local disk timestamps, and `prepare_download_tasks(contents)` purely maps `Content` items to `DownloadTask` instances via `create_download_task`.
- **BREAKING**: Remove `exists(path)` and `get_mtime(path)` from `FileSystemPort` and `LocalFileSystemAdapter`, and remove `DownloadTask.last_modified` from `application/ports.py`.
- Remove the `if target_path.suffix: ... else: ...` branch in `RecordingDownloader.download` so `RecordingContent.path` is unconditionally treated as a target file path (`target_path.parent.mkdir(parents=True, exist_ok=True)` and `output_path_template = f"{prefix}{os.path.abspath(target_path)}"`).
- Update `GLOSSARY.md` (`Content` and `RecordingContent` target file path definitions) and `agent-os/standards/global/layer-boundaries.md`.

## Non-Goals

- Changing how `SharepointApiAdapter.fetch_recordings` constructs `RecordingContent` paths or URLs.
- Modifying `SyncDiff.from_trees` diffing rules or `TaskOrchestrator` concurrency grouping.
- Retaining backward-compatible `root_dir`, `should_download`, `exists`, `get_mtime`, or `DownloadTask.last_modified` shims.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `course-synchronization`: Update `DownloadManager` and `FileSystemPort` requirements to remove `should_download`, `root_dir` in `prepare_download_tasks`, `FileSystemPort.exists`, `FileSystemPort.get_mtime`, and `DownloadTask.last_modified`, and require `RecordingDownloader.download` to unconditionally treat `RecordingContent.path` as the target file path.

## Applicable Standards

- `backend/content-strategy-dispatch`: Generic `Downloader[C]` task creation and target file path handling.
- `global/layer-boundaries`: `FileSystemPort` boundary (`scan_mtimes` and `ensure_dir`) and zero direct filesystem I/O in `application/`.
- `testing/composition-and-seams`: Seam verification for `RecordingDownloader`, `DownloadManager`, and `LocalFileSystemAdapter`.

## Impact

- `src/idat_sync/infrastructure/downloaders.py` (`RecordingDownloader.download`)
- `src/idat_sync/application/download_manager.py` (`DownloadManager.prepare_download_tasks`, removal of `should_download`)
- `src/idat_sync/application/ports.py` (`FileSystemPort`, `DownloadTask`)
- `src/idat_sync/infrastructure/filesystem.py` (`LocalFileSystemAdapter`)
- `GLOSSARY.md` and `agent-os/standards/global/layer-boundaries.md`
- `tests/test_sync_pipeline.py`
