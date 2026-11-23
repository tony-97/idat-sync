# Proposal

## Why

File downloads in `idat-sync` currently show only binary `0%` / `100%` states until completion, leaving long SharePoint documents, Moodle archives, and Teams class recordings looking frozen even though all three network clients already stream natively. Meanwhile, the static Astro UI (`frontend/`) relies on vanilla JavaScript calling `treeBody.replaceChildren()` on every state push, which cannot handle live per-file progress ticks or the richer multi-pane responsive layout prototyped in `CourseSync.html` without DOM teardown and WebView IPC stutter.

## What Changes

- **BREAKING**: Replace `is_content_pending` in `src/idat_sync/domain/sync.py` (and `domain/__init__.py`) and `DownloadManager.is_pending` with `ContentSyncState(StrEnum)` (`SYNCED`, `NEW`, `OUTDATED`) and `classify_content(content, local_mtimes) -> ContentSyncState`; add `get_course_term(course) -> str` to `src/idat_sync/domain/course.py`.
- Add `ProgressCallback = Callable[[int, int | None], None]` and optional `on_progress: ProgressCallback | None = None` to `Downloader.download` and `DownloadTask.download` in `src/idat_sync/application/ports.py`.
- **BREAKING**: Update `CourseFileDiscovered` in `src/idat_sync/application/events.py` to carry `state: ContentSyncState` instead of `is_pending: bool`, add frozen `TaskDownloadProgress(path, downloaded, total)` to `SyncEvent` (silent in `log_sync_event`), and emit per-task throttled (`0.15s` default, first tick immediate, post-completion guarded) progress events in `IDATSync`.
- Wire byte progress reporting in `MoodleClient.download_file` (`httpx` stream + `Content-Length`), `SharepointClient.download_file` (`download_session_async` with `256 KB` chunks and `progress=`), `MoodleDownloader`, `SharepointDownloader`, and `RecordingDownloader` (`yt-dlp` `_progress_hook` bridged via `call_soon_threadsafe` with cumulative bytes across video+audio passes).
- Add `MainApp.push_progress(...)` (`window.__onFileProgress(...)`), widen the default desktop window to `1100x720`, and extend `SyncFrame`, `_JsApi`, and `CourseTree` with parsed-term grouping, tri-state term checkboxes (`toggle_term`), `sync_course(course_id)`, `cancel_sync()`, `retry_load()`, stable file node IDs, pure-Python `speed_bps` + clamped `0..99%` progress, failed-file error strings, and Activity projection.
- **BREAKING**: Replace static Astro in `frontend/` with Vite + SolidJS (`createStore` + `reconcile`) + Tailwind CSS + `vite-plugin-singlefile` compiling to the single self-contained `src/idat_sync/ui/index.html` per [ADR-0008](../../../docs/adr/0008-vite-solidjs-single-file-ui.md), implementing the responsive 3-pane desktop and mobile bottom-sheet `CourseSync` UI.

## Non-Goals

- Pause/Resume mid-sync.
- Upfront full-course discovery scanning on startup (stays streaming on sync per ADR-0006).
- Pre-download file sizes for queued or already-synced items.
- Persisting `folder_path` across app restarts (remains ephemeral per session).
- Fake window titlebar chrome, MFA resend, or external CDN font stylesheets.
- Modifying `TaskOrchestrator` (`task_orchestrator.py`) or `bootstrap.py`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `course-synchronization`: Add 3-state `ContentSyncState` classification, `get_course_term`, optional downloader progress callbacks across Moodle/SharePoint/Recordings, throttled `TaskDownloadProgress` `SyncEvent` streaming, targeted `push_progress` bridge updates, `Cancel`/`Retry`/per-course sync actions, and the SolidJS `CourseSync` UI.
- `authentication`: Update the bundled UI specification in `src/idat_sync/ui/index.html` from Astro `.astro` components to the Vite + SolidJS single-file bundle (`ADR-0008`) and extend `MainApp` with `push_progress` and `1100x720` default window sizing while keeping `LoginFrame` and `SyncFrame` credential-retention invariants intact.

## Impact

- **Modules touched**: `src/idat_sync/domain/sync.py`, `src/idat_sync/domain/course.py`, `src/idat_sync/domain/__init__.py`, `src/idat_sync/application/ports.py`, `src/idat_sync/application/events.py`, `src/idat_sync/application/download_manager.py`, `src/idat_sync/application/sync_service.py`, `src/idat_sync/infrastructure/moodle_client.py`, `src/idat_sync/infrastructure/sharepoint_client.py`, `src/idat_sync/infrastructure/downloaders.py`, `src/idat_sync/ui/app.py`, `src/idat_sync/ui/sync_frame.py`, `src/idat_sync/ui/course_tree.py`, `frontend/`, `src/idat_sync/ui/index.html`.
- **Docs & standards touched**: `docs/adr/0008-vite-solidjs-single-file-ui.md` (created), `docs/adr/0007-pywebview-and-static-astro-ui.md` (updated), `agent-os/standards/global/layer-boundaries.md`, `agent-os/standards/testing/composition-and-seams.md`.
- **Applicable `agent-os` standards**: `global/layer-boundaries`, `backend/client-adapter-acl`, `backend/content-strategy-dispatch`, `testing/composition-and-seams`.
