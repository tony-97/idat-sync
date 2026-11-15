# Design

## Context

See [proposal.md](./proposal.md) for motivation and [ADR-0001: Hexagonal Ports and Adapters Architecture](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) plus [ADR-0002: Dual Path Sanitization](../../../docs/adr/0002-dual-path-sanitization.md) for long-lived architectural boundaries.

Currently:
- `MoodleClient` and `MoodleTokenClient` perform blocking `requests` calls and `time.sleep(self.request_delay)`.
- `SharepointClient` wraps `office365-rest-python-client` `v2.6.2` synchronous calls in a custom `_run_query` retry loop (`_retry_delay` + `time.sleep`) and performs network I/O (`get_personal_site().execute_query()`) inside `__init__`.
- `TaskOrchestrator` owns `EventPublisher` and a `threading.Event` (`_abort_event`), offloading synchronous downloader groups via `asyncio.to_thread`.
- `IDATSync` accepts `publish: EventPublisher` and emits events via callback while `SyncFrame` bridges background threads back onto `MainApp.loop` via `loop.call_soon_threadsafe(self.progress_queue.put_nowait, event)` and a polling `update_progress` coroutine.

## Goals / Non-Goals

**Goals:**
- Run Moodle and SharePoint HTTP/SDK operations natively on `asyncio` via `httpx.AsyncClient` and `office365-rest-python-client[httpx] >= 3.3.1` (`HttpxTransport`).
- Delegate SharePoint transient retry (`408, 429, 500, 502, 503, 504`, `Retry-After` header, exponential backoff with `asyncio.sleep`) to `office365-rest-python-client` `v3.3.1` via `.with_auto_retry()` and `execute_query_with_incremental_retry_async()`, removing `_run_query` and `_retry_delay`.
- Make `TaskOrchestrator` a pure coroutine generator (`create_coroutines(tasks, run_task)`) with zero dependency on `events.py` or `threading`.
- Stream `SyncEvent`s directly from `IDATSync.sync_course` and `IDATSync.sync_courses` as `AsyncIterator[SyncEvent]` async generators that log each event via `log_sync_event(event)` before yielding.

**Non-Goals:**
- Extracting `_assignments_cache` or introducing a presentation-layer course/assignment state store.
- Making `LocalFileSystemAdapter` (`scan_mtimes`, `ensure_dir`) asynchronous.

## Decisions

### 1. Native `httpx` transport for Moodle and SharePoint (`v3.3.1`)
- **Choice**:
  - `MoodleTokenClient` and `MoodleClient` use `async with httpx.AsyncClient(...) as client:` (`client.post`, `client.head`, and `async with client.stream("GET", ...) as response:` with `await response.aiter_bytes(chunk_size=1024 * 256)`), raising `CredentialsExpiredError` on HTTP `401`/`403` or `invalidtoken` and `httpx.HTTPStatusError` via `response.raise_for_status()`, and awaiting `asyncio.sleep(self.request_delay)`.
  - `SharepointClient.get_client(site_url)` chains `ClientContext(site_url).with_cookies(lambda: self.sharepoint_cookies).with_auto_retry()` and `ctx.pending_request().with_async_transport(HttpxTransport())`.
  - `SharepointApiAdapter` reads `file.server_relative_url` and `entity.server_relative_url` (updated from `v2.6.2`'s camelCase `serverRelativeUrl` in `v3.3.1`).
- **Alternatives considered**: Keeping `requests` and offloading calls with `asyncio.to_thread` (rejected: retains `requests` dependency and thread hops when `v3.3.1` natively supports `HttpxTransport`).

### 2. Non-blocking `SharepointClient.__init__` + `@classmethod async def create` + built-in SDK retry
- **Choice**:
  - `SharepointClient.__init__` sets `self.search_client: ClientContext | None = None` without network I/O.
  - `@classmethod async def create(...) -> Self` instantiates `cls(...)`, awaits `client.personal_client.web.current_user.get_personal_site().execute_query_async()` (translating HTTP `401`/`403` `ClientRequestException` or empty `personal_site.url` into `CredentialsExpiredError("SharePoint login expired")`), sets `client.search_client = client.get_client(personal_site.url)`, and awaits `asyncio.sleep(client.request_delay)`.
  - `_run_query` and `_retry_delay` are deleted. Because `get_client` configures `.with_auto_retry()`, every `execute_query_async()`, `execute_query_with_incremental_retry_async()`, `get_all_async(page_size=50)`, and `download_session_async(dest, use_path=False)` automatically retries transient `429`/`503` responses honoring `Retry-After` and awaits `asyncio.sleep(self.request_delay)` afterward.
- **Alternatives considered**: Keeping a custom `_run_query` retry wrapper (rejected: `v3.3.1` fixes transient status filtering, query re-queuing, and `Retry-After` handling natively).

### 3. Pure coroutine generator `TaskOrchestrator.create_coroutines`
- **Choice**: `TaskOrchestrator(task_group: list[list[Downloader]])` exposes only `create_coroutines(self, tasks: Sequence[DownloadTask], run_task: Callable[[DownloadTask], Awaitable[None]] = lambda task: task.download()) -> list[Coroutine[Any, Any, None]]`. Each returned coroutine iterates sequentially over the tasks matched to its downloader group and awaits `run_task(task)`.
- **Alternatives considered**: Passing an `asyncio.Queue` or `EventPublisher` into `TaskOrchestrator` (rejected: couples task grouping to event reporting; `run_task` injection lets `IDATSync` wrap task execution with event queueing and cancellation while keeping `TaskOrchestrator` pure).

### 4. `AsyncIterator[SyncEvent]` stream in `IDATSync` and direct `async for` in `SyncFrame`
- **Choice**:
  - Per [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md), `IDATSync.sync_course` and `IDATSync.sync_courses` are `AsyncIterator[SyncEvent]` async generators. Before yielding any `SyncEvent`, `IDATSync` calls `log_sync_event(event)`.
  - Inside `sync_course`, an internal `asyncio.Queue[SyncEvent | None]` and `run_task` closure wrap each `DownloadTask` (`TaskDownloadStarted` -> `await task.download()` -> `TaskDownloadCompleted` / `TaskDownloadFailed`), running `task_orchestrator.create_coroutines(tasks, run_task)` as concurrent `asyncio.Task`s. On `CredentialsExpiredError`, remaining group tasks are cancelled immediately (`t.cancel()`) and `CredentialsExpiredError` is re-raised after yielding `TaskDownloadFailed`.
  - `SyncFrame` drops `progress_queue`, `update_progress`, `exit`, `_queue_error`, `call_soon_threadsafe`, and `asyncio.to_thread`, awaiting `create_sync_service`, `list_courses`, and `async for event in self.idat_sync.sync_courses(path, selected):` directly on `MainApp.loop`.
- **Alternatives considered**: Keeping `EventPublisher` callbacks (rejected: requires thread-safe queue plumbing in `SyncFrame` and extra parameters across `bootstrap` and `IDATSync`).

## Modules Touched & Public Test Seams

- **Modules touched**:
  - `pyproject.toml`
  - `GLOSSARY.md`, `docs/adr/0001-hexagonal-ports-and-adapters.md`, `agent-os/standards/global/layer-boundaries.md`, `agent-os/standards/backend/client-adapter-acl.md`
  - `src/idat_sync/application/events.py`, `src/idat_sync/application/ports.py`, `src/idat_sync/application/auth_service.py`, `src/idat_sync/application/course_repository.py`, `src/idat_sync/application/task_orchestrator.py`, `src/idat_sync/application/sync_service.py`
  - `src/idat_sync/infrastructure/moodle_client.py`, `src/idat_sync/infrastructure/moodle_adapter.py`, `src/idat_sync/infrastructure/sharepoint_client.py`, `src/idat_sync/infrastructure/sharepoint_adapter.py`, `src/idat_sync/infrastructure/downloaders.py`
  - `src/idat_sync/bootstrap.py`, `src/idat_sync/ui/sync_frame.py`, `scripts/moodle_json.py`
- **Public test seams**:
  - `MoodleTokenClient`, `MoodleClient`, `MoodleTokenAdapter`, `MoodleAdapter` (`tests/test_moodle_adapter.py`, `tests/test_auth_service.py`)
  - `SharepointClient`, `SharepointApiAdapter` (`tests/test_sharepoint_adapter.py`)
  - `AuthService` (`tests/test_auth_service.py`)
  - `CourseRepository` (`tests/test_course_repository.py`)
  - `MoodleDownloader`, `SharepointDownloader`, `StringDownloader`, `RecordingDownloader`, `TaskOrchestrator.create_coroutines` (`tests/test_downloaders.py`)
  - `idat_sync.application.events`, `IDATSync`, `bootstrap.create_sync_service` (`tests/test_sync_service.py`)
  - `SyncFrame` (`tests/test_ui_app.py`)
  - `scripts/moodle_json.py` (`tests/test_cli_scripts.py`)
  - Real-data fixtures and live smoke tests (`tests/test_real_data.py`)
  - AST architecture boundary seam (`tests/test_architecture.py`)

## Risks / Trade-offs

- **[Risk] Blocking `YoutubeDL.download` on the asyncio event loop** → Mitigation: `RecordingDownloader.download` offloads `_run_ydl()` via `await asyncio.to_thread(_run_ydl)`.
- **[Risk] Uncancelled background group tasks when `CredentialsExpiredError` aborts `sync_course` early** → Mitigation: `sync_course` cancels all in-flight group `asyncio.Task`s and awaits `asyncio.gather(*group_tasks, return_exceptions=True)` in `finally:` before propagating `CredentialsExpiredError`.
