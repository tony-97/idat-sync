# Design

## Context

See [proposal.md](./proposal.md) for motivation and capability impact. Long-lived architectural choices are recorded in [ADR-0006](../../../docs/adr/0006-streaming-sync-pipeline.md) (streaming per-course discovery/download pipeline), [ADR-0007](../../../docs/adr/0007-pywebview-and-static-astro-ui.md) (`pywebview` driving UI & `WebViewDriver` SSO), and [ADR-0008](../../../docs/adr/0008-vite-solidjs-single-file-ui.md) (replacing static Astro with Vite + SolidJS single-file bundle).

Key constraints verified in the codebase:
- `src/idat_sync/domain/` and `src/idat_sync/application/` have zero imports from `infrastructure/`, `ui/`, or vendor SDKs; `src/idat_sync/application/task_orchestrator.py` has zero imports from `events.py` or `threading`.
- `src/idat_sync/infrastructure/moodle_client.py` has zero imports from `asyncio` or `time`; `sharepoint_client.py` and `downloaders.py` have zero imports from `time`.
- `office365-rest-python-client` (`>= 3.3.1`) `File.download_session_async` already supports `chunk_size` and `progress=Callable[[Progress], None]` where `Progress` exposes `(done, total)`; `MoodleClient.download_file` already streams 256 KB `httpx` chunks; `RecordingDownloader._download_sync` already wires `yt-dlp`'s `progress_hooks` inside `asyncio.to_thread(...)`.
- `CourseSync.html` at the repo root defines the visual structure and interaction model (responsive 3-pane desktop `>= 960px` and mobile bottom-sheet Activity drawer, term-grouped sidebar with tri-state checkboxes, compact folder headers, filter chips, and light/dark theme) adapted to real streaming discovery (no upfront full-course scan, no Pause/Resume, no fake titlebar/MFA resend, native pywebview folder picker).

## Goals / Non-Goals

**Goals:**
- Stream live byte progress (`downloaded`, `total`) from Moodle, SharePoint, and `yt-dlp` recordings into `IDATSync` without breaking hexagonal layer boundaries or existing `TaskOrchestrator` / `bootstrap.py` wiring.
- Classify discovered items into `ContentSyncState` (`SYNCED`, `NEW`, `OUTDATED`) and group courses by parsed academic term (`get_course_term`).
- Deliver smooth, zero-teardown UI updates (~6.7 ticks/sec per active file) via targeted `MainApp.push_progress(...)` (`window.__onFileProgress(...)`) paired with SolidJS `createStore` + `reconcile` in a single-file zero-Node-runtime `src/idat_sync/ui/index.html`.
- Support single-course sync (`sync_course`), multi-course/tri-state term selection (`toggle_term`), non-destructive `cancel_sync()`, `retry_load()`, and clear error surfacing in `.syncbar` / onboarding card and ActivityPanel.

**Non-Goals:**
- Pause/Resume mid-sync or upfront startup scanning across courses.
- Pre-download file sizes for queued/synced items or persistent `folder_path` storage.
- Touching `bootstrap.py`, `task_orchestrator.py`, `MoodleAdapter`, `SharepointApiAdapter`, or `AsyncRateLimiter`.

## Decisions

See [ADR-0008](../../../docs/adr/0008-vite-solidjs-single-file-ui.md) for replacing Astro with Vite + SolidJS + Tailwind + `vite-plugin-singlefile`. Tactical design choices:

1. **Three-state `ContentSyncState` replacing `is_pending` booleans**:
   - Define `ContentSyncState(StrEnum)` (`SYNCED`, `NEW`, `OUTDATED`) and `classify_content(content, local_mtimes)` in `domain/sync.py`, extended in `DownloadManager.classify_content` to return `OUTDATED` for existing `StringContent(last_modified=0)` items whose local bytes differ (or are unreadable) via `FileSystemPort.read_bytes`.
   - Update `CourseFileDiscovered(course, path, state: ContentSyncState)` so `CourseTree` distinguishes `New` vs. `Outdated` without extra filesystem probes.
   - *Alternative rejected*: Keeping `is_pending: bool` alongside a second `is_new: bool` flag (redundant state combinations).

2. **Loop-bound `ProgressCallback` at the `Downloader` port + cumulative `yt-dlp` bridging**:
   - Define `ProgressCallback = Callable[[int, int | None], None]` in `application/ports.py` as an optional parameter (`on_progress: ProgressCallback | None = None`) on `Downloader.download` and `DownloadTask.download`. When `on_progress is None`, `DownloadTask.download()` calls `downloader.download(content)` with 1 argument so legacy/custom `Downloader` subclasses omitting `on_progress` keep working.
   - `MoodleClient.download_file` parses `Content-Length` (numeric positive int, else `None`) and invokes `on_progress(downloaded, total)` after each written 256 KB chunk.
   - `SharepointClient.download_file` lowers `download_session_async` `chunk_size` from `1 MB` to `256 * 1024` (matching Moodle) and wraps `on_progress` into `progress=lambda p: on_progress(p.done, p.total)` so no `office365` types cross the client boundary.
   - `RecordingDownloader.download` captures `loop = asyncio.get_running_loop()` before `asyncio.to_thread(...)`, tracks `completed_bytes` across finished DASH passes (`bestvideo+bestaudio` downloads video then audio sequentially), computes `total = status.get("total_bytes") or status.get("total_bytes_estimate")`, and schedules `loop.call_soon_threadsafe(on_progress, completed_bytes + downloaded, completed_bytes + total if total is not None else None)` only when `not cancel_event.is_set()`. This keeps `application/` completely unaware of worker threads and prevents bytes from jumping back to `0` when the audio stream starts.
   - *Alternative rejected*: Putting `call_soon_threadsafe` inside `IDATSync` (leaks threading concerns into application layer) or adding a `Merging` status via `postprocessor_hooks` (clamped `0..99%` while `Downloading` already covers FFmpeg muxing cleanly).

3. **Per-task `0.15s` throttling and post-completion guard in `IDATSync._CourseStreamRun`**:
   - Accept optional `progress_interval: float = 0.15` on `IDATSync.__init__` (tests can pass `0.0` for deterministic zero-sleep checks).
   - Inside `_CourseStreamRun._run_single_task`, wrap emission with a local `finished = False` flag (set `True` in `finally:`) and `last_emit: float | None = None`. The first callback emits `TaskDownloadProgress` immediately; subsequent callbacks emit only when `time.monotonic() - last_emit >= self._progress_interval`. Because `finished` flips in `finally:` before/alongside `TaskDownloadCompleted` / `TaskDownloadFailed` / cancellation, late thread-scheduled callbacks can never emit stale progress events out of order.
   - `log_sync_event` treats `TaskDownloadProgress` as a silent no-op so `log.err` is never flooded.
   - *Alternative rejected*: Unthrottled emission (`yt-dlp` fires 200–1,000+ hooks/sec across `window.evaluate_js` IPC and produces noisy 1 ms speed samples) or throttling inside infrastructure downloaders (violates `no time import` AST boundary on `downloaders.py` / clients).

4. **Dual-channel pywebview bridge (`push_state` + `push_progress`) and pure-Python `speed_bps`**:
   - Structural lifecycle transitions (`CourseSyncStarted`, `CourseFileDiscovered`, `CourseDiscoveryCompleted`, `TaskDownloadStarted`, `TaskDownloadCompleted`, `TaskDownloadFailed`, `CourseSyncCompleted`, selection/cancel/error changes) push full snapshots via `MainApp.push_state()` -> `window.__onPythonState(state)`.
   - `TaskDownloadProgress` updates `CourseTree.set_file_progress(path, downloaded, total)` — computing exponential/windowed `speed_bps` via `time.monotonic()` and clamping `percent` to `0..99` when `total > 0` (`None` when `total is None`) — and pushes ONLY the targeted dict `{id, course_id, downloaded, total, percent, progress, speed_bps}` via `MainApp.push_progress(payload)` -> `window.__onFileProgress(payload)`.
   - Keeping `speed_bps` and `percent` inside `CourseTree` ensures `get_state()`, `push_state()`, and `push_progress()` always agree and remain directly unit-testable in pure Python without a browser.

5. **Non-destructive `SyncFrame.cancel_sync()` and single-job `_busy` semantics**:
   - `SyncFrame` runs `do_sync()` (selected courses, falling back to `in_progress_ids`, then all loaded courses) and `sync_course(course_id)` through `AppFrame._busy`, preventing concurrent sync jobs.
   - `cancel_sync()` cancels `self._active_task` (only when a sync is active) without closing `self.idat_sync`. `do_sync` / `sync_course` catches `asyncio.CancelledError` inside `_run_sync` only when triggered by user cancellation (`_cancel_requested` flag) vs. `aclose()` shutdown: on user cancel, it calls `self.course_tree.cancel_sync()` (reverting `DOWNLOADING` and queued `NEW`/`OUTDATED` nodes to their discovered `NEW`/`OUTDATED` state with `percent=0` while preserving `COMPLETED`/`SYNCED` rows), sets `self.status = "Sync cancelled."`, resets `is_syncing = False` via `_busy`, and notifies the UI; on `aclose()`, `CancelledError` still propagates as required by existing shutdown tests.

6. **SolidJS `CourseSync` frontend architecture (`frontend/`)**:
   - Replace Astro packages in `frontend/package.json` with `vite`, `solid-js`, `vite-plugin-solid`, `tailwindcss`, `@tailwindcss/vite`, `vite-plugin-singlefile`, and `typescript`. `npm run build` outputs directly to `../src/idat_sync/ui/index.html`.
   - Maintain a reactive Solid store (`createStore`) hydrated from `get_state()` and updated via `reconcile(next, { key: "id" })` on `__onPythonState`, plus a keyed `progressById` store updated directly on `__onFileProgress` so only the affected file's bar width, byte text, percentage, and `MB/s` text mutate in place.
   - Component decomposition: `AuthOnboarding` (Login, authenticating spinner, 6-digit MFA without fake phone/resend, verifying spinner, empty-folder onboarding with startup error + Retry/Sign out), `Sidebar` (pathbar + native Change + theme/logout controls, mobile collapsible course switcher, parsed-term groups with newest expanded and tri-state term + course checkboxes, `.selbar`), `SyncBar` (status strip showing active progress + Cancel, errors + Retry/Sign out, or primary Sync action), `CourseMain` (course header with stats + always-enabled-when-idle `Sync course`, filter chips with conditional `Failed`, recursive `FolderNode` with automatic single-child `Parent › Child` compaction, `FileRow` with extension badge + live bar/bytes/`MB/s` + status pill), and `ActivityPanel` (desktop right column / mobile bottom sheet with overall progress, `Downloading now` jump-to-file cards, `Up next`, `Finished`, `Failed`, and skipped count).
   - Default desktop window size widens from `760x560` to `1100x720` so desktop users land directly in the 3-pane (`>= 960px`) layout while narrow/Android viewports use the responsive stacked + bottom-sheet layout.

## Risks / Trade-offs

- [Stale `call_soon_threadsafe` progress callbacks arriving from `yt-dlp` worker thread after task completion or cancellation] → Guarded both in `RecordingDownloader` (`not cancel_event.is_set()`) and in `_CourseStreamRun._run_single_task` (`finished = True` in `finally:`), guaranteeing zero out-of-order `TaskDownloadProgress` events.
- [Differentiating user-initiated `cancel_sync()` from shutdown `aclose()` task cancellation in `SyncFrame`] → Track an explicit `_cancel_requested: bool` flag set only by `cancel_sync()` and cleared in `finally:`, so `aclose()` continues to propagate `asyncio.CancelledError` cleanly while `cancel_sync()` settles cleanly into `"Sync cancelled."`.
- [Removing Astro breaks existing `frontend/` files and AST/HTML checks referencing `.astro`] → Cleanly replace `frontend/` source files, rebuild `src/idat_sync/ui/index.html`, and update `agent-os/standards/global/layer-boundaries.md`, `agent-os/standards/testing/composition-and-seams.md`, and tests referencing old `is_pending` or Astro assumptions.
- [Pylint strict thresholds (`fail-under = 10.0`, `max-attributes = 7`, `max-statements = 35`, `max-args = 5`)] → Keep `CourseTree` progress/activity logic split cleanly into small helper dataclasses (`_FileProgress`, `_ActivitySnapshot` / pure helpers) and verify `ruff` + `pylint` after every slice.

## Modules Touched & Confirmed Public Test Seams

- **Production Python modules (13)**: `src/idat_sync/domain/sync.py`, `src/idat_sync/domain/course.py`, `src/idat_sync/domain/__init__.py`, `src/idat_sync/application/ports.py`, `src/idat_sync/application/events.py`, `src/idat_sync/application/download_manager.py`, `src/idat_sync/application/sync_service.py`, `src/idat_sync/infrastructure/moodle_client.py`, `src/idat_sync/infrastructure/sharepoint_client.py`, `src/idat_sync/infrastructure/downloaders.py`, `src/idat_sync/ui/app.py`, `src/idat_sync/ui/sync_frame.py`, `src/idat_sync/ui/course_tree.py`.
- **Frontend & bundled asset**: `frontend/` (Vite + SolidJS + Tailwind CSS + TypeScript) compiling to `src/idat_sync/ui/index.html`.
- **Docs & standards**: `docs/adr/0008-vite-solidjs-single-file-ui.md` (created), `docs/adr/0007-pywebview-and-static-astro-ui.md` (updated), `agent-os/standards/global/layer-boundaries.md`, `agent-os/standards/testing/composition-and-seams.md`.
- **Confirmed public test seams (12)**:
  1. `domain.sync` (`ContentSyncState`, `classify_content`)
  2. `domain.course` (`get_course_term`, `get_in_progress_course_ids`)
  3. `application.ports` & `DownloadManager` (`ProgressCallback`, `DownloadTask.download`, `DownloadManager.classify_content`)
  4. `application.events` (`CourseFileDiscovered`, `TaskDownloadProgress`, `log_sync_event`)
  5. `IDATSync` (`sync_course`, `sync_courses`, `progress_interval` throttling & post-finish guard)
  6. `MoodleClient` (`download_file` progress & `Content-Length` parsing)
  7. `SharepointClient` (`download_file` `chunk_size=256 * 1024` & `progress=` translation)
  8. `*Downloader` (`MoodleDownloader`, `SharepointDownloader`, `StringDownloader`, `RecordingDownloader` cumulative DASH progress & cancel safety)
  9. `CourseTree` (term metadata, stable file IDs, `New`/`Outdated`/`Failed`, `set_file_progress` with `speed_bps` & `0..99%`, `cancel_sync`, `activity_to_dict`)
  10. `SyncFrame` (`do_sync`, `sync_course`, `toggle_term`, `cancel_sync`, `retry_load`, `push_progress` vs. `push_state`, error/auth retention)
  11. `MainApp` (`push_progress`, `1100x720` default window, `_JsApi`, bundled `index.html` smoke check & `npm run build` / `tsc`)
  12. AST architecture boundary checks (`tests/test_architecture.py`) & `TestRealCourseContentsFixture` (`tests/test_real_data.py`).
