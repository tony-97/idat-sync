# Design

## Context

See [proposal.md](./proposal.md) for motivation and capability impact. Long-lived architectural trade-offs and supersession of batch `SyncDiff.from_trees` / `get_course_remote_tree` assumptions in [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) and [ADR-0004](../../../docs/adr/0004-module-id-folders-and-filepath-preservation.md) are recorded in [ADR-0006](../../../docs/adr/0006-streaming-sync-pipeline.md).

Key constraints verified in the codebase:
- Production wiring in `bootstrap.py` uses two independent `AsyncRateLimiter` instances (`moodle_limiter` and `sharepoint_limiter`, default `30.0s`). `MoodleAdapter.list_url_content` does zero network I/O, while `SharepointApiAdapter.list_url_content`, `fetch_recordings`, `SharepointDownloader`, and `RecordingDownloader` share `sharepoint_limiter`.
- `pyproject.toml` targets `requires-python = ">=3.12"` (Ruff/Pylint `py312`), where `asyncio.as_completed(tasks)` is a synchronous iterator (`for coro in asyncio.as_completed(tasks):`), not an async iterator.
- `SharepointClient.get_client_from_url` currently falls back to a shared mutable `self.client` instance whose pending query queue is mutated in `load_entity` before acquiring `self.rate_limiter`.

## Goals / Non-Goals

**Goals:**
- Start writing inline `StringContent`, downloading `MoodleContent`, and rendering `CourseTree` rows at `t = 0` while SharePoint link and recording lookups run concurrently.
- Preserve deterministic path disambiguation, per-item/per-course error isolation, and immediate `CredentialsExpiredError` cancellation across discovery and download workers.
- Keep `bootstrap.py`, `application/ports.py`, `MoodleAdapter`, `SharepointApiAdapter`, `*Downloader`, and `AsyncRateLimiter` unchanged.

**Non-Goals:**
- Concurrent course execution across `IDATSync.sync_courses` (courses remain sequential).
- Streaming inside `UrlContentStrategy.list_url_content` or `SharepointApiAdapter.fetch_folder_contents`.
- Backward compatibility for removed batch APIs (`SyncDiff`, `get_course_content`, `get_course_remote_tree`, `check_course_sync`, `diff_trees`, `prepare_download_tasks`, `create_coroutines`, `CourseFilesDiscovered`, `show_files`).

## Decisions

See [ADR-0006](../../../docs/adr/0006-streaming-sync-pipeline.md) for the high-level streaming architecture, per-module task granularity, separate `get_recordings` orchestration, `TaskChannel`, and removal of `SyncDiff`. Tactical design choices:

1. **Per-module cancellation and Python 3.12 `as_completed` in `CourseRepository.stream_course_content`**:
   - Build pure `inline_items` and `(module.contents, mod_name)` jobs first via `_collect_stream_plan` (zero task creation during planning, preventing task leaks if a later `in_folder` check raises), then spawn one `asyncio.Task` per named `CourseModule` with non-empty `module.contents`. Inside `_resolve_module`, resolve `module.contents` sequentially in Moodle list order, preserving deterministic ordering under `<bounded_name>-[<module.id>]` with a single task layer and a single `finally` cancellation block.
   - Call `await asyncio.sleep(0)` before yielding `inline_items` so resolution tasks start running immediately, yield `inline_items` in section/module order, then iterate `for coro in asyncio.as_completed(tasks):` (compatible with Python 3.12) and cancel/gather unfinished tasks in `finally`.
   - *Alternative rejected*: `async for ... in asyncio.as_completed(tasks)` (Python 3.13+ only), nested per-URL tasks inside `_resolve_module` (redundant second task layer and cancel/gather boilerplate), or flat per-URL tasks without module grouping (network-race-dependent intra-module collision numbering).

2. **Pure `Path -> Path` `PathDisambiguator` in `domain/path_utils.py`**:
   - Keep `PathDisambiguator` operating solely on `Path` (using `as_posix().casefold()`, `split_extension`, and `safe_suffixed_segment`) so `domain/path_utils.py` has zero dependency on `domain/content.py`. `IDATSync` calls `replace(item, path=new_path)` only when `new_path != item.path`.

3. **Concurrent discovery + worker `TaskGroup` and FIFO event ordering in `IDATSync.sync_course`**:
   - Run one discovery coordinator task and all `channel.create_workers(...)` tasks in the same `asyncio.TaskGroup`, writing all `SyncEvent`s to a single `event_queue: asyncio.Queue[SyncEvent | None]`.
   - Inside the discovery coordinator, run `stream_course_content` and `get_recordings` concurrently (closing `channel` and emitting `CourseDiscoveryCompleted(course, synced_count, pending_count)` once both complete). Catch non-auth `RemoteServiceError` from `get_recordings`, log immediately at `logging.ERROR`, and append to `errors` without failing the coordinator.
   - Emit `CourseFileDiscovered(course, item.path, is_pending)` onto `event_queue` **strictly before** calling `channel.enqueue(task)` so a fast worker (`StringDownloader`) can never deliver `TaskDownloadStarted(path)` ahead of `CourseFileDiscovered(path)` to `CourseTree`.
   - When unwrapping `except* CredentialsExpiredError as eg:` around `TaskGroup`, flatten any nested `ExceptionGroup` so `sync_course` always re-raises the leaf `CredentialsExpiredError` (never an `ExceptionGroup`).

4. **Idempotent `TaskChannel.close()` and fail-fast `enqueue()` in `TaskOrchestrator`**:
   - `TaskChannel` maps each configured `Downloader` to its group index `0..N-1` and owns one unbounded `asyncio.Queue[DownloadTask | None]` per group. `enqueue(task)` raises `ValueError` for an unregistered downloader; `close()` tracks `_closed: bool` so repeated calls are safe no-ops. `task_orchestrator.py` keeps zero imports from `events.py` or `threading`.

5. **Fresh fallback `ClientContext` in `SharepointClient.get_client_from_url`**:
   - Store `self._academic_site_url = academic_site_url.rstrip("/")` instead of reusing a shared `self.client` instance on fallback, returning `self.get_client(self._academic_site_url)` while continuing to share `self._transport`.

6. **Discovery-aware progress rendering in `CourseTree`**:
   - `_CourseNodes` tracks `course_dir: Path | None`, `discovering: bool`, and `progress: Progress`. While `discovering` is `True`, both `add_file` and `set_file_status(..., RowStatus.COMPLETED)` render `(RowStatus.FETCHING_AND_SYNCING, f"{done}/{total}+")`. `finish_discovery` flips `discovering = False` and renders `(RowStatus.SYNCING, str(progress))` (`"100%"` if `total == 0`).

## Risks / Trade-offs

- [SharePoint discovery and SharePoint downloads contend for `sharepoint_limiter`] -> Both go through the same FIFO `asyncio.Lock`, preserving strict rate limits while Moodle downloads and inline HTML writes finish immediately on independent paths.
- [Nested `asyncio.TaskGroup` wraps `CredentialsExpiredError` in multi-level `ExceptionGroup`] -> Helper recursively extracts the first leaf `CredentialsExpiredError` inside `except* CredentialsExpiredError` so callers always catch `CredentialsExpiredError` directly.
- [Streaming `CourseFileDiscovered` events could flood `INFO` logs on large courses] -> Log `CourseFileDiscovered` at `DEBUG` and retain the single `INFO` summary log on `CourseDiscoveryCompleted`.

## Modules Touched & Confirmed Public Test Seams

- **Production modules (11)**: `src/idat_sync/domain/sync.py`, `src/idat_sync/domain/__init__.py`, `src/idat_sync/domain/path_utils.py`, `src/idat_sync/application/course_repository.py`, `src/idat_sync/application/download_manager.py`, `src/idat_sync/application/task_orchestrator.py`, `src/idat_sync/application/events.py`, `src/idat_sync/application/sync_service.py`, `src/idat_sync/infrastructure/sharepoint_client.py`, `src/idat_sync/ui/course_tree.py`, `src/idat_sync/ui/sync_frame.py`.
- **Docs & standards**: `GLOSSARY.md` (updated), `docs/adr/0006-streaming-sync-pipeline.md` (created), `agent-os/standards/global/layer-boundaries.md`, `agent-os/standards/backend/content-strategy-dispatch.md`, `agent-os/standards/testing/composition-and-seams.md`.
- **Confirmed public test seams (12)**: `domain.sync` (`is_content_pending`), `domain.path_utils` (`PathDisambiguator`), `CourseRepository` (`list_url_content`, `stream_course_content`, `get_recordings`), `DownloadManager` (`is_pending`, `create_download_task`), `TaskOrchestrator` (`create_channel` -> `TaskChannel`), `application.events` (`SyncEvent`, `log_sync_event`), `IDATSync` (`sync_course`, `sync_courses`), `SharepointClient` (`get_client_from_url`), `CourseTree` (`start_course`, `add_file`, `finish_discovery`, `set_file_status`, `set_course_status`), `SyncFrame` (`do_sync`), `TestRealCourseContentsFixture`, and AST layer-boundary checks (`tests/test_architecture.py`).
