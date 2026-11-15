# Tasks

## 1. Dependencies, Architecture Boundaries & Standards

- [x] 1.1 Upgrade `pyproject.toml` to `"office365-rest-python-client[httpx] >= 3.3.1"` and `"httpx >= 0.27"` (removing `"requests >= 2.32.5"`), update `agent-os/standards/global/layer-boundaries.md` and `agent-os/standards/backend/client-adapter-acl.md`, and add AST boundary tests in `tests/test_architecture.py` enforcing zero `requests` imports across `src/idat_sync/`, zero `httpx` imports in `domain/` and `application/`, and zero `events.py` or `threading` imports in `task_orchestrator.py`; verify package sync (`uv sync`).

## 2. Async Moodle Transport, Adapters & Authentication Seams

- [x] 2.1 Convert `MoodleTokenPort.fetch_token`, `MoodleTokenClient.request_token` (using `httpx.AsyncClient`), `MoodleTokenAdapter.fetch_token`, and `AuthService.authenticate` / `AuthService.login` (awaiting `moodle_auth.fetch_token` directly in `asyncio.TaskGroup()`) to `async def`, and verify `uv run python -m unittest tests/test_auth_service.py -v` passes.
- [x] 2.2 Convert `CourseGateway`, `UrlContentStrategy`, `MoodleClient` (`call_ws`, `fetch_enrolled_courses`, `fetch_assignments`, `fetch_course_contents`, `fetch_url_head`, `download_file` via `httpx.AsyncClient` and `await asyncio.sleep(self.request_delay)`), and `MoodleAdapter` (`list_courses`, `populate_course_sections`, `_get_assignments_by_course`, `_resolve_url_to_content`, `list_url_content`) to `async def`, and verify `uv run python -m unittest tests/test_moodle_adapter.py -v` passes.

## 3. Async SharePoint `v3.3.1` Transport, Adapter & CourseRepository Seams

- [x] 3.1 Convert `RecordingGateway`, `SharepointClient` (non-blocking `__init__`, `@classmethod async def create`, `get_client` chaining `.with_cookies(...).with_auto_retry()` and `HttpxTransport()`, removal of `_run_query` and `_retry_delay`, and `async def` query/download methods with `await asyncio.sleep(self.request_delay)`), and `SharepointApiAdapter` (`async def` methods reading `server_relative_url`), and verify `uv run python -m unittest tests/test_sharepoint_adapter.py -v` passes.
- [x] 3.2 Convert `CourseRepository` (`list_courses`, `populate_course_sections`, `get_course_content`, `get_recordings`) to `async def` coroutines awaiting the async gateways and URL strategies, and verify `uv run python -m unittest tests/test_course_repository.py -v` passes.

## 4. Async Downloaders, Pure `TaskOrchestrator.create_coroutines` & `IDATSync` Event Stream Seams

- [x] 4.1 Convert `Downloader.download`, `DownloadTask.download`, `MoodleDownloader.download`, `SharepointDownloader.download`, `StringDownloader.download`, and `RecordingDownloader.download` (offloading `YoutubeDL` via `await asyncio.to_thread`) to `async def`, simplify `TaskOrchestrator(task_group)` to expose only `create_coroutines(tasks, run_task)` (removing `publish`, `_abort_event`, `run`, `run_async`, and `_run_group`), and verify `uv run python -m unittest tests/test_downloaders.py -v` passes.
- [x] 4.2 Remove `EventPublisher` and `noop_publisher` from `src/idat_sync/application/events.py`, convert `bootstrap.create_sync_service(credentials, config) -> IDATSync` to `async def` (awaiting `SharepointClient.create`), convert `IDATSync.list_courses`, `get_course_remote_tree`, and `check_course_sync` to `async def`, and convert `IDATSync.sync_course` and `IDATSync.sync_courses` to `AsyncIterator[SyncEvent]` async generators that call `log_sync_event(event)` before yielding each `SyncEvent` and cancel sibling group tasks immediately on `CredentialsExpiredError`, and verify `uv run python -m unittest tests/test_sync_service.py -v` passes.

## 5. UI `SyncFrame`, CLI Scripts, Real-Data Tests & Final Verification

- [x] 5.1 Remove `progress_queue`, `update_progress`, `exit`, `_queue_error`, `loop.call_soon_threadsafe`, and `asyncio.to_thread` from `SyncFrame` (`src/idat_sync/ui/sync_frame.py`), awaiting `create_sync_service`, `list_courses`, and `async for event in self.idat_sync.sync_courses(path, selected):` directly on `MainApp.loop`, and verify `uv run python -m unittest tests/test_ui_app.py -v` passes.
- [x] 5.2 Convert `scripts/moodle_json.py` (`async def main()` run via `asyncio.run(main())`) and `tests/test_real_data.py` to await the async client, adapter, repository, and downloader methods, and verify `uv run python -m unittest tests/test_cli_scripts.py tests/test_real_data.py -v` passes.
- [x] 5.3 Run the full test suite (`uv run python -m unittest discover -s tests -v`), typecheck, and lint (`tests/test_architecture.py`), and verify all checks pass.
