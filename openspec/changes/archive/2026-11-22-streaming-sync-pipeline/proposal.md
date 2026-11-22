# Proposal

## Why

Course synchronization currently hits a full-course discovery barrier before downloading a single file or rendering rows in `CourseTree`: inline `StringContent` artifacts and zero-I/O `MoodleContent` items sit idle for minutes while rate-limited SharePoint link resolutions and recording queries run sequentially behind `sharepoint_limiter`. Converting per-course discovery, diffing, download scheduling, and UI updates into a streaming pipeline lets Moodle downloads, HTML writes, and live tree updates start immediately at `t = 0` while SharePoint discovery runs concurrently.

## What Changes

- **BREAKING**: Replace `SyncDiff`, `SyncEntry`, and `SyncStatus` in `src/idat_sync/domain/sync.py` (and re-exports in `domain/__init__.py`) with a pure per-item `is_content_pending(content, local_mtimes) -> bool` helper, and add a stateful `Path -> Path` `PathDisambiguator` to `src/idat_sync/domain/path_utils.py`.
- **BREAKING**: Replace `CourseRepository.get_course_content` with `stream_course_content(course, course_dir) -> AsyncIterator[Content]` resolving one concurrent task per `CourseModule` (preserving intra-module `module.contents` order sequentially inside each module task and yielding via Python 3.12-compatible `for coro in asyncio.as_completed(tasks):` with full cancellation cleanup in `finally`), and change `list_url_content` to take a single `ModuleContent`.
- **BREAKING**: Replace `DownloadManager.diff_trees` and `prepare_download_tasks` with `is_pending(content, local_mtimes) -> bool` (combining `is_content_pending` with `StringContent` byte comparison via `FileSystemPort.read_bytes`) while keeping `create_download_task`.
- **BREAKING**: Replace `TaskOrchestrator.create_coroutines` with `create_channel() -> TaskChannel` exposing per-group unbounded `asyncio.Queue` routing (`enqueue`, `close`, `create_workers`) without importing `events` or `threading`.
- **BREAKING**: Replace batch `CourseFilesDiscovered` in `src/idat_sync/application/events.py` with `CourseSyncStarted(course, course_dir)`, per-file `CourseFileDiscovered(course, path, is_pending)` (logged at `DEBUG`), and `CourseDiscoveryCompleted(course, synced_count, pending_count)` (logged at `INFO`).
- **BREAKING**: Remove `IDATSync.get_course_remote_tree` and `check_course_sync`; update `IDATSync.sync_course` to run `stream_course_content` and `get_recordings` concurrently alongside group download workers inside `asyncio.TaskGroup`, isolate `get_recordings` `RemoteServiceError` (logged immediately at `ERROR` and recorded for `CourseSyncCompleted`), apply `PathDisambiguator`, and emit `CourseFileDiscovered` strictly before enqueueing each download task.
- Fix `SharepointClient.get_client_from_url` to return a fresh `ClientContext` on fallback instead of mutating shared `self.client` across concurrent tasks.
- **BREAKING**: Replace `CourseTree.show_files` with incremental `start_course`, `add_file`, and `finish_discovery` transitioning `"Fetching contents..."` (`"..."`) -> `"Fetching & Syncing"` (`"done/total+"`) -> `"Syncing"` (`"pct%"`) -> `"Completed"` / `"Failed"`, and update `SyncFrame` event handling.
- Update `agent-os/standards/` docs to replace stale `SyncDiff.from_trees` and `create_coroutines` references.

## Non-Goals

- Running multiple courses concurrently in `IDATSync.sync_courses` (courses stay sequential).
- Converting `UrlContentStrategy.list_url_content` or `SharepointApiAdapter.fetch_folder_contents` into async generators.
- Preserving backward compatibility for removed batch methods or `SyncDiff`.
- Modifying `bootstrap.py`, `application/ports.py`, `MoodleAdapter`, `SharepointApiAdapter`, `*Downloader`, or `AsyncRateLimiter`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `course-synchronization`: Replace batch tree collection, `SyncDiff.from_trees`, static `TaskOrchestrator.create_coroutines`, batch `CourseFilesDiscovered`, and `CourseTree.show_files` with a streaming per-course discovery/download/UI pipeline, per-item `is_content_pending`/`DownloadManager.is_pending`, `PathDisambiguator`, `TaskChannel`, isolated `get_recordings` `RemoteServiceError`, and fresh fallback `ClientContext` in `SharepointClient.get_client_from_url`.

## Impact

- **Modules touched**: `src/idat_sync/domain/sync.py`, `src/idat_sync/domain/__init__.py`, `src/idat_sync/domain/path_utils.py`, `src/idat_sync/application/course_repository.py`, `src/idat_sync/application/download_manager.py`, `src/idat_sync/application/task_orchestrator.py`, `src/idat_sync/application/events.py`, `src/idat_sync/application/sync_service.py`, `src/idat_sync/infrastructure/sharepoint_client.py`, `src/idat_sync/ui/course_tree.py`, `src/idat_sync/ui/sync_frame.py`.
- **Docs & standards touched**: `GLOSSARY.md` (updated), `docs/adr/0006-streaming-sync-pipeline.md` (created), `agent-os/standards/global/layer-boundaries.md`, `agent-os/standards/backend/content-strategy-dispatch.md`, `agent-os/standards/testing/composition-and-seams.md`.
- **Applicable `agent-os` standards**: `global/layer-boundaries`, `backend/content-strategy-dispatch`, `backend/client-adapter-acl`, `testing/composition-and-seams`.
