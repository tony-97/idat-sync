# Proposal

## Why

Network I/O across Moodle and SharePoint currently relies on synchronous `requests` and `office365-rest-python-client` `v2.6.2` calls wrapped in `asyncio.to_thread(...)` and `time.sleep(...)`, while `TaskOrchestrator` and `IDATSync` rely on callback-based `EventPublisher` injection and thread-safe UI polling queues. Upgrading to `office365-rest-python-client[httpx] >= 3.3.1` and `httpx >= 0.27` lets the entire transport, adapter, orchestration, and UI pipeline run natively on `asyncio` with built-in SharePoint incremental retry and an `AsyncIterator[SyncEvent]` event stream.

## What Changes

- **BREAKING**: Upgrade `pyproject.toml` dependencies to `"office365-rest-python-client[httpx] >= 3.3.1"` and `"httpx >= 0.27"`, and remove `"requests >= 2.32.5"`.
- **BREAKING**: Convert `MoodleTokenClient.request_token` and all `MoodleClient` HTTP/download methods to `async def` using `httpx.AsyncClient` and `await asyncio.sleep(self.request_delay)`.
- **BREAKING**: Make `SharepointClient.__init__` non-blocking (`self.search_client: ClientContext | None = None`), add `@classmethod async def create(...) -> Self`, configure `.with_cookies(...).with_auto_retry()` and `HttpxTransport()` in `get_client(site_url)`, remove `_run_query` and `_retry_delay`, and convert all SharePoint query/download methods to `async def` with `await asyncio.sleep(self.request_delay)`.
- **BREAKING**: Convert `MoodleTokenPort`, `CourseGateway`, `RecordingGateway`, `UrlContentStrategy`, `Downloader.download`, `DownloadTask.download`, `MoodleAdapter`, `MoodleTokenAdapter`, `SharepointApiAdapter` (using `v3.3.1` `server_relative_url`), `AuthService.authenticate`, `CourseRepository`, and `*Downloader.download` (`RecordingDownloader` offloading `YoutubeDL` via `asyncio.to_thread`) to `async def`.
- **BREAKING**: Remove `EventPublisher` and `noop_publisher` from `application/events.py`.
- **BREAKING**: Simplify `TaskOrchestrator(task_group)` to a pure coroutine generator exposing only `create_coroutines(tasks, run_task)` (removing `publish`, `_abort_event`, `run`, `run_async`, and `_run_group`).
- **BREAKING**: Convert `bootstrap.create_sync_service(credentials, config) -> IDATSync` to `async def` (awaiting `SharepointClient.create`), convert `IDATSync.list_courses`, `get_course_remote_tree`, and `check_course_sync` to `async def`, and convert `IDATSync.sync_course` and `IDATSync.sync_courses` to `AsyncIterator[SyncEvent]` async generators that call `log_sync_event(event)` before yielding each `SyncEvent`.
- **BREAKING**: Remove `progress_queue`, `update_progress`, `exit`, `_queue_error`, `call_soon_threadsafe`, and `asyncio.to_thread` from `SyncFrame`, awaiting `create_sync_service`, `list_courses`, and `async for event in self.idat_sync.sync_courses(path, selected):` directly on `MainApp.loop`; convert `scripts/moodle_json.py` to `async def main()`.

## Non-Goals

- Refactoring `MoodleAdapter._assignments_cache` or introducing a separate presentation-layer state store for courses and assignments (deferred to a follow-up change).
- Replacing `yt-dlp` (`YoutubeDL`) with a custom video segment downloader.
- Changing local filesystem scanning (`LocalFileSystemAdapter.scan_mtimes` and `ensure_dir` remain synchronous).

## Applicable Standards (`agent-os/standards/index.yml`)

- `global/layer-boundaries`: Four-layer package boundaries (`domain`, `application`, `infrastructure`, `ui`), hexagonal dependency inversion, and `FileSystemPort` rules.
- `backend/client-adapter-acl`: Two-tier `*Client` transport and `*Adapter`/`*Downloader` anti-corruption layer delegating HTTP/SDK calls and rate-limiting to clients.
- `backend/content-strategy-dispatch`: URL strategy routing (`can_handle`), bottom-up `Content.in_folder` path composition, and generic `Downloader[C]` rate-limit groups.
- `testing/composition-and-seams`: `bootstrap.py` composition root, constructor injection rules, and confirmed I/O and architectural test seams.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `authentication`: `MoodleTokenClient.request_token`, `MoodleTokenAdapter.fetch_token`, `MoodleTokenPort.fetch_token`, and `AuthService.authenticate` become `async def` over `httpx.AsyncClient`; `AuthService.login` awaits `moodle_auth.fetch_token` directly in `asyncio.TaskGroup()`; `SyncFrame` awaits `create_sync_service` without `publish` or `asyncio.to_thread`.
- `course-synchronization`: Async `httpx` and `HttpxTransport` clients, `SharepointClient.create` and `.with_auto_retry()` (removing `_run_query` and `_retry_delay`), async ports/adapters/repositories/downloaders, pure coroutine `TaskOrchestrator.create_coroutines`, `AsyncIterator[SyncEvent]` streaming in `IDATSync`, and direct async consumption in `SyncFrame` and `scripts/moodle_json.py`.

## Impact

- **Dependencies**: `pyproject.toml` (`office365-rest-python-client[httpx] >= 3.3.1`, `httpx >= 0.27`; drop `requests`).
- **Code**: `src/idat_sync/application/`, `src/idat_sync/infrastructure/`, `src/idat_sync/bootstrap.py`, `src/idat_sync/ui/sync_frame.py`, `scripts/moodle_json.py`, `agent-os/standards/`, and `tests/`.
