# Design

## Context

See `proposal.md` for motivation. Across earlier refactors, `RecordingDownloader.download` retained a `target_path.suffix` fallback to `yt-dlp`'s `"%(title)s %(id)s.%(ext)s"` template, and `DownloadManager` retained both per-file timestamp checks (`should_download` via `FileSystemPort.exists` and `FileSystemPort.get_mtime`) and an unused `root_dir` parameter on `prepare_download_tasks` alongside tree-wide diffing (`diff_trees` via `FileSystemPort.scan_mtimes`).

## Goals / Non-Goals

**Goals:**
- Ensure `RecordingDownloader.download` unconditionally treats `RecordingContent.path` as the target file path.
- Make `DownloadManager.diff_trees` the single place comparing remote `Content` against local disk timestamps, and make `DownloadManager.prepare_download_tasks(contents)` a pure mapper from `Sequence[Content]` to `list[DownloadTask]`.
- Trim `FileSystemPort`, `LocalFileSystemAdapter`, and `DownloadTask` to the exact surface used by the application (`scan_mtimes`, `ensure_dir`, `DownloadTask.path`, `DownloadTask.download`).

**Non-Goals:**
- Keeping backward-compatible parameters or deprecated methods (`root_dir`, `should_download`, `exists`, `get_mtime`, `DownloadTask.last_modified`).
- Writing a new ADR (removing dead branches and duplicate sync-filtering paths is straightforward and not a hard-to-reverse trade-off).

## Decisions

1. **Unconditional target file path in `RecordingDownloader.download`**:
   - Remove `if target_path.suffix: ... else: ...` in `src/idat_sync/infrastructure/downloaders.py`.
   - Always execute `target_path.parent.mkdir(parents=True, exist_ok=True)` and `output_path_template = f"{prefix}{os.path.abspath(target_path)}"`, matching `MoodleDownloader`, `SharepointDownloader`, and `StringDownloader`.
   - *Alternative considered*: Keeping the `else` branch for extensionless paths. Rejected because `SharepointApiAdapter.fetch_recordings` always supplies a filename and any `yt-dlp`-generated filename diverges from `RecordingContent.path`, breaking `SyncDiff` on subsequent sync runs.

2. **Single sync-filtering responsibility in `DownloadManager`**:
   - Remove `should_download(self, task: DownloadTask) -> bool` and change `prepare_download_tasks` to `prepare_download_tasks(self, contents: Sequence[Content]) -> list[DownloadTask]`.
   - `diff_trees(root_dir, remote_contents)` owns comparing remote `Content` against `file_system.scan_mtimes(Path(root_dir))` to produce `SyncDiff.pending_contents`.
   - `prepare_download_tasks(contents)` owns matching `Content` items to `Downloader` strategies via `create_download_task(content)` (filtering out items no downloader handles, such as whitespace-only `StringContent`).
   - *Alternative considered*: Keeping `should_download` while only dropping `root_dir`. Rejected because `IDATSync.sync_course` already passes `diff.pending_contents` into `prepare_download_tasks`, causing redundant per-file filesystem checks and obscuring which method owns sync filtering.

3. **Trim unused port and task members**:
   - Remove `exists` and `get_mtime` from `FileSystemPort` (`src/idat_sync/application/ports.py`) and `LocalFileSystemAdapter` (`src/idat_sync/infrastructure/filesystem.py`).
   - Remove `last_modified` from `DownloadTask` (`src/idat_sync/application/ports.py`), keeping `DownloadTask.path`.

4. **Modules Touched & Confirmed Public Test Seams**:
   - **Modules touched**:
     - `src/idat_sync/infrastructure/downloaders.py`
     - `src/idat_sync/application/download_manager.py`
     - `src/idat_sync/application/ports.py`
     - `src/idat_sync/infrastructure/filesystem.py`
     - `GLOSSARY.md`
     - `agent-os/standards/global/layer-boundaries.md`
     - `tests/test_sync_pipeline.py`
   - **Confirmed public test seams**:
     - **`RecordingDownloader` seam**: `RecordingDownloader.download` with `yt_dlp.YoutubeDL` patched, verifying `target_path.parent.mkdir` and exact file `outtmpl`.
     - **`DownloadManager` seam**: `DownloadManager.diff_trees` and `DownloadManager.prepare_download_tasks` verified against `FileSystemPort` and `Downloader` strategies.
     - **`FileSystemPort` / `LocalFileSystemAdapter` seam**: `LocalFileSystemAdapter` (`scan_mtimes`, `ensure_dir`) and port surface verified in `tests/test_sync_pipeline.py`.

## Risks / Trade-offs

- **[Breaking existing tests that expected `prepare_download_tasks` to filter existing local files on disk]** → Update `test_prepare_download_tasks_creates_and_filters_existing_files` and `test_local_filesystem_adapter_and_download_manager_seam` in `tests/test_sync_pipeline.py` to test tree diffing via `diff_trees` (`diff.pending_contents`) and task mapping via `prepare_download_tasks`.
