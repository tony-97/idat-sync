# course-synchronization Specification

## MODIFIED Requirements

### Requirement: CourseRepository orchestrates gateways and URL strategies
`CourseRepository` in `src/idat_sync/application/course_repository.py` SHALL accept `(course_gateway: CourseGateway, recording_gateway: RecordingGateway, url_strategies: Sequence[UrlContentStrategy])` and expose `async def list_courses(self) -> list[Course]`, `async def populate_course_sections(self, course: Course) -> Course`, `def invalidate_cache(self) -> None` (delegating to `self.course_gateway.invalidate_cache()`), `async def get_course_content(self, course: Course, course_dir: Path | str = Path()) -> list[Content]`, and `async def get_recordings(self, course_name: str, recordings_folder: str) -> list[Content]`. `get_course_content(course, course_dir)` SHALL skip sections and modules with empty `name`, place each module's `inline_contents` before resolved `contents` in `safe_path_segment(module.name)`, prepend `section.inline_contents` before module items in `safe_path_segment(section.name)`, and wrap items in `course_dir`. Verified at the `CourseRepository` (`src/idat_sync/application/course_repository.py`) public seam.

#### Scenario: Composing course content hierarchy and ordering
- **WHEN** `await CourseRepository.get_course_content(course, course_dir)` is called on a `Course` with sections and modules (including unsafe characters such as `:` or `/` in `section.name` or `module.name`)
- **THEN** it awaits `strategy.list_url_content(...)` for each matching URL strategy and emits each section's `inline_contents` followed by each named module's `inline_contents` and URL-resolved `contents`, composed bottom-up via `.in_folder(safe_path_segment(module.name))`, `.in_folder(safe_path_segment(section.name))`, and `.in_folder(course_dir)`

#### Scenario: Wrapping recordings in target folder
- **WHEN** `await CourseRepository.get_recordings(course_name, recordings_folder)` is called
- **THEN** it awaits `recording_gateway.fetch_recordings(course_name)` and returns `[r.in_folder(recordings_folder) for r in recordings]`

#### Scenario: Delegating cache invalidation from CourseRepository to CourseGateway
- **WHEN** `CourseRepository.invalidate_cache()` is called
- **THEN** it invokes `self.course_gateway.invalidate_cache()` once

### Requirement: Composition root wires production dependencies outside application
`async def create_sync_service(credentials: Credentials, config: SyncConfig) -> IDATSync` in `src/idat_sync/bootstrap.py` SHALL require an explicit `SyncConfig` without a `publish` parameter, construct `MoodleClient`, `MoodleAdapter`, await `await SharepointClient.create(...)` (awaiting `moodle_client.aclose()` before re-raising if `SharepointClient.create(...)` fails), construct `SharepointApiAdapter`, `LocalFileSystemAdapter`, and `Downloader` groups with `config` values, wire `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)` directly to the low-level clients without `request_delay`, wire `TaskOrchestrator(downloader_group)`, and inject the composed collaborators, `recordings_folder_name`, and `closers=[moodle_client.aclose, sharepoint_raw_client.aclose]` into `IDATSync`. `IDATSync.__init__` SHALL accept optional `closers: Sequence[Callable[[], Awaitable[None]]] = ()` without instantiating clients or adapters from `Credentials` and MUST NOT accept `publish`; `IDATSync` SHALL expose `async def aclose(self) -> None` (invoking every closer once, ensuring remaining closers still execute if an earlier closer fails, and re-raising the first exception if any) and `__aenter__` / `__aexit__` delegating to `aclose()`. `configure_logging(log_file: Path) -> logging.Logger` in `src/idat_sync/bootstrap.py` SHALL configure `logging.getLogger("idat_sync")` at `logging.INFO` with `propagate = False`, closing and clearing existing handlers before attaching `logging.StreamHandler()` and `logging.FileHandler(log_file, mode="w", encoding="utf-8")`. Verified at `bootstrap.create_sync_service`, `bootstrap.configure_logging`, and `IDATSync` (`src/idat_sync/application/sync_service.py`) seams.

#### Scenario: Constructing IDATSync with injected collaborators
- **WHEN** `IDATSync(course_repository, download_manager, task_orchestrator, file_system, recordings_folder_name="Videos")` is instantiated and `async for _ in sync.sync_course(course, course_dir):` runs
- **THEN** it stores the injected collaborators without network or credential initialization in `__init__`, rejects a `publish` keyword argument with `TypeError`, and ensures and syncs recordings under `Path(course_dir) / "Videos"`

#### Scenario: Closing injected closers deterministically via IDATSync.aclose
- **WHEN** `IDATSync(..., closers=[closer_a, closer_b])` exits `async with` or awaits `await sync.aclose()`
- **THEN** each closer is awaited once (`closer_b` still runs if `closer_a` raises, re-raising the first failure) and repeated `aclose()` calls are safe no-ops

#### Scenario: Wiring SyncConfig through create_sync_service
- **WHEN** `configure_logging(log_file)` and `await create_sync_service(credentials, config=custom_config)` are called
- **THEN** `configure_logging` configures `logging.getLogger("idat_sync")` at `logging.INFO` (`propagate == False`) with one `StreamHandler` and one `FileHandler` targeting `log_file`, `await create_sync_service` forwards `custom_config` fields to `MoodleClient`, `MoodleAdapter`, `await SharepointClient.create(...)`, `SharepointApiAdapter` (without `request_delay`), `RecordingDownloader`, `TaskOrchestrator`, and `IDATSync`, wires `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)` with `moodle_dl.moodle_client is moodle_adapter.client` and `sp_dl.sharepoint_client is sp_adapter.sp_client`, and `await idat_sync.aclose()` closes both `moodle_client` and `sharepoint_raw_client`

#### Scenario: Cleaning up MoodleClient when SharepointClient.create fails in create_sync_service
- **WHEN** `await SharepointClient.create(...)` raises during `await create_sync_service(credentials, config=custom_config)`
- **THEN** `moodle_client.aclose()` is awaited before the exception propagates

### Requirement: Client-owned file download streaming and SharePoint SDK query execution
`MoodleClient` SHALL reuse a single shared `httpx.AsyncClient` across `call_ws` and `download_file` and expose `async def aclose(self) -> None`, `__aenter__`, and `__aexit__` closing the shared client (`MoodleTokenClient` retains single-call `async with httpx.AsyncClient()`). `MoodleClient` and `SharepointClient` SHALL provide `async def download_file(..., dest: BinaryIO) -> None` and `await asyncio.sleep(self.request_delay)`. `MoodleDownloader.download`, `SharepointDownloader.download`, and `StringDownloader.download` SHALL be `async def` coroutines, all three writing into a sibling `<filename>.part` temporary file, atomically replacing `content.path` via `os.replace` on completion, and unlinking the `.part` file (`missing_ok=True`) if writing or `download_file` raises (`StringDownloader` writing UTF-8 encoded content). `SharepointClient.__init__` SHALL be non-blocking (`self.search_client: ClientContext | None = None`), MUST NOT set `self.onedrive_cookies` or flatten all hosts into a shared `self.sharepoint_cookies` dictionary, SHALL provide `cookies_for_host(self, host: str) -> dict[str, str]` filtering `FedAuth`, `rtFa`, and `SPOIDCRL` cookies strictly per target host by exact lowercase domain equality or proper leading-dot domain suffix (with exact-host precedence over leading-dot wildcard matches and never substring matching), and SHALL instantiate a single `self._transport = HttpxTransport()` reused across all `get_client(site_url)` calls. `SharepointClient` SHALL expose `async def aclose(self) -> None` awaiting `self._transport.aclose()` and `__aenter__` / `__aexit__` delegating to `aclose()`, and `@classmethod async def create(...) -> Self` SHALL resolve `personal_site` via `await client.personal_client.web.current_user.get_personal_site().execute_query_async()` and `await asyncio.sleep(client.request_delay)`, calling `await client.aclose()` before raising if initialization fails. `get_client(site_url)` SHALL parse the target host from `site_url` and chain `.with_cookies(lambda host=target_host: self.cookies_for_host(host)).with_auto_retry()` and `ctx.pending_request().with_async_transport(self._transport)`. `SharepointClient` MUST NOT define `_run_query` or `_retry_delay`. `SharepointClient` SHALL execute async SDK queries directly (`await ctx.execute_query_with_incremental_retry_async()` in `load_entity`, `await ...execute_query_async()` in `search_for_recordings`, `fetch_folder_files`, and `fetch_subfolders`, `await ...get_all_async(page_size=50)` in `fetch_document_library_items`, and `await file.download_session_async(dest, use_path=False)` in `download_file`), and `SharepointApiAdapter.list_url_content` SHALL be `async def`, dispatch by URL path, and skip empty entities where `not entity.name and not entity.server_relative_url`. Verified at `MoodleClient`, `SharepointClient`, `SharepointApiAdapter`, and `*Downloader` seams.

#### Scenario: Streaming a Moodle file through MoodleClient
- **WHEN** `await MoodleClient.download_file(url, dest)` is called with a target URL and writable binary stream
- **THEN** it opens `async with self._client.stream("GET", url, params={"wstoken": self.token, "token": self.token}, follow_redirects=True, timeout=60, headers=REQUEST_HEADER) as response:` on the shared `httpx.AsyncClient`, raises `CredentialsExpiredError("403 Forbidden: your session cookie is missing/expired.")` on HTTP `401` or `403`, calls `response.raise_for_status()`, writes non-empty chunks from `await response.aiter_bytes(chunk_size=1024 * 256)` into `dest`, and awaits `asyncio.sleep(self.request_delay)`

#### Scenario: Reusing and closing shared httpx.AsyncClient in MoodleClient
- **WHEN** a `MoodleClient` instance executes multiple `call_ws` and `download_file` operations and then exits `async with client:` or awaits `await client.aclose()`
- **THEN** `httpx.AsyncClient` is instantiated once per `MoodleClient`, reused across all requests, and `aclose()` awaits `self._client.aclose()` while `MoodleTokenClient.request_token` retains its per-call `async with httpx.AsyncClient()`

#### Scenario: Delegating MoodleDownloader and SharepointDownloader file downloads to clients
- **WHEN** `await MoodleDownloader(moodle_client).download(moodle_content)` and `await SharepointDownloader(sharepoint_client).download(sp_content)` succeed vs when `download_file` raises mid-download
- **THEN** each stores `self.moodle_client` or `self.sharepoint_client` without accepting `request_delay`, creates `Path(content.path).parent`, streams into `Path(content.path).with_name(f"{Path(content.path).name}.part")`, atomically moves the `.part` file to `content.path` via `os.replace` on success, and removes the `.part` file without leaving a partial `content.path` on failure (where `SharepointClient.download_file` awaits `file.download_session_async(dest, use_path=False)` and `asyncio.sleep(self.request_delay)`)

#### Scenario: Writing StringContent atomically via .part files in StringDownloader
- **WHEN** `await StringDownloader().download(string_content)` succeeds vs when writing fails mid-operation
- **THEN** it creates `Path(content.path).parent`, writes UTF-8 encoded content into `Path(content.path).with_name(f"{Path(content.path).name}.part")` via `_atomic_download`, atomically moves the `.part` file onto `content.path` via `os.replace` on success, and unlinks the `.part` file (`missing_ok=True`) without corrupting an existing destination file on failure

#### Scenario: Isolating SharePoint cookies per target host with exact or leading-dot matching
- **WHEN** `SharepointClient` is created with cookies for `"intranet"`, `".sharepoint.com"`, `"evil.sharepoint.com.attacker.test"`, `"contoso.sharepoint.com"`, and `"contoso-my.sharepoint.com"` and `cookies_for_host(...)` or `get_client(site_url)` resolves cookies for each host
- **THEN** `hasattr(client, "sharepoint_cookies")` is `False`, `cookies_for_host("intranet")` rejects `".sharepoint.com"` and `"evil.sharepoint.com.attacker.test"` while returning `intranet` exact matches, `"contoso.sharepoint.com"` never receives `"contoso-my.sharepoint.com"` cookies and rejects `"evil.sharepoint.com.attacker.test"`, exact host cookies take precedence over `".sharepoint.com"` suffix cookies regardless of input order, and `get_client(site_url)` supplies only cookies matching the parsed host of `site_url`

#### Scenario: Reusing and closing HttpxTransport in SharepointClient without onedrive_cookies
- **WHEN** `SharepointClient` is instantiated, `get_client` is called multiple times, and `async with client:` / `await client.aclose()` (or a failing `await SharepointClient.create(...)`) runs
- **THEN** `hasattr(client, "onedrive_cookies")` is `False`, `HttpxTransport` is instantiated once per `SharepointClient` and passed to every `with_async_transport` call, and `await client.aclose()` / `__aexit__` (as well as a failing `SharepointClient.create`) awaits `self._transport.aclose()`

#### Scenario: Delegating SharePoint guest URLs, direct links, and folder traversal to SharepointClient
- **WHEN** `await SharepointApiAdapter.list_url_content`, `await fetch_folder_contents`, and `await fetch_file_from_direct_link` resolve guest folder URLs (`^/:f:/`), guest file URLs (`^/:(\w+):/`) across `sharepoint_host` and `sharepoint_personal_host`, direct links with `uniqueid`/`sourcedoc`, and folder hierarchies
- **THEN** `SharepointApiAdapter` inspects `urlparse(url).path` without repeating the hostname check, awaits `sp_client.fetch_folder_by_guest_url`, `sp_client.fetch_file_by_guest_url`, and `sp_client.fetch_file_by_id(link, file_id)` (skipping the client call and returning `None` when neither `uniqueid` nor `sourcedoc` is present), and awaits `sp_client.fetch_folder_files(folder)` before `sp_client.fetch_subfolders(folder)`, while `SharepointClient.get_client` configures host-scoped `.with_cookies(...).with_auto_retry()` and `HttpxTransport()`, `SharepointClient.get_client_from_url` matches `r"/(sites|teams|s|personal)/([^/]+)"` case-insensitively (mapping `/s/<name>` to `{base_url}/sites/<name}` and `/(sites|teams|personal)/<name>` to `{base_url}{path_match.group(0)}`), and `hasattr(SharepointClient, "_run_query")` and `hasattr(SharepointClient, "_retry_delay")` are both `False`

#### Scenario: Skipping dead or expired SharePoint guest and direct links returning odata.null
- **WHEN** `await SharepointClient.load_entity(entity, ctx)` encounters `TypeError` because an `odata.null` guest URL response left `entity.resource_url` as `None`, or `await SharepointApiAdapter.list_url_content` receives a `Folder` or `File` where `not entity.name and not entity.server_relative_url`
- **THEN** `SharepointClient.load_entity` catches `TypeError` and still awaits `asyncio.sleep(self.request_delay)` (while propagating non-`TypeError` exceptions unchanged), and `SharepointApiAdapter.list_url_content` returns `[]`

#### Scenario: Retrying SharePoint HTTP 429 and 503 with Retry-After backoff and re-raising on exhaustion
- **WHEN** `SharepointClient.get_client(site_url)` creates a `ClientContext` and `load_entity` executes a query
- **THEN** `get_client` chains host-scoped `.with_cookies(...).with_auto_retry()` and `ctx.pending_request().with_async_transport(self._transport)`, `load_entity` awaits `ctx.execute_query_with_incremental_retry_async()`, and `SharepointClient` does not define `_run_query` or `_retry_delay` (`office365-rest-python-client` `v3.3.1` handles transient `429`/`503` retries and `Retry-After` headers natively)

### Requirement: Per-course failure isolation and early credential abort in IDATSync
`IDATSync` in `src/idat_sync/application/sync_service.py` MUST NOT accept `publish` in `__init__` or `progress_listener` in `sync_course` or `sync_courses`. `list_courses() -> list[Course]`, `get_course_remote_tree(course, course_dir) -> list[Content]`, and `check_course_sync(course, course_dir) -> SyncDiff` SHALL be `async def` coroutines. `sync_course(course: Course, course_dir: str | Path) -> AsyncIterator[SyncEvent]` and `sync_courses(root_folder: str | Path, courses: Sequence[Course]) -> AsyncIterator[SyncEvent]` SHALL be async generators that call `log_sync_event(event)` before yielding each `SyncEvent`. `sync_course` SHALL drive `task_orchestrator.create_coroutines(tasks, run_task)` concurrently across downloader groups, continuing remaining tasks after non-credential task errors and cancelling sibling group tasks immediately on `CredentialsExpiredError`. `sync_courses` SHALL call `self.course_repository.invalidate_cache()` at the start of each sync pass before iterating `courses`, compute each `Course` folder via `Path(root_folder) / safe_filename(course.name)`, lazily await `populate_course_sections(course)` when `course.sections` is empty, emit `CourseSyncCompleted(course, exc)` and skip single-`Course` failures other than `CredentialsExpiredError` so later courses still sync, and abort remaining courses immediately on `CredentialsExpiredError`. Verified at the `IDATSync` (`src/idat_sync/application/sync_service.py`) seam.

#### Scenario: Invalidating repository cache at the start of sync_courses
- **WHEN** `[e async for e in IDATSync.sync_courses(root_folder, [course1, course2])]` starts executing
- **THEN** `course_repository.invalidate_cache()` is called once before processing `course1` and `course2`

#### Scenario: Continuing remaining courses when one course fails
- **WHEN** `sync_course` raises a non-credential error on the first `Course` while a second `Course` in `courses` succeeds
- **THEN** `[e async for e in IDATSync.sync_courses(root_folder, [course1, course2])]` yields `CourseSyncCompleted(course1, exc)`, still synchronizes the second `Course` and yields `CourseSyncCompleted(course2, None)`, and finishes without raising

#### Scenario: Aborting remaining courses on CredentialsExpiredError
- **WHEN** a `DownloadTask` or course step raises `CredentialsExpiredError` inside `IDATSync.sync_course` or `IDATSync.sync_courses(root_folder, [course1, course2])`
- **THEN** `TaskDownloadFailed(task.path, exc)` (if raised by a task) and `CourseSyncCompleted(course1, exc)` are logged and yielded, remaining unstarted tasks across all downloader groups and subsequent courses in `courses` MUST NOT run, and `CredentialsExpiredError` propagates immediately to the caller

### Requirement: Lazy per-course section population and eager loader removal
`MoodleAdapter`, `CourseGateway`, and `CourseRepository` SHALL define `async def list_courses(self) -> list[Course]` (mapping `category` with `sections=[]`), `async def populate_course_sections(self, course: Course) -> Course` (caching `await _get_assignments_by_course()` once per `MoodleAdapter` instance until invalidated), and `def invalidate_cache(self) -> None` (clearing `MoodleAdapter._assignments_cache = None`). `get_courses_with_contents`, `get_courses`, and `get_all_courses_contents` MUST NOT exist. Verified at `MoodleAdapter` (`src/idat_sync/infrastructure/moodle_adapter.py`) and `CourseRepository` (`src/idat_sync/application/course_repository.py`) seams.

#### Scenario: Listing courses with categories without fetching sections or assignments
- **WHEN** `await MoodleAdapter.list_courses()` or `await CourseRepository.list_courses()` is called
- **THEN** `MoodleClient.fetch_enrolled_courses()` is awaited once, `fetch_assignments` and `fetch_course_contents` are not called, and each returned `Course` has `id`, `name`, `category`, and `sections == []`

#### Scenario: Caching assignments across lazy per-course section population
- **WHEN** `await MoodleAdapter.populate_course_sections(course)` is called sequentially for two different `Course` instances on the same `MoodleAdapter`
- **THEN** `MoodleClient.fetch_assignments()` is awaited only once, `MoodleClient.fetch_course_contents(course.id)` is awaited once per course, and `MoodleAdapter`, `CourseGateway`, and `CourseRepository` do not define `get_courses_with_contents`, `get_courses`, or `get_all_courses_contents`

#### Scenario: Resetting cached assignments with invalidate_cache
- **WHEN** `MoodleAdapter.populate_course_sections(course)` has cached assignments, `invalidate_cache()` is called, and `populate_course_sections` runs again
- **THEN** `MoodleClient.fetch_assignments()` is awaited a second time to refresh assignment data

### Requirement: Treeview course selection UI and ProgressInterceptor removal
`src/idat_sync/ui/app.py` MUST NOT define `ProgressInterceptor` and MUST NOT reassign `sys.stdout` or `sys.stderr`, and `ui.app.main()` SHALL invoke `configure_logging(config.log_file)`. `SyncFrame` in `src/idat_sync/ui/sync_frame.py` MUST NOT define `output_text`, `_TreeProgressListener`, `progress_queue`, `update_progress`, `exit`, or `_queue_error`, and MUST NOT call `loop.call_soon_threadsafe` or `asyncio.to_thread`. `SyncFrame` SHALL await `await create_sync_service(self.credentials, self.config)`, `await self.idat_sync.list_courses()`, and `async for event in self.idat_sync.sync_courses(path, selected): self._handle_sync_event(event)` directly on the UI event loop while indexing `self._file_iids` by `Path -> (course_id, child_iid)`, clearing `course.sections.clear()` on each checked `Course` in `do_sync()` before iterating `sync_courses(path, selected)` so repeat `Sync` runs refresh Moodle sections without recreating the active `IDATSync` service. `SyncFrame` SHALL render a `ttk.Treeview` (`#0` `Name`, `status`, `progress`) with checkbox, folder, and file icons, selection buttons (`In Progress`, `Select All`, `Clear`), and `status_var`, ordering in-progress courses first (checked by default) on startup and updating parent, nested folder, and leaf file rows (`0%`-`100%`) during sync. Verified at `MainApp`, `ui.app.main`, and `SyncFrame` (`src/idat_sync/ui/sync_frame.py`) seams.

#### Scenario: Ordering in-progress courses first and pre-selecting them on startup
- **WHEN** `await SyncFrame.load_idat_sync(credentials)` loads courses from `await idat_sync.list_courses()` containing past and in-progress courses
- **THEN** `SyncFrame` inserts in-progress courses first in the `ttk.Treeview` with a checked icon and past courses after with an unchecked icon, and enables `sync_button` only when `not is_syncing`, `folder_path` is non-empty, and at least one course is checked (or when courses have not loaded yet after a startup error)

#### Scenario: Updating parent course and child file rows during sync
- **WHEN** `await SyncFrame.do_sync()` runs on checked courses
- **THEN** `hasattr(SyncFrame, "update_progress")` and `hasattr(SyncFrame, "exit")` are `False`, only checked courses are reset to `Status="Queued"`, `Progress="0%"` and passed to `async for event in self.idat_sync.sync_courses(path, selected):`, nested folder nodes use a folder icon and leaf file nodes use a file icon with `Synced` (`100%`) or `Pending` (`0%`) -> `Downloading` (`0%`) -> `Completed` (`100%`) / `Failed` (`0%`), and parent course progress updates as `f"{int(done * 100 / total)}%"` (`"100%"` when `total == 0`)

#### Scenario: Clearing selected course sections on repeat Sync without recreating IDATSync
- **WHEN** `await SyncFrame.do_sync()` runs a second time on checked courses with already-populated `sections` while unchecked courses retain cached `sections`
- **THEN** checked `Course.sections` are cleared before `self.idat_sync.sync_courses(path, selected)` runs, unchecked `Course.sections` remain untouched, and `create_sync_service` is not invoked again while `self.idat_sync` is already active
