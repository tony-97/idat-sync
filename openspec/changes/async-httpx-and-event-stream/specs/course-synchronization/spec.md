# course-synchronization Specification

## MODIFIED Requirements

### Requirement: Strict inward package dependency boundary
The `src/idat_sync/domain/` and `src/idat_sync/application/` packages SHALL have zero imports from `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `httpx`, `office365`, `yt_dlp`, or `playwright`. No module under `src/idat_sync/` SHALL import `requests` or call `print(...)`, `src/idat_sync/ui/` MUST NOT import `idat_sync.infrastructure`, `src/idat_sync/infrastructure/sharepoint_auth.py` SHALL be the sole module importing `playwright`, `src/idat_sync/application/task_orchestrator.py` SHALL have zero imports from `idat_sync.application.events` or `threading`, `src/idat_sync/infrastructure/auth.py` MUST NOT exist, `src/idat_sync/utils.py` MUST NOT exist, and `safe_filename` and `WINDOWS_RESERVED` SHALL reside in `src/idat_sync/domain/path_utils.py`. Verified at the AST architecture boundary (`src/idat_sync/`) and `domain.path_utils` (`src/idat_sync/domain/path_utils.py`) seams.

#### Scenario: Inspecting domain and application imports
- **WHEN** all Python modules under `src/idat_sync/domain/` and `src/idat_sync/application/` are parsed for import statements
- **THEN** none of them import `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `httpx`, `office365`, `yt_dlp`, or `playwright`, and `src/idat_sync/application/task_orchestrator.py` does not import `idat_sync.application.events` or `threading`

#### Scenario: Sanitizing course filenames without importing infrastructure
- **WHEN** `safe_filename(s, replacement=" ", max_len=100)` in `src/idat_sync/domain/path_utils.py` is called with accents, forbidden characters, or Windows reserved names (`WINDOWS_RESERVED`)
- **THEN** it returns the normalized, safe ASCII filename of at most `max_len` characters

#### Scenario: Enforcing ui and playwright isolation and legacy auth/utils removal
- **WHEN** all Python modules under `src/idat_sync/` are parsed via AST for imports and calls and `src/idat_sync/utils.py` and `src/idat_sync/infrastructure/auth.py` are checked
- **THEN** `src/idat_sync/utils.py` and `src/idat_sync/infrastructure/auth.py` do not exist, no module imports `idat_sync.utils`, `idat_sync.infrastructure.auth`, or `requests`, no module under `src/idat_sync/ui/` imports `idat_sync.infrastructure`, `src/idat_sync/infrastructure/sharepoint_auth.py` is the only module importing `playwright`, and zero modules under `src/idat_sync/` contain a `print(...)` call

### Requirement: SharePoint adapter projects recordings into weekly domain content
`SharepointApiAdapter` in `src/idat_sync/infrastructure/sharepoint_adapter.py` SHALL accept `(client: SharepointClient, semester_start: date, sharepoint_host: str, sharepoint_personal_host: str, recordings_folder_name: str, week_folder_prefix: str)` without `request_delay` or defaults, and MUST NOT store `request_delay`, `properties_to_retrieve`, `client`, or `search_client`, or import `time` or `ClientContext`. `can_handle(url)` SHALL match `{self.sharepoint_host, self.sharepoint_personal_host}`, and `async def fetch_recordings(self, course_name: str) -> list[RecordingContent]` SHALL await `sp_client.search_for_recordings(course_name)` and `sp_client.fetch_document_library_items(site_url)`, reading `file.server_relative_url` to return weekly `list[RecordingContent]`. Verified at the `SharepointApiAdapter` (`src/idat_sync/infrastructure/sharepoint_adapter.py`) seam.

#### Scenario: Computing weekly folder and SharePoint stream URL per recording
- **WHEN** `await SharepointApiAdapter.fetch_recordings(course_name)` finds recording files with `time_created` and `server_relative_url`
- **THEN** it returns `RecordingContent(Path(safe_path_segment(f"{self.week_folder_prefix} {week}")) / safe_path_segment(name), last_mod, f"https://{self.sharepoint_personal_host}/:v:/r{quote(file.server_relative_url)}?csf=1&web=1")` for each recording inside `self.recordings_folder_name` (matched case-insensitively)

#### Scenario: Skipping recordings missing creation timestamp without shifting subsequent weeks
- **WHEN** `await SharepointApiAdapter.fetch_recordings(course_name)` encounters a recording file with `time_created = None` or `server_relative_url = None` before a valid recording file
- **THEN** the incomplete file is excluded and the subsequent file's `week` is still computed from its own `time_created.date()`

#### Scenario: Customizing SharePoint hosts, recordings folder name, and week prefix
- **WHEN** `SharepointApiAdapter` is initialized with custom `sharepoint_host`, `sharepoint_personal_host`, `recordings_folder_name="Videos"`, and `week_folder_prefix="Week"`
- **THEN** `can_handle` matches `(urlparse(url).hostname or "").lower() in {self.sharepoint_host, self.sharepoint_personal_host}` (and rejects other hosts), and `await fetch_recordings` matches parent folders named `"videos"` and returns `RecordingContent` paths under `Path(f"Week {week}")` with stream URLs on `sharepoint_personal_host`

#### Scenario: Sanitizing unsafe SharePoint folder and file names without losing accents
- **WHEN** `SharepointApiAdapter` maps a recording, file, or subfolder named `"Sesión: 01/Parte?.mp4"` or `"CON"`
- **THEN** emitted `SharepointContent` and `RecordingContent` path segments become `"Sesión 01 Parte.mp4"` and `"_CON"`

### Requirement: CourseRepository orchestrates gateways and URL strategies
`CourseRepository` in `src/idat_sync/application/course_repository.py` SHALL accept `(course_gateway: CourseGateway, recording_gateway: RecordingGateway, url_strategies: Sequence[UrlContentStrategy])` and expose `async def list_courses(self) -> list[Course]`, `async def populate_course_sections(self, course: Course) -> Course`, `async def get_course_content(self, course: Course, course_dir: Path | str = Path()) -> list[Content]`, and `async def get_recordings(self, course_name: str, recordings_folder: str) -> list[Content]`. `get_course_content(course, course_dir)` SHALL skip sections and modules with empty `name`, place each module's `inline_contents` before resolved `contents` in `safe_path_segment(module.name)`, prepend `section.inline_contents` before module items in `safe_path_segment(section.name)`, and wrap items in `course_dir`. Verified at the `CourseRepository` (`src/idat_sync/application/course_repository.py`) public seam.

#### Scenario: Composing course content hierarchy and ordering
- **WHEN** `await CourseRepository.get_course_content(course, course_dir)` is called on a `Course` with sections and modules (including unsafe characters such as `:` or `/` in `section.name` or `module.name`)
- **THEN** it awaits `strategy.list_url_content(...)` for each matching URL strategy and emits each section's `inline_contents` followed by each named module's `inline_contents` and URL-resolved `contents`, composed bottom-up via `.in_folder(safe_path_segment(module.name))`, `.in_folder(safe_path_segment(section.name))`, and `.in_folder(course_dir)`

#### Scenario: Wrapping recordings in target folder
- **WHEN** `await CourseRepository.get_recordings(course_name, recordings_folder)` is called
- **THEN** it awaits `recording_gateway.fetch_recordings(course_name)` and returns `[r.in_folder(recordings_folder) for r in recordings]`

### Requirement: Composition root wires production dependencies outside application
`async def create_sync_service(credentials: Credentials, config: SyncConfig) -> IDATSync` in `src/idat_sync/bootstrap.py` SHALL require an explicit `SyncConfig` without a `publish` parameter, construct `MoodleClient`, `MoodleAdapter`, await `await SharepointClient.create(...)`, construct `SharepointApiAdapter`, `LocalFileSystemAdapter`, and `Downloader` groups with `config` values, wire `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)` directly to the low-level clients without `request_delay`, wire `TaskOrchestrator(downloader_group)`, and inject the composed collaborators plus `recordings_folder_name` into `IDATSync`. `configure_logging(log_file: Path) -> logging.Logger` in `src/idat_sync/bootstrap.py` SHALL configure `logging.getLogger("idat_sync")` at `logging.INFO` with `propagate = False`, closing and clearing existing handlers before attaching `logging.StreamHandler()` and `logging.FileHandler(log_file, mode="w", encoding="utf-8")`. `IDATSync.__init__` MUST NOT instantiate clients or adapters from `Credentials` and MUST NOT accept `publish`. Verified at `bootstrap.create_sync_service`, `bootstrap.configure_logging`, and `IDATSync` (`src/idat_sync/application/sync_service.py`) seams.

#### Scenario: Constructing IDATSync with injected collaborators
- **WHEN** `IDATSync(course_repository, download_manager, task_orchestrator, file_system, recordings_folder_name="Videos")` is instantiated and `async for _ in sync.sync_course(course, course_dir):` runs
- **THEN** it stores the injected collaborators without network or credential initialization in `__init__`, rejects a `publish` keyword argument with `TypeError`, and ensures and syncs recordings under `Path(course_dir) / "Videos"`

#### Scenario: Wiring SyncConfig through create_sync_service
- **WHEN** `configure_logging(log_file)` and `await create_sync_service(credentials, config=custom_config)` are called
- **THEN** `configure_logging` configures `logging.getLogger("idat_sync")` at `logging.INFO` (`propagate == False`) with one `StreamHandler` and one `FileHandler` targeting `log_file`, and `await create_sync_service` forwards `custom_config` fields to `MoodleClient`, `MoodleAdapter`, `await SharepointClient.create(...)`, `SharepointApiAdapter` (without `request_delay`), `RecordingDownloader`, `TaskOrchestrator`, and `IDATSync`, and wires `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)` with `moodle_dl.moodle_client is moodle_adapter.client` and `sp_dl.sharepoint_client is sp_adapter.sp_client`

### Requirement: Configurable tenant endpoints and throttling across clients, adapters, and downloaders
`MoodleClient`, `MoodleTokenClient`, `MoodleAdapter`, `SharepointClient`, `RecordingDownloader`, and `SharepointBrowserAuth` SHALL require injected tenant URL/host parameters without hardcoded defaults, and `MoodleClient` and `SharepointClient` SHALL require `request_delay` without hardcoded defaults as the sole owners of `await asyncio.sleep(self.request_delay)`. `MoodleTokenClient`, `MoodleDownloader`, `SharepointDownloader`, and `SharepointApiAdapter` MUST NOT accept `request_delay`. `src/idat_sync/infrastructure/moodle_client.py`, `src/idat_sync/infrastructure/sharepoint_client.py`, `src/idat_sync/infrastructure/sharepoint_adapter.py`, and `src/idat_sync/infrastructure/downloaders.py` MUST NOT import `requests` or `time`, `REQUEST_HEADER` SHALL reside in `src/idat_sync/infrastructure/moodle_client.py`, and `src/idat_sync/utils.py` MUST NOT exist. Verified at client, adapter, downloader, and AST seams.

#### Scenario: Respecting injected Moodle and SharePoint hosts and request delays
- **WHEN** `MoodleClient`, `MoodleAdapter`, `await SharepointClient.create(...)`, and `RecordingDownloader` are constructed with custom `base_url`, `moodle_base_url`, `sharepoint_personal_host`, and `request_delay`
- **THEN** `MoodleAdapter.can_handle` matches the hostname of `moodle_base_url`, `RecordingDownloader.can_handle` matches `f"{sharepoint_personal_host}/:v:"`, and `MoodleClient.call_ws`, `MoodleClient.download_file`, `SharepointClient.create`, `SharepointClient.load_entity`, `SharepointClient.search_for_recordings`, `SharepointClient.download_file`, `SharepointClient.fetch_document_library_items`, `SharepointClient.fetch_folder_files`, and `SharepointClient.fetch_subfolders` each `await asyncio.sleep(self.request_delay)` once per operation

#### Scenario: Inspecting infrastructure imports and utils cleanup
- **WHEN** all Python modules under `src/idat_sync/infrastructure/` and `src/idat_sync/utils.py` are inspected
- **THEN** `src/idat_sync/utils.py` does not exist, no `infrastructure/` module imports `idat_sync.utils` or `requests`, `moodle_client.py`, `sharepoint_client.py`, `sharepoint_adapter.py`, and `downloaders.py` do not import `time`, `sharepoint_adapter.py` does not import `ClientContext`, and `REQUEST_HEADER` is defined in `src/idat_sync/infrastructure/moodle_client.py`

### Requirement: Unconditional target file path in RecordingDownloader
`async def download(self, content: RecordingContent) -> None` on `RecordingDownloader` in `src/idat_sync/infrastructure/downloaders.py` SHALL unconditionally treat `content.path` as the target file path by creating `Path(content.path).parent` (`mkdir(parents=True, exist_ok=True)`), setting `yt_dlp` `outtmpl` to `f"{prefix}{os.path.abspath(Path(content.path))}"`, and offloading `YoutubeDL(ydl_opts).download([content.url])` via `await asyncio.to_thread(...)`. `RecordingDownloader.download` MUST NOT branch on `Path(content.path).suffix` or append `"%(title)s %(id)s.%(ext)s"`. Verified at the `RecordingDownloader` (`src/idat_sync/infrastructure/downloaders.py`) public seam.

#### Scenario: Downloading a recording with a file extension
- **WHEN** `await RecordingDownloader.download(content)` is called with `RecordingContent(Path("Grabaciones/Semana 1/video.mp4"), 1700000000, url)`
- **THEN** it creates the parent directory `Grabaciones/Semana 1` and passes `f"{prefix}{os.path.abspath(Path('Grabaciones/Semana 1/video.mp4'))}"` as `outtmpl` to `yt_dlp.YoutubeDL` inside `await asyncio.to_thread(...)`

#### Scenario: Downloading a recording without a file extension in path
- **WHEN** `await RecordingDownloader.download(content)` is called with a `RecordingContent` whose `path` has no file suffix (`Path(content.path).suffix == ""`)
- **THEN** it still creates `Path(content.path).parent` (not `Path(content.path)` as a directory) and passes `f"{prefix}{os.path.abspath(Path(content.path))}"` as `outtmpl` without appending `"%(title)s %(id)s.%(ext)s"`

### Requirement: Client-owned file download streaming and SharePoint SDK query execution
`MoodleClient` and `SharepointClient` SHALL provide `async def download_file(..., dest: BinaryIO) -> None` and `await asyncio.sleep(self.request_delay)`. `MoodleDownloader.download`, `SharepointDownloader.download`, and `StringDownloader.download` SHALL be `async def` coroutines, with `MoodleDownloader` and `SharepointDownloader` awaiting `client.download_file`. `SharepointClient.__init__` SHALL be non-blocking (`self.search_client: ClientContext | None = None`), `@classmethod async def create(...) -> Self` SHALL resolve `personal_site` via `await client.personal_client.web.current_user.get_personal_site().execute_query_async()` and `await asyncio.sleep(client.request_delay)`, and `get_client(site_url)` SHALL chain `.with_cookies(lambda: self.sharepoint_cookies).with_auto_retry()` and `ctx.pending_request().with_async_transport(HttpxTransport())`. `SharepointClient` MUST NOT define `_run_query` or `_retry_delay`. `SharepointClient` SHALL execute async SDK queries directly (`await ctx.execute_query_with_incremental_retry_async()` in `load_entity`, `await ...execute_query_async()` in `search_for_recordings`, `fetch_folder_files`, and `fetch_subfolders`, `await ...get_all_async(page_size=50)` in `fetch_document_library_items`, and `await file.download_session_async(dest, use_path=False)` in `download_file`), and `SharepointApiAdapter.list_url_content` SHALL be `async def`, dispatch by URL path, and skip empty entities where `not entity.name and not entity.server_relative_url`. Verified at `MoodleClient`, `SharepointClient`, `SharepointApiAdapter`, and `*Downloader` seams.

#### Scenario: Streaming a Moodle file through MoodleClient
- **WHEN** `await MoodleClient.download_file(url, dest)` is called with a target URL and writable binary stream
- **THEN** it opens `async with httpx.AsyncClient() as client:` and `async with client.stream("GET", url, params={"wstoken": self.token, "token": self.token}, follow_redirects=True, timeout=60, headers=REQUEST_HEADER) as response:`, raises `CredentialsExpiredError("403 Forbidden: your session cookie is missing/expired.")` on HTTP `401` or `403`, calls `response.raise_for_status()`, writes non-empty chunks from `await response.aiter_bytes(chunk_size=1024 * 256)` into `dest`, and awaits `asyncio.sleep(self.request_delay)`

#### Scenario: Delegating MoodleDownloader and SharepointDownloader file downloads to clients
- **WHEN** `await MoodleDownloader(moodle_client).download(moodle_content)` and `await SharepointDownloader(sharepoint_client).download(sp_content)` are called
- **THEN** each stores `self.moodle_client` or `self.sharepoint_client` without accepting `request_delay`, creates `Path(content.path).parent`, opens `content.path` as `"wb"`, and awaits `moodle_client.download_file(moodle_content.url, f)` and `sharepoint_client.download_file(sp_content.file, local_file)` respectively (where `SharepointClient.download_file` awaits `file.download_session_async(dest, use_path=False)` and `asyncio.sleep(self.request_delay)`)

#### Scenario: Delegating SharePoint guest URLs, direct links, and folder traversal to SharepointClient
- **WHEN** `await SharepointApiAdapter.list_url_content`, `await fetch_folder_contents`, and `await fetch_file_from_direct_link` resolve guest folder URLs (`^/:f:/`), guest file URLs (`^/:(\w+):/`) across `sharepoint_host` and `sharepoint_personal_host`, direct links with `uniqueid`/`sourcedoc`, and folder hierarchies
- **THEN** `SharepointApiAdapter` inspects `urlparse(url).path` without repeating the hostname check, awaits `sp_client.fetch_folder_by_guest_url`, `sp_client.fetch_file_by_guest_url`, and `sp_client.fetch_file_by_id(link, file_id)` (skipping the client call and returning `None` when neither `uniqueid` nor `sourcedoc` is present), and awaits `sp_client.fetch_folder_files(folder)` before `sp_client.fetch_subfolders(folder)`, while `SharepointClient.get_client` configures `.with_cookies(...).with_auto_retry()` and `HttpxTransport()`, `SharepointClient.get_client_from_url` matches `r"/(sites|teams|s|personal)/([^/]+)"` case-insensitively (mapping `/s/<name>` to `{base_url}/sites/<name}` and `/(sites|teams|personal)/<name>` to `{base_url}{path_match.group(0)}`), and `hasattr(SharepointClient, "_run_query")` and `hasattr(SharepointClient, "_retry_delay")` are both `False`

#### Scenario: Skipping dead or expired SharePoint guest and direct links returning odata.null
- **WHEN** `await SharepointClient.load_entity(entity, ctx)` encounters `TypeError` because an `odata.null` guest URL response left `entity.resource_url` as `None`, or `await SharepointApiAdapter.list_url_content` receives a `Folder` or `File` where `not entity.name and not entity.server_relative_url`
- **THEN** `SharepointClient.load_entity` catches `TypeError` and still awaits `asyncio.sleep(self.request_delay)` (while propagating non-`TypeError` exceptions unchanged), and `SharepointApiAdapter.list_url_content` returns `[]`

#### Scenario: Retrying SharePoint HTTP 429 and 503 with Retry-After backoff and re-raising on exhaustion
- **WHEN** `SharepointClient.get_client(site_url)` creates a `ClientContext` and `load_entity` executes a query
- **THEN** `get_client` chains `.with_cookies(lambda: self.sharepoint_cookies).with_auto_retry()` and `ctx.pending_request().with_async_transport(HttpxTransport())`, `load_entity` awaits `ctx.execute_query_with_incremental_retry_async()`, and `SharepointClient` does not define `_run_query` or `_retry_delay` (`office365-rest-python-client` `v3.3.1` handles transient `429`/`503` retries and `Retry-After` headers natively)

### Requirement: Credentials expiration mapping in Moodle, SharePoint, and recording layers
`MoodleClient.call_ws`, `MoodleClient.download_file`, `SharepointClient.create` (and `SharepointClient.search_for_recordings` when `self.search_client is None`), and `RecordingDownloader.download` SHALL map Moodle HTTP `401`/`403`, Moodle `errorcode == "invalidtoken"`, SharePoint `get_personal_site()` HTTP `401`/`403` or empty `personal_site.url`, uninitialized `self.search_client is None`, and recording HTTP `401`/`403` into `CredentialsExpiredError`, while preserving non-auth exceptions unchanged. Verified at the `MoodleClient`, `SharepointClient`, and `RecordingDownloader` seams.

#### Scenario: Mapping Moodle invalidtoken and HTTP 401 or 403 to CredentialsExpiredError
- **WHEN** `await MoodleClient.call_ws` receives `{"exception": "moodle_exception", "errorcode": "invalidtoken", ...}` or HTTP `401`/`403`, or `await MoodleClient.download_file` receives HTTP `401`/`403`
- **THEN** `MoodleClient` raises `CredentialsExpiredError` (which is also a `RuntimeError`), while HTTP `500` or non-auth Moodle exceptions raise `httpx.HTTPStatusError` or `RuntimeError`

#### Scenario: Mapping SharePoint and recording auth failures to CredentialsExpiredError
- **WHEN** `await SharepointClient.create(...)` observes empty `personal_site.url` or `ClientRequestException` with HTTP `401`/`403` from `get_personal_site().execute_query_async()`, `await client.search_for_recordings(...)` is called when `client.search_client is None`, or `await RecordingDownloader.download` fails with an HTTP `401`/`403` download error
- **THEN** each raises `CredentialsExpiredError`, while mid-sync `ClientRequestException`s on individual SharePoint items or non-auth errors propagate without being converted into `CredentialsExpiredError`

### Requirement: Per-item failure isolation and early credential abort in TaskOrchestrator
`TaskOrchestrator(task_group: list[list[Downloader]])` in `src/idat_sync/application/task_orchestrator.py` SHALL accept only `task_group` (no `publish` parameter), SHALL NOT import `idat_sync.application.events` or `threading`, and MUST NOT define `run`, `run_async`, or `_run_group`. `TaskOrchestrator` SHALL expose `create_coroutines(self, tasks: Sequence[DownloadTask], run_task: Callable[[DownloadTask], Awaitable[None]] = lambda task: task.download()) -> list[Coroutine[Any, Any, None]]`, returning one coroutine per downloader group in `self.task_group` that awaits `run_task(task)` sequentially in input order for each task routed to that group. Verified at the `TaskOrchestrator` (`src/idat_sync/application/task_orchestrator.py`) seam.

#### Scenario: Continuing remaining tasks after a single-item download failure
- **WHEN** `TaskOrchestrator([[dl_a], [dl_b]]).create_coroutines(tasks, run_task)` is called with a `run_task` callback (or default `task.download()`) and awaited via `asyncio.gather(*coroutines)`
- **THEN** tasks matching `dl_a` execute sequentially in their original order inside the first coroutine, tasks matching `dl_b` execute sequentially in their original order inside the second coroutine, and `hasattr(TaskOrchestrator, "run")`, `hasattr(TaskOrchestrator, "run_async")`, and `hasattr(TaskOrchestrator, "_run_group")` are all `False`

#### Scenario: Aborting all remaining tasks on CredentialsExpiredError
- **WHEN** a `run_task` coroutine raises an unhandled exception (such as `CredentialsExpiredError`) inside a group coroutine returned by `TaskOrchestrator.create_coroutines(tasks, run_task)`
- **THEN** subsequent tasks in that group coroutine do not execute and the exception propagates to the caller awaiting or gathering the group coroutines

### Requirement: Per-course failure isolation and early credential abort in IDATSync
`IDATSync` in `src/idat_sync/application/sync_service.py` MUST NOT accept `publish` in `__init__` or `progress_listener` in `sync_course` or `sync_courses`. `list_courses() -> list[Course]`, `get_course_remote_tree(course, course_dir) -> list[Content]`, and `check_course_sync(course, course_dir) -> SyncDiff` SHALL be `async def` coroutines. `sync_course(course: Course, course_dir: str | Path) -> AsyncIterator[SyncEvent]` and `sync_courses(root_folder: str | Path, courses: Sequence[Course]) -> AsyncIterator[SyncEvent]` SHALL be async generators that call `log_sync_event(event)` before yielding each `SyncEvent`. `sync_course` SHALL drive `task_orchestrator.create_coroutines(tasks, run_task)` concurrently across downloader groups, continuing remaining tasks after non-credential task errors and cancelling sibling group tasks immediately on `CredentialsExpiredError`. `sync_courses` SHALL compute each `Course` folder via `Path(root_folder) / safe_filename(course.name)`, lazily await `populate_course_sections(course)` when `course.sections` is empty, emit `CourseSyncCompleted(course, exc)` and skip single-`Course` failures other than `CredentialsExpiredError` so later courses still sync, and abort remaining courses immediately on `CredentialsExpiredError`. Verified at the `IDATSync` (`src/idat_sync/application/sync_service.py`) seam.

#### Scenario: Continuing remaining courses when one course fails
- **WHEN** `sync_course` raises a non-credential error on the first `Course` while a second `Course` in `courses` succeeds
- **THEN** `[e async for e in IDATSync.sync_courses(root_folder, [course1, course2])]` yields `CourseSyncCompleted(course1, exc)`, still synchronizes the second `Course` and yields `CourseSyncCompleted(course2, None)`, and finishes without raising

#### Scenario: Aborting remaining courses on CredentialsExpiredError
- **WHEN** a `DownloadTask` or course step raises `CredentialsExpiredError` inside `IDATSync.sync_course` or `IDATSync.sync_courses(root_folder, [course1, course2])`
- **THEN** `TaskDownloadFailed(task.path, exc)` (if raised by a task) and `CourseSyncCompleted(course1, exc)` are logged and yielded, remaining unstarted tasks across all downloader groups and subsequent courses in `courses` MUST NOT run, and `CredentialsExpiredError` propagates immediately to the caller

### Requirement: Developer CLI extraction scripts and gitignored local test artifacts
`scripts/login_cli.py` and `scripts/moodle_json.py` SHALL reside under `scripts/` (without root copies), and `.gitignore` SHALL list `all_courses_contents.json` and `session*.json`. `scripts/login_cli.py` SHALL save `Credentials` to `config.session_file`, and `scripts/moodle_json.py` SHALL define `async def main() -> None` invoked via `asyncio.run(main())`, awaiting `auth_service.authenticate(username, password)` when stored `Credentials` are absent and awaiting `client.fetch_enrolled_courses()`, `client.fetch_assignments()`, and `client.fetch_course_contents(int(course["id"]))` before writing `Path("./all_courses_contents.json")`. Verified at the `scripts/login_cli.py`, `scripts/moodle_json.py`, and `.gitignore` seams.

#### Scenario: Extracting courses, contents, and assignments to all_courses_contents.json
- **WHEN** `scripts/moodle_json.py` runs (reusing `Credentials` from `JsonCredentialStore(config.session_file)` when present, or prompting for `Username:` and `Password:` and awaiting `auth_service.authenticate(username, password)` when absent)
- **THEN** it obtains a token, awaits `fetch_enrolled_courses()`, `fetch_assignments()` (indexed by `int(course["id"])`), and `fetch_course_contents(int(course["id"]))` on `MoodleClient`, and writes UTF-8 JSON (`indent=2, ensure_ascii=False`) with exact keys `{"course": course, "contents": ..., "assignments": ...}` per course directly to `Path("./all_courses_contents.json")` without dumping JSON to stdout

#### Scenario: Keeping extracted curriculum and Credentials files gitignored
- **WHEN** `.gitignore` and the repository layout are inspected
- **THEN** `.gitignore` contains `all_courses_contents.json` and `session*.json`, `scripts/login_cli.py` and `scripts/moodle_json.py` exist using `Credentials` terminology, and root `login_cli.py` and `moodle_json.py` do not exist

### Requirement: Conditional real-data offline fixture and live Credentials smoke testing
`tests/test_real_data.py` SHALL define `TestRealCourseContentsFixture` and `TestLiveCredentialsIntegration` guarded by `@unittest.skipUnless` on `./all_courses_contents.json` and `./session.json`, with zero hardcoded course IDs, teacher names, or share tokens, executing async repository, client, adapter, and downloader methods on `asyncio`. Verified at the `MoodleAdapter`, `SharepointApiAdapter`, `CourseRepository`, `SyncDiff.from_trees`, `JsonCredentialStore`, and `MoodleDownloader` seams.

#### Scenario: Running offline fixture tests against all_courses_contents.json
- **WHEN** `TestRealCourseContentsFixture` runs with `./all_courses_contents.json` present (or skips via `@unittest.skipUnless(Path("./all_courses_contents.json").is_file(), "Requires ./all_courses_contents.json")` when absent)
- **THEN** it feeds `"course"`, `"contents"`, and `"assignments"` (defaulting missing `"assignments"` to `[]`) through `await CourseRepository.list_courses()`, `await CourseRepository.populate_course_sections(course)`, and `await CourseRepository.get_course_content()`, verifies `get_in_progress_course_ids(courses)` is non-empty, verifies every emitted item is a domain `Content` (`StringContent`, `MoodleContent`, or `SharepointContent`) safely inside its `course_dir`, verifies `"modname": "assign"` modules with matching `"assignments"` emit `-[id]-intro.html` `StringContent` items and `introattachments` `ModuleContent` items, verifies both `sharepoint_host` and `sharepoint_personal_host` URLs route to `SharepointApiAdapter`, and verifies `SyncDiff.from_trees(remote_contents, {})` marks all resolved paths as `MISSING`

#### Scenario: Running live Credentials smoke tests against session.json and all_courses_contents.json
- **WHEN** `TestLiveCredentialsIntegration` runs with both `./all_courses_contents.json` and `./session.json` present (or skips via `@unittest.skipUnless(Path("./all_courses_contents.json").is_file() and Path("./session.json").is_file(), "Requires ./all_courses_contents.json and ./session.json")` when absent or when `JsonCredentialStore(Path("./session.json")).load()` returns `None`)
- **THEN** it initializes `MoodleClient` and `await SharepointClient.create(...)` with `request_delay=0.0`, dynamically discovers the first matching URL from `./all_courses_contents.json` for each category (1 Moodle `/pluginfile.php/` URL, 1 `:b:` guest file URL on `sharepoint_host`, 1 `:f:` guest folder URL on `sharepoint_host`, 1 direct link with `uniqueid=`/`sourcedoc=` on `sharepoint_host`, and 1 `:b:` guest file URL on `sharepoint_personal_host`), downloads the Moodle file into a `tempfile.TemporaryDirectory()` via `await MoodleDownloader.download` verifying a non-empty file on disk, and resolves the 4 SharePoint URLs via `await SharepointApiAdapter.list_url_content` into non-empty `list[SharepointContent]` (failing immediately if `CredentialsExpiredError` is raised)

### Requirement: Lazy per-course section population and eager loader removal
`MoodleAdapter`, `CourseGateway`, and `CourseRepository` SHALL define `async def list_courses(self) -> list[Course]` (mapping `category` with `sections=[]`) and `async def populate_course_sections(self, course: Course) -> Course` (caching `await _get_assignments_by_course()` once per `MoodleAdapter` instance). `get_courses_with_contents`, `get_courses`, and `get_all_courses_contents` MUST NOT exist. Verified at `MoodleAdapter` (`src/idat_sync/infrastructure/moodle_adapter.py`) and `CourseRepository` (`src/idat_sync/application/course_repository.py`) seams.

#### Scenario: Listing courses with categories without fetching sections or assignments
- **WHEN** `await MoodleAdapter.list_courses()` or `await CourseRepository.list_courses()` is called
- **THEN** `MoodleClient.fetch_enrolled_courses()` is awaited once, `fetch_assignments` and `fetch_course_contents` are not called, and each returned `Course` has `id`, `name`, `category`, and `sections == []`

#### Scenario: Caching assignments across lazy per-course section population
- **WHEN** `await MoodleAdapter.populate_course_sections(course)` is called sequentially for two different `Course` instances on the same `MoodleAdapter`
- **THEN** `MoodleClient.fetch_assignments()` is awaited only once, `MoodleClient.fetch_course_contents(course.id)` is awaited once per course, and `MoodleAdapter`, `CourseGateway`, and `CourseRepository` do not define `get_courses_with_contents`, `get_courses`, or `get_all_courses_contents`

### Requirement: Structured sync progress reporting and actionable file filtering
`src/idat_sync/application/events.py` SHALL define frozen dataclasses `CourseSyncStarted(course: Course)`, `CourseFilesDiscovered(course: Course, course_dir: Path, synced_paths: tuple[Path, ...], pending_paths: tuple[Path, ...])`, `TaskDownloadStarted(path: Path)`, `TaskDownloadCompleted(path: Path)`, `TaskDownloadFailed(path: Path, error: Exception)`, and `CourseSyncCompleted(course: Course, error: Exception | None = None)`, type alias `SyncEvent`, and `log_sync_event(event: SyncEvent) -> None` logging progress/completion events at `logging.INFO` and failure events (`TaskDownloadFailed` and `CourseSyncCompleted` with non-`None` `error`) at `logging.ERROR` on `logging.getLogger("idat_sync")`. `EventPublisher` and `noop_publisher` MUST NOT exist in `src/idat_sync/application/events.py`, and `SyncProgressPort` MUST NOT exist in `src/idat_sync/application/ports.py`. `IDATSync.sync_course` SHALL call `log_sync_event(event)` before yielding each `SyncEvent`, report only `tuple(sorted(diff.synced_paths))` and `tuple(t.path for t in tasks)` in `CourseFilesDiscovered` (excluding non-downloadable empty `StringContent` items), and pass `errors[0] if errors else None` into `CourseSyncCompleted`. Verified at `idat_sync.application.events` (`src/idat_sync/application/events.py`) and `IDATSync` (`src/idat_sync/application/sync_service.py`) seams.

#### Scenario: Filtering non-downloadable empty StringContent items from reported course files
- **WHEN** `[e async for e in IDATSync(...).sync_course(course, course_dir)]` runs on a course containing a synced file, a downloadable missing file, and an empty `StringContent` summary rejected by `StringDownloader.can_handle`
- **THEN** the yielded events are `CourseSyncStarted(course)`, `CourseFilesDiscovered(course, Path(course_dir), (synced_file,), (missing_file,))` (excluding the empty `StringContent` path), `TaskDownloadStarted(missing_file)`, `TaskDownloadCompleted(missing_file)`, and `CourseSyncCompleted(course, None)`, `hasattr(events, "EventPublisher")` and `hasattr(events, "noop_publisher")` are `False`, and `hasattr(ports, "SyncProgressPort")` is `False`

#### Scenario: Reporting task failure and course error to SyncProgressPort
- **WHEN** a `DownloadTask` raises a non-credential `Exception` during `IDATSync.sync_course`
- **THEN** `TaskDownloadFailed(task.path, exc)` and `CourseSyncCompleted(course, exc)` are logged via `log_sync_event` and yielded from the async generator, remaining tasks still execute, and `log_sync_event` logs `CourseSyncStarted`, `CourseFilesDiscovered`, `TaskDownloadStarted`, `TaskDownloadCompleted`, and `CourseSyncCompleted(course, None)` at `logging.INFO` and `TaskDownloadFailed` and `CourseSyncCompleted(course, exc)` at `logging.ERROR` on `logging.getLogger("idat_sync")`

### Requirement: Treeview course selection UI and ProgressInterceptor removal
`src/idat_sync/ui/app.py` MUST NOT define `ProgressInterceptor` and MUST NOT reassign `sys.stdout` or `sys.stderr`, and `ui.app.main()` SHALL invoke `configure_logging(config.log_file)`. `SyncFrame` in `src/idat_sync/ui/sync_frame.py` MUST NOT define `output_text`, `_TreeProgressListener`, `progress_queue`, `update_progress`, `exit`, or `_queue_error`, and MUST NOT call `loop.call_soon_threadsafe` or `asyncio.to_thread`. `SyncFrame` SHALL await `await create_sync_service(self.credentials, self.config)`, `await self.idat_sync.list_courses()`, and `async for event in self.idat_sync.sync_courses(path, selected): self._handle_sync_event(event)` directly on the UI event loop while indexing `self._file_iids` by `Path -> (course_id, child_iid)`. `SyncFrame` SHALL render a `ttk.Treeview` (`#0` `Name`, `status`, `progress`) with checkbox, folder, and file icons, selection buttons (`In Progress`, `Select All`, `Clear`), and `status_var`, ordering in-progress courses first (checked by default) on startup and updating parent, nested folder, and leaf file rows (`0%`-`100%`) during sync. Verified at `MainApp`, `ui.app.main`, and `SyncFrame` (`src/idat_sync/ui/sync_frame.py`) seams.

#### Scenario: Ordering in-progress courses first and pre-selecting them on startup
- **WHEN** `await SyncFrame.load_idat_sync(credentials)` loads courses from `await idat_sync.list_courses()` containing past and in-progress courses
- **THEN** `SyncFrame` inserts in-progress courses first in the `ttk.Treeview` with a checked icon and past courses after with an unchecked icon, and enables `sync_button` only when `not is_syncing`, `folder_path` is non-empty, and at least one course is checked (or when courses have not loaded yet after a startup error)

#### Scenario: Updating parent course and child file rows during sync
- **WHEN** `await SyncFrame.do_sync()` runs on checked courses
- **THEN** `hasattr(SyncFrame, "update_progress")` and `hasattr(SyncFrame, "exit")` are `False`, only checked courses are reset to `Status="Queued"`, `Progress="0%"` and passed to `async for event in self.idat_sync.sync_courses(path, selected):`, nested folder nodes use a folder icon and leaf file nodes use a file icon with `Synced` (`100%`) or `Pending` (`0%`) -> `Downloading` (`0%`) -> `Completed` (`100%`) / `Failed` (`0%`), and parent course progress updates as `f"{int(done * 100 / total)}%"` (`"100%"` when `total == 0`)
