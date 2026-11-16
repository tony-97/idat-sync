# course-synchronization Specification Delta

## ADDED Requirements

### Requirement: Paginated enrolled-course retrieval in MoodleClient
`MoodleClient.fetch_enrolled_courses(self, classification: str = "all", limit: int = 100, offset: int = 0) -> list[dict]` SHALL page through `core_course_get_enrolled_courses_by_timeline_classification` from `offset` with page size `limit`, accumulating `courses` across advancing `nextoffset` values until `nextoffset` is absent, stops advancing, or a page is empty. Verified at `MoodleClient` (`src/idat_sync/infrastructure/moodle_client.py`).

#### Scenario: Returning single-page enrolled courses when nextoffset is absent
- **WHEN** `await MoodleClient.fetch_enrolled_courses()` receives `{"courses": [{"id": 101, "fullname": "Course 101"}]}` without `nextoffset`
- **THEN** `call_ws` is awaited once with `("core_course_get_enrolled_courses_by_timeline_classification", classification="all", limit=100, offset=0)` and returns `[{"id": 101, "fullname": "Course 101"}]`

#### Scenario: Fetching subsequent pages while nextoffset advances
- **WHEN** `await MoodleClient.fetch_enrolled_courses(classification="all", limit=2, offset=0)` receives `{"courses": [c1, c2], "nextoffset": 2}` on the first call, `{"courses": [c3], "nextoffset": 3}` on the second call, and `{"courses": [], "nextoffset": 3}` (or a payload omitting `nextoffset`) on the third call
- **THEN** `call_ws` is awaited across pages with advancing `offset` values (`0`, `2`, `3`) until an empty page or missing/non-advancing `nextoffset` stops pagination, returning `[c1, c2, c3]`

## MODIFIED Requirements

### Requirement: SharePoint adapter projects recordings into weekly domain content
`SharepointApiAdapter` in `src/idat_sync/infrastructure/sharepoint_adapter.py` SHALL accept `(client: SharepointClient, semester_start: date, sharepoint_host: str, sharepoint_personal_host: str, recordings_folder_name: str, week_folder_prefix: str, timezone: str)` without `request_delay` or defaults, validating `timezone` via `zoneinfo.ZoneInfo` on construction, MUST NOT store `request_delay`, `properties_to_retrieve`, `client`, or `search_client`, MUST NOT define `_SharepointApiAdapter__get_name` or `_SharepointApiAdapter__get_last_modified` aliases, and MUST NOT import `time` or `ClientContext`. `can_handle(url)` SHALL match `{self.sharepoint_host, self.sharepoint_personal_host}`, treat `office365`'s `datetime.min` default on `time_last_modified` and `time_created` identically to missing timestamps (`fallback_last_modified` in `get_last_modified` and skipping in `fetch_recordings`), and `async def fetch_recordings(self, course_name: str) -> list[RecordingContent]` SHALL await `sp_client.search_for_recordings(course_name)` and `sp_client.fetch_document_library_items(site_url)`, matching `course_name` at non-alphanumeric boundaries (`re.search(rf"(?<![0-9A-Za-zÀ-ÿ]){re.escape(course_name)}(?![0-9A-Za-zÀ-ÿ])", text)`) against `Title` and `urllib.parse.unquote(file.server_relative_url)` to return weekly `list[RecordingContent]`, converting timezone-aware `file.time_created` timestamps to the configured `ZoneInfo` before taking `.date()` (while leaving naive `datetime` values unchanged). Verified at the `SharepointApiAdapter` (`src/idat_sync/infrastructure/sharepoint_adapter.py`) seam.

#### Scenario: Computing weekly folder and SharePoint stream URL per recording
- **WHEN** `await SharepointApiAdapter.fetch_recordings(course_name)` finds recording files with `time_created` and `server_relative_url`
- **THEN** it returns `RecordingContent(Path(safe_path_segment(f"{self.week_folder_prefix} {week}")) / safe_path_segment(name), last_mod, f"https://{self.sharepoint_personal_host}/:v:/r{quote(file.server_relative_url)}?csf=1&web=1")` for each recording inside `self.recordings_folder_name` (matched case-insensitively)

#### Scenario: Converting UTC-aware recording timestamps to configured timezone before computing week
- **WHEN** `SharepointApiAdapter(..., semester_start=date(2025, 9, 22), timezone="America/Lima")` processes a recording whose `time_created` is `datetime(2025, 9, 29, 3, 0, tzinfo=timezone.utc)` (`2025-09-28 22:00` in `America/Lima`) alongside a naive `datetime(2025, 9, 29, 14, 30)`
- **THEN** the UTC-aware Sunday evening recording resolves to `Semana 1` (`date(2025, 9, 28)`) while the naive recording resolves to `Semana 2` (`date(2025, 9, 29)`)

#### Scenario: Guarding datetime.min defaults on real office365 File and Folder entities
- **WHEN** `SharepointApiAdapter` reads a real `office365` `File` or `Folder` lacking `TimeLastModified` or `TimeCreated` (so the SDK property returns `datetime.min`)
- **THEN** `get_last_modified` returns `fallback_last_modified` (`0`) without calling `datetime.min.timestamp()` (`OSError` on Windows), and `fetch_recordings` skips the item rather than generating a negative week folder such as `Semana -105748`

#### Scenario: Preventing prefix course collisions when matching recordings
- **WHEN** `await SharepointApiAdapter.fetch_recordings("Matemática I")` evaluates SharePoint search rows or library items for `"Matemática II"` vs URL-encoded `"/personal/user/Documents/Matem%C3%A1tica%20I/Grabaciones/video.mp4"`
- **THEN** rows and files belonging to `"Matemática II"` are rejected while the URL-encoded `"Matemática I"` recording matches and is returned, and `hasattr(SharepointApiAdapter, "_SharepointApiAdapter__get_name")` and `hasattr(SharepointApiAdapter, "_SharepointApiAdapter__get_last_modified")` are both `False`

#### Scenario: Skipping recordings missing creation timestamp without shifting subsequent weeks
- **WHEN** `await SharepointApiAdapter.fetch_recordings(course_name)` encounters a recording file with `time_created = None` or `server_relative_url = None` before a valid recording file
- **THEN** the incomplete file is excluded and the subsequent file's `week` is still computed from its own `time_created.date()`

#### Scenario: Customizing SharePoint hosts, recordings folder name, and week prefix
- **WHEN** `SharepointApiAdapter` is initialized with custom `sharepoint_host`, `sharepoint_personal_host`, `recordings_folder_name="Videos"`, `week_folder_prefix="Week"`, and `timezone="America/Lima"`
- **THEN** `can_handle` matches `(urlparse(url).hostname or "").lower() in {self.sharepoint_host, self.sharepoint_personal_host}` (and rejects other hosts), and `await fetch_recordings` matches parent folders named `"videos"` and returns `RecordingContent` paths under `Path(f"Week {week}")` with stream URLs on `sharepoint_personal_host`

#### Scenario: Sanitizing unsafe SharePoint folder and file names without losing accents
- **WHEN** `SharepointApiAdapter` maps a recording, file, or subfolder named `"Sesión: 01/Parte?.mp4"` or `"CON"`
- **THEN** emitted `SharepointContent` and `RecordingContent` path segments become `"Sesión 01 Parte.mp4"` and `"_CON"`

### Requirement: Immutable SyncConfig schema and standard-library validation
`SyncConfig` in `src/idat_sync/config.py` SHALL be a frozen dataclass with defaults (`semester_start=date(2025, 9, 22)`, `request_delay=30.0`, `session_file=Path("./session.json")`, `log_file=Path("./log.err")`, `moodle_base_url`, `sharepoint_host`, `sharepoint_personal_host`, `sharepoint_academic_site_url`, `email_domain="idat.pe"`, `recordings_folder_name="Grabaciones"`, `week_folder_prefix="Semana"`, `timezone="America/Lima"`) validated via `urllib.parse`, `PurePath`, and `zoneinfo.ZoneInfo` (raising `ValueError` for invalid or non-string timezones). `pyproject.toml` SHALL declare `"tzdata; sys_platform == 'win32'"` in project dependencies so `ZoneInfo("America/Lima")` resolves on Windows. Verified at `idat_sync.config` seam.

#### Scenario: Instantiating SyncConfig with defaults and URL normalization
- **WHEN** `SyncConfig(moodle_base_url="https://aulavirtual.idat.edu.pe/", sharepoint_academic_site_url="https://idat628.sharepoint.com/sites/MATERIALESTED-ACADEMICOIDAT/")` is instantiated
- **THEN** `urlsplit` and `urlunsplit` strip trailing slashes from the URL fields while preserving all default values (`semester_start == date(2025, 9, 22)`, `request_delay == 30.0`, `session_file == Path("./session.json")`, `log_file == Path("./log.err")`, `sharepoint_host == "idat628.sharepoint.com"`, `sharepoint_personal_host == "idat628-my.sharepoint.com"`, `email_domain == "idat.pe"`, `recordings_folder_name == "Grabaciones"`, `week_folder_prefix == "Semana"`, `timezone == "America/Lima"`)

#### Scenario: Rejecting malformed URLs, hostnames, folder names, or delays
- **WHEN** `SyncConfig` is constructed with a URL missing an `http`/`https` scheme or hostname, a host field (`sharepoint_host`, `sharepoint_personal_host`, `email_domain`) containing a scheme or path, a multi-segment or dot folder name (`recordings_folder_name`, `week_folder_prefix`) where `PurePath(val.strip()).name != val.strip()`, or a negative/non-finite/boolean `request_delay`
- **THEN** `SyncConfig.__post_init__` raises `ValueError`

#### Scenario: Rejecting invalid, blank, or non-string timezones
- **WHEN** `SyncConfig` is constructed with `timezone=""`, `timezone="Mars/Olympus"`, or a non-string `timezone`
- **THEN** `SyncConfig.__post_init__` catches `ZoneInfoNotFoundError` / `ValueError` and raises `ValueError`

### Requirement: Mandatory FileSystemPort delegation in DownloadManager
`DownloadManager` SHALL require `FileSystemPort` as a non-nullable constructor dependency and delegate all local directory tree modification timestamp scans and file reads to `FileSystemPort`. `FileSystemPort` and `LocalFileSystemAdapter` SHALL expose `scan_mtimes(root_dir)`, `ensure_dir(path)`, and `read_bytes(path) -> bytes | None` (returning `None` on `OSError`, never defining `exists` or `get_mtime`). `LocalFileSystemAdapter` in `src/idat_sync/infrastructure/filesystem.py` SHALL exclude temporary `*.part` files (`not p.name.endswith(".part")`) in `scan_mtimes(root_dir)` and apply Windows extended-length `\\?\` path normalization internally at the OS boundary (returning `scan_mtimes` keys relative to the caller's `root_dir` without `\\?\`) so directories and files exceeding 259 characters on Windows are created, read, and scanned accurately. In `DownloadManager.diff_trees(root_dir, remote_contents)`, for any `StringContent` in `remote_contents` whose `path` exists in `local_mtimes`, `last_modified == 0`, and rendered text `"".join(content.contents).strip()` is non-empty, `DownloadManager` SHALL compare `self.file_system.read_bytes(content.path)` directly against `rendered.encode("utf-8")` (without hashing) and pass mismatched (or unreadable) paths via `outdated_paths` to `SyncDiff.from_trees`. `DownloadManager.prepare_download_tasks(contents: Sequence[Content]) -> list[DownloadTask]` SHALL accept only `contents` and map each `Content` to a `DownloadTask` via `create_download_task` without performing filesystem queries. `DownloadManager` MUST NOT define `should_download`, accept `root_dir` in `prepare_download_tasks`, or perform direct filesystem I/O (`os`, `stat`, `rglob`, `mkdir`). `DownloadTask` MUST NOT define `last_modified`. Verified at the `DownloadManager` and `FileSystemPort` / `LocalFileSystemAdapter` public seams.

#### Scenario: Scanning local tree modification times
- **WHEN** `DownloadManager.diff_trees(root_dir, remote_contents)` or `LocalFileSystemAdapter.scan_mtimes(root_dir)` runs on a directory containing completed files, `*.part` temporary files, or nested paths exceeding 259 characters on Windows
- **THEN** `DownloadManager.diff_trees` delegates scanning to `file_system.scan_mtimes(Path(root_dir))` without calling `Path.rglob` or `Path.stat` directly, and `LocalFileSystemAdapter.scan_mtimes` returns modification timestamps keyed under `root_dir` (including deep Windows paths) while omitting any file whose name ends with `".part"`

#### Scenario: Determining whether a task needs download
- **WHEN** `DownloadManager.prepare_download_tasks(contents)` evaluates a sequence of `Content` items (such as `diff.pending_contents`)
- **THEN** it maps each `Content` to a `DownloadTask` via `create_download_task` without querying `file_system`, and rejects any `root_dir` argument with `TypeError`

#### Scenario: Enforcing minimal FileSystemPort and DownloadTask surface
- **WHEN** `FileSystemPort`, `LocalFileSystemAdapter`, `DownloadManager`, and `DownloadTask` are inspected
- **THEN** `FileSystemPort` and `LocalFileSystemAdapter` expose only `scan_mtimes`, `ensure_dir`, and `read_bytes` (neither `exists` nor `get_mtime`), `DownloadManager` does not expose `should_download`, and `DownloadTask` exposes `path` and `download` without `last_modified`

### Requirement: Unconditional target file path in RecordingDownloader
`async def download(self, content: RecordingContent) -> None` on `RecordingDownloader` in `src/idat_sync/infrastructure/downloaders.py` SHALL unconditionally treat `content.path` as the target file path by creating `Path(content.path).parent` (using Windows `\\?\` extended-length normalization before `mkdir(parents=True, exist_ok=True)`), setting `yt_dlp` `outtmpl` to `f"{prefix}{os.path.abspath(Path(content.path))}"`, passing a `progress_hook` checked against a per-download `threading.Event` that raises `yt_dlp.utils.DownloadCancelled` when set, offloading `YoutubeDL(ydl_opts).download([content.url])` via `await asyncio.to_thread(...)`, and catching `asyncio.CancelledError` to set the `threading.Event` before re-raising. `RecordingDownloader.download` MUST NOT branch on `Path(content.path).suffix` or append `"%(title)s %(id)s.%(ext)s"`. Verified at the `RecordingDownloader` (`src/idat_sync/infrastructure/downloaders.py`) public seam.

#### Scenario: Downloading a recording with a file extension
- **WHEN** `await RecordingDownloader.download(content)` is called with `RecordingContent(Path("Grabaciones/Semana 1/video.mp4"), 1700000000, url)`
- **THEN** it creates the parent directory `Grabaciones/Semana 1` and passes `f"{prefix}{os.path.abspath(Path('Grabaciones/Semana 1/video.mp4'))}"` as `outtmpl` to `yt_dlp.YoutubeDL` inside `await asyncio.to_thread(...)`

#### Scenario: Downloading a recording without a file extension in path
- **WHEN** `await RecordingDownloader.download(content)` is called with a `RecordingContent` whose `path` has no file suffix (`Path(content.path).suffix == ""`)
- **THEN** it still creates `Path(content.path).parent` (not `Path(content.path)` as a directory) and passes `f"{prefix}{os.path.abspath(Path(content.path))}"` as `outtmpl` without appending `"%(title)s %(id)s.%(ext)s"`

#### Scenario: Aborting worker thread download on asyncio task cancellation
- **WHEN** an active `RecordingDownloader.download(content)` task is cancelled via `asyncio.CancelledError` while `yt_dlp` is downloading in `asyncio.to_thread(...)`
- **THEN** the per-download `threading.Event` is set immediately before `CancelledError` propagates, and invoking the registered `progress_hook` raises `yt_dlp.utils.DownloadCancelled`

### Requirement: Client-owned file download streaming and SharePoint SDK query execution
`MoodleClient` SHALL reuse a single shared `httpx.AsyncClient` across `call_ws` and `download_file` and expose `async def aclose(self) -> None`, `__aenter__`, and `__aexit__` closing the shared client (`MoodleTokenClient` retains single-call `async with httpx.AsyncClient()`). `MoodleClient` and `SharepointClient` SHALL provide `async def download_file(..., dest: BinaryIO) -> None` and `await asyncio.sleep(self.request_delay)`, with `MoodleClient.download_file` merging `wstoken` and `token` into existing query parameters and awaiting `asyncio.sleep(self.request_delay)` after exiting `async with self._client.stream(...)`. `MoodleDownloader.download`, `SharepointDownloader.download`, and `StringDownloader.download` SHALL be `async def` coroutines, all three writing into a sibling `<filename>.part` temporary file using Windows `\\?\` extended-length path normalization at the OS boundary, atomically replacing `content.path` via `os.replace` on completion, and unlinking the `.part` file (`missing_ok=True`) if writing or `download_file` raises (`StringDownloader` writing UTF-8 encoded content). `SharepointClient.__init__` SHALL be non-blocking (`self.search_client: ClientContext | None = None`), MUST NOT set `self.onedrive_cookies` or flatten all hosts into a shared `self.sharepoint_cookies` dictionary, SHALL provide `cookies_for_host(self, host: str) -> dict[str, str]` filtering `FedAuth`, `rtFa`, and `SPOIDCRL` cookies strictly per target host by exact lowercase domain equality or proper leading-dot domain suffix (with exact-host precedence over leading-dot wildcard matches and never substring matching), and SHALL instantiate a single `self._transport = HttpxTransport()` reused across all `get_client(site_url)` calls. `SharepointClient` SHALL expose `async def aclose(self) -> None` awaiting `self._transport.aclose()` and `__aenter__` / `__aexit__` delegating to `aclose()`, and `@classmethod async def create(...) -> Self` SHALL resolve `personal_site` via `await client.personal_client.web.current_user.get_personal_site().execute_query_async()` and `await asyncio.sleep(client.request_delay)`, calling `await client.aclose()` in `except BaseException:` before raising if initialization fails or is cancelled. `get_client(site_url)` SHALL parse the target host from `site_url` and chain `.with_cookies(lambda host=target_host: self.cookies_for_host(host)).with_auto_retry()` and `ctx.pending_request().with_async_transport(self._transport)`. `SharepointClient` MUST NOT define `_run_query` or `_retry_delay`. `SharepointClient` SHALL execute async SDK queries directly (`await ctx.execute_query_with_incremental_retry_async()` in `load_entity`, `await ...execute_query_async()` in `search_for_recordings`, `fetch_folder_files`, and `fetch_subfolders`, `await doc_lib.items.select(["File/Name", "File/ServerRelativeUrl", "File/TimeCreated", "File/TimeLastModified"]).expand(["File"]).get_all_async(page_size=50)` in `fetch_document_library_items`, and `await file.download_session_async(dest, use_path=False)` in `download_file`), and `SharepointApiAdapter.list_url_content` SHALL be `async def`, dispatch by URL path, and skip empty entities where `not entity.name and not entity.server_relative_url`. Verified at `MoodleClient`, `SharepointClient`, `SharepointApiAdapter`, and `*Downloader` seams.

#### Scenario: Streaming a Moodle file through MoodleClient
- **WHEN** `await MoodleClient.download_file(url, dest)` is called with a target URL (including query params) and writable binary stream
- **THEN** it merges `wstoken` and `token` into `url`'s query string on the shared `httpx.AsyncClient.stream("GET", ...)`, raises `CredentialsExpiredError("403 Forbidden: your session cookie is missing/expired.")` on HTTP `401` or `403`, calls `response.raise_for_status()`, writes non-empty chunks from `await response.aiter_bytes(chunk_size=1024 * 256)` into `dest`, closes the stream upon exiting `async with`, and then awaits `asyncio.sleep(self.request_delay)`

#### Scenario: Reusing and closing shared httpx.AsyncClient in MoodleClient
- **WHEN** a `MoodleClient` instance executes multiple `call_ws` and `download_file` operations and then exits `async with client:` or awaits `await client.aclose()`
- **THEN** `httpx.AsyncClient` is instantiated once per `MoodleClient`, reused across all requests, and `aclose()` awaits `self._client.aclose()` while `MoodleTokenClient.request_token` retains its per-call `async with httpx.AsyncClient()`

#### Scenario: Delegating MoodleDownloader and SharepointDownloader file downloads to clients
- **WHEN** `await MoodleDownloader(moodle_client).download(moodle_content)` and `await SharepointDownloader(sharepoint_client).download(sp_content)` succeed vs when `download_file` raises mid-download (including targets > 259 characters on Windows)
- **THEN** each stores `self.moodle_client` or `self.sharepoint_client` without accepting `request_delay`, creates `Path(content.path).parent`, streams into `<filename>.part` (using Windows `\\?\` prefix at the OS boundary), atomically moves the `.part` file to `content.path` via `os.replace` on success, and removes the `.part` file without leaving a partial `content.path` on failure (where `SharepointClient.download_file` awaits `file.download_session_async(dest, use_path=False)` and `asyncio.sleep(self.request_delay)`)

#### Scenario: Writing StringContent atomically via .part files in StringDownloader
- **WHEN** `await StringDownloader().download(string_content)` succeeds vs when writing fails mid-operation
- **THEN** it creates `Path(content.path).parent`, writes UTF-8 encoded content into `Path(content.path).with_name(f"{Path(content.path).name}.part")` via `_atomic_download`, atomically moves the `.part` file onto `content.path` via `os.replace` on success, and unlinks the `.part` file (`missing_ok=True`) without corrupting an existing destination file on failure

#### Scenario: Isolating SharePoint cookies per target host with exact or leading-dot matching
- **WHEN** `SharepointClient` is created with cookies for `"intranet"`, `".sharepoint.com"`, `"evil.sharepoint.com.attacker.test"`, `"contoso.sharepoint.com"`, and `"contoso-my.sharepoint.com"` and `cookies_for_host(...)` or `get_client(site_url)` resolves cookies for each host
- **THEN** `hasattr(client, "sharepoint_cookies")` is `False`, `cookies_for_host("intranet")` rejects `".sharepoint.com"` and `"evil.sharepoint.com.attacker.test"` while returning `intranet` exact matches, `"contoso.sharepoint.com"` never receives `"contoso-my.sharepoint.com"` cookies and rejects `"evil.sharepoint.com.attacker.test"`, exact host cookies take precedence over `".sharepoint.com"` suffix cookies regardless of input order, and `get_client(site_url)` supplies only cookies matching the parsed host of `site_url`

#### Scenario: Reusing and closing HttpxTransport in SharepointClient without onedrive_cookies
- **WHEN** `SharepointClient` is instantiated, `get_client` is called multiple times, and `async with client:` / `await client.aclose()` (or a failing/cancelled `await SharepointClient.create(...)`) runs
- **THEN** `hasattr(client, "onedrive_cookies")` is `False`, `HttpxTransport` is instantiated once per `SharepointClient` and passed to every `with_async_transport` call, and `await client.aclose()` / `__aexit__` (as well as a failing or cancelled `SharepointClient.create`) awaits `self._transport.aclose()`

#### Scenario: Delegating SharePoint guest URLs, direct links, and folder traversal to SharepointClient
- **WHEN** `await SharepointApiAdapter.list_url_content`, `await fetch_folder_contents`, and `await fetch_file_from_direct_link` resolve guest folder URLs (`^/:f:/`), guest file URLs (`^/:(\w+):/`) across `sharepoint_host` and `sharepoint_personal_host`, direct links with `uniqueid`/`sourcedoc`, and folder hierarchies
- **THEN** `SharepointApiAdapter` inspects `urlparse(url).path` without repeating the hostname check, awaits `sp_client.fetch_folder_by_guest_url`, `sp_client.fetch_file_by_guest_url`, and `sp_client.fetch_file_by_id(link, file_id)` (skipping the client call and returning `None` when neither `uniqueid` nor `sourcedoc` is present), and awaits `sp_client.fetch_folder_files(folder)` before `sp_client.fetch_subfolders(folder)`, while `SharepointClient.fetch_document_library_items` selects `["File/Name", "File/ServerRelativeUrl", "File/TimeCreated", "File/TimeLastModified"]` with `.expand(["File"])`, `SharepointClient.get_client` configures host-scoped `.with_cookies(...).with_auto_retry()` and `HttpxTransport()`, `SharepointClient.get_client_from_url` matches `r"/(sites|teams|s|personal)/([^/]+)"` case-insensitively (mapping `/s/<name>` to `{base_url}/sites/<name}` and `/(sites|teams|personal)/<name>` to `{base_url}{path_match.group(0)}`), and `hasattr(SharepointClient, "_run_query")` and `hasattr(SharepointClient, "_retry_delay")` are both `False`

#### Scenario: Skipping dead or expired SharePoint guest and direct links returning odata.null
- **WHEN** `await SharepointClient.load_entity(entity, ctx)` encounters `TypeError` because an `odata.null` guest URL response left `entity.resource_url` as `None`, or `await SharepointApiAdapter.list_url_content` receives a `Folder` or `File` where `not entity.name and not entity.server_relative_url`
- **THEN** `SharepointClient.load_entity` catches `TypeError` and still awaits `asyncio.sleep(self.request_delay)` (while propagating non-`TypeError` exceptions unchanged), and `SharepointApiAdapter.list_url_content` returns `[]`

#### Scenario: Retrying SharePoint HTTP 429 and 503 with Retry-After backoff and re-raising on exhaustion
- **WHEN** `SharepointClient.get_client(site_url)` creates a `ClientContext` and `load_entity` executes a query
- **THEN** `get_client` chains host-scoped `.with_cookies(...).with_auto_retry()` and `ctx.pending_request().with_async_transport(self._transport)`, `load_entity` awaits `ctx.execute_query_with_incremental_retry_async()`, and `SharepointClient` does not define `_run_query` or `_retry_delay` (`office365-rest-python-client` `v3.3.1` handles transient `429`/`503` retries and `Retry-After` headers natively)

### Requirement: UTF-8 and extension-preserving segment sanitization in safe_path_segment
Per ADR-0002, `safe_path_segment(s, replacement=" ", max_len=100)` SHALL preserve UTF-8 accents and short file extensions (`1–15` characters after the last `.` with no whitespace) for `CourseSection`, `CourseModule`, and file names while stripping `<>:"/\|?*`, control characters `\x00-\x1F`/`\x7F`, `.`/`..`, post-truncation `[ .]+$`, and `WINDOWS_RESERVED` stems, and `safe_suffixed_segment(prefix, suffix, *, max_len=100)` SHALL truncate `prefix` directly within `max_len - len(suffix)` without letting `preserve_extension` truncate `suffix` or strip counters. Verified at the `domain.path_utils` seam.

#### Scenario: Preserving UTF-8 and extensions while making nested segments OS-safe
- **WHEN** `safe_path_segment` receives `"Lección 1: Diseño/Guía?.pdf"`, `"CON.txt"`, `".."`, or `"a" * 110 + ".mp4"` with `max_len=100`
- **THEN** it returns `"Lección 1 Diseño Guía.pdf"`, `"_CON.txt"`, `"untitled"`, and `"a" * 96 + ".mp4"` (length `<= 100`, UTF-8 and extension intact, no forbidden characters or trailing dots/spaces)

#### Scenario: Ignoring fake long or spaced extensions and preserving bounded suffixes
- **WHEN** `safe_path_segment` receives `"Publishing, M. (2007. " + "x" * 100` (`max_len=100`), or `safe_suffixed_segment("1", " (2)." + "x" * 98, max_len=100)` is called
- **THEN** `safe_path_segment` truncates to the 100-character prefix without chopping `"Publishing, M"` down to preserve a multi-word or `> 15`-character tail, and `safe_suffixed_segment` returns a segment of length `<= 100` without dropping `" (2)"`

### Requirement: Per-course failure isolation and early credential abort in IDATSync
`IDATSync` in `src/idat_sync/application/sync_service.py` MUST NOT accept `publish` in `__init__` or `progress_listener` in `sync_course` or `sync_courses`. `list_courses() -> list[Course]`, `get_course_remote_tree(course, course_dir) -> list[Content]`, and `check_course_sync(course, course_dir) -> SyncDiff` SHALL be `async def` coroutines. `get_course_remote_tree(course, course_dir)` SHALL merge `course_repository.get_course_content(course, course_path)` and `course_repository.get_recordings(course.name, recordings_folder)` and disambiguate any duplicate leaf paths compared case-insensitively (`path.as_posix().casefold()`): the first occurrence SHALL remain unchanged, and each subsequent colliding item SHALL be renamed in its final segment to `<stem> (n)<ext>` (starting at `n = 2` and incrementing until unused case-insensitively, treating `item.path.suffix` as `<ext>` only when `1 <= len(ext) <= 16` including `.` with no whitespace and treating the full filename as `<stem>` with `ext=""` otherwise, bounding the filename to `<= 100` characters without looping infinitely). `sync_course(course: Course, course_dir: str | Path) -> AsyncIterator[SyncEvent]` and `sync_courses(root_folder: str | Path, courses: Sequence[Course]) -> AsyncIterator[SyncEvent]` SHALL be async generators that call `log_sync_event(event)` before yielding each `SyncEvent`. `sync_course` SHALL drive `task_orchestrator.create_coroutines(tasks, run_task)` concurrently across downloader groups, continuing remaining tasks after non-credential task errors and cancelling sibling group tasks immediately on `CredentialsExpiredError`. `sync_courses` SHALL call `self.course_repository.invalidate_cache()` at the start of each sync pass before iterating `courses`, compute each `Course` folder via `Path(root_folder) / safe_filename(course.name)`, lazily await `populate_course_sections(course)` when `course.sections` is empty, emit `CourseSyncCompleted(course, exc)` and skip single-`Course` failures other than `CredentialsExpiredError` so later courses still sync, and abort remaining courses immediately on `CredentialsExpiredError`. Verified at the `IDATSync` (`src/idat_sync/application/sync_service.py`) seam.

#### Scenario: Disambiguating case-insensitive and truncated leaf path collisions in get_course_remote_tree
- **WHEN** `await IDATSync.get_course_remote_tree(course, course_dir)` receives merged course and recording items containing three case-insensitively colliding paths (`"Course/Sec/Mod-[1]/Parte 1/guia.pdf"`, `"Course/Sec/Mod-[1]/parte 1/guia.pdf"`, `"Course/Sec/Mod-[1]/Parte 1/guia.pdf"`) or a second file whose 100-character name collides with an earlier file
- **THEN** the first item keeps its exact `path`, the second becomes `.../parte 1/guia (2).pdf`, the third becomes `.../Parte 1/guia (3).pdf`, every leaf segment has length `<= 100`, and passing the returned list into `SyncDiff.from_trees` succeeds without raising `ValueError`

#### Scenario: Disambiguating long early-dot leaf path collisions without looping
- **WHEN** `await IDATSync.get_course_remote_tree(course, course_dir)` receives two 100-character items named `"1." + "x" * 98` inside the same folder
- **THEN** the first item keeps its exact `path`, the second terminates immediately as a distinct path ending in `" (2)"` of length `<= 100`, and `SyncDiff.from_trees` succeeds without raising `ValueError`

#### Scenario: Invalidating repository cache at the start of sync_courses
- **WHEN** `[e async for e in IDATSync.sync_courses(root_folder, [course1, course2])]` starts executing
- **THEN** `course_repository.invalidate_cache()` is called once before processing `course1` and `course2`

#### Scenario: Continuing remaining courses when one course fails
- **WHEN** `sync_course` raises a non-credential error on the first `Course` while a second `Course` in `courses` succeeds
- **THEN** `[e async for e in IDATSync.sync_courses(root_folder, [course1, course2])]` yields `CourseSyncCompleted(course1, exc)`, still synchronizes the second `Course` and yields `CourseSyncCompleted(course2, None)`, and finishes without raising

#### Scenario: Aborting remaining courses on CredentialsExpiredError
- **WHEN** a `DownloadTask` or course step raises `CredentialsExpiredError` inside `IDATSync.sync_course` or `IDATSync.sync_courses(root_folder, [course1, course2])`
- **THEN** `TaskDownloadFailed(task.path, exc)` (if raised by a task) and `CourseSyncCompleted(course1, exc)` are logged and yielded, remaining unstarted tasks across all downloader groups and subsequent courses in `courses` MUST NOT run, and `CredentialsExpiredError` propagates immediately to the caller

### Requirement: Developer CLI extraction scripts and gitignored local test artifacts
`scripts/login_cli.py` and `scripts/moodle_json.py` SHALL reside under `scripts/` (without root copies), and `.gitignore` SHALL list `all_courses_contents.json` and `session*.json`. `scripts/login_cli.py` SHALL save `Credentials` to `config.session_file`, and `scripts/moodle_json.py` SHALL define `async def main() -> int` invoked via `asyncio.run(main())`, loading `Credentials` from `JsonCredentialStore(config.session_file)` when present or prompting for `Username:` and `Password:` and awaiting `MoodleTokenAdapter(MoodleTokenClient(config.moodle_base_url)).fetch_token(username, password)` (catching `AuthenticationError` and exiting with status `1` when rejected) before running `async with MoodleClient(...) as client:` to await `client.fetch_enrolled_courses()`, `client.fetch_assignments()`, and `client.fetch_course_contents(int(course["id"]))` and write `Path("./all_courses_contents.json")`. Verified at the `scripts/login_cli.py`, `scripts/moodle_json.py`, and `.gitignore` seams.

#### Scenario: Extracting courses, contents, and assignments to all_courses_contents.json
- **WHEN** `scripts/moodle_json.py` runs (reusing `Credentials` from `JsonCredentialStore(config.session_file)` when present, or prompting for `Username:` and `Password:` and awaiting `MoodleTokenAdapter.fetch_token(username, password)` when absent)
- **THEN** it obtains a token (or catches `AuthenticationError` and returns exit code `1` on rejected credentials), enters `async with MoodleClient(...) as client:` (closing `client` on exit), awaits `fetch_enrolled_courses()`, `fetch_assignments()` (indexed by `int(course["id"])`), and `fetch_course_contents(int(course["id"]))`, and writes UTF-8 JSON (`indent=2, ensure_ascii=False`) with exact keys `{"course": course, "contents": ..., "assignments": ...}` per course directly to `Path("./all_courses_contents.json")` without dumping JSON to stdout

#### Scenario: Keeping extracted curriculum and Credentials files gitignored
- **WHEN** `.gitignore` and the repository layout are inspected
- **THEN** `.gitignore` contains `all_courses_contents.json` and `session*.json`, `scripts/login_cli.py` and `scripts/moodle_json.py` exist using `Credentials` terminology, and root `login_cli.py` and `moodle_json.py` do not exist

### Requirement: Treeview course selection UI and ProgressInterceptor removal
`src/idat_sync/ui/app.py` MUST NOT define `ProgressInterceptor` and MUST NOT reassign `sys.stdout` or `sys.stderr`, and `ui.app.main()` SHALL invoke `configure_logging(config.log_file)`. `SyncFrame` in `src/idat_sync/ui/sync_frame.py` MUST NOT define `output_text`, `_TreeProgressListener`, `progress_queue`, `update_progress`, `exit`, or `_queue_error`, and MUST NOT call `loop.call_soon_threadsafe` or `asyncio.to_thread`. `SyncFrame` SHALL await `await create_sync_service(self.credentials, self.config)`, `await self.idat_sync.list_courses()`, and `async for event in self.idat_sync.sync_courses(path, selected): self._handle_event(event)` directly on the UI event loop while indexing `self._file_iids` by `Path -> (course_id, child_iid)`, clearing `course.sections.clear()` on each checked `Course` in `do_sync()` before iterating `sync_courses(path, selected)` so repeat `Sync` runs refresh Moodle sections without recreating the active `IDATSync` service, and setting `status_var` to `"Sync completed with errors."` when any yielded `CourseSyncCompleted` has `error is not None` or `"Sync done."` when all checked courses succeed. `SyncFrame` SHALL render a `ttk.Treeview` (`#0` `Name`, `status`, `progress`) with checkbox, folder, and file icons, selection buttons (`In Progress`, `Select All`, `Clear`), and `status_var`, ordering in-progress courses first (checked by default) on startup and updating parent, nested folder, and leaf file rows (`0%`-`100%`) during sync. Verified at `MainApp`, `ui.app.main`, and `SyncFrame` (`src/idat_sync/ui/sync_frame.py`) seams.

#### Scenario: Ordering in-progress courses first and pre-selecting them on startup
- **WHEN** `await SyncFrame.load_idat_sync(credentials)` loads courses from `await idat_sync.list_courses()` containing past and in-progress courses
- **THEN** `SyncFrame` inserts in-progress courses first in the `ttk.Treeview` with a checked icon and past courses after with an unchecked icon, and enables `sync_button` only when `not is_syncing`, `folder_path` is non-empty, and at least one course is checked (or when courses have not loaded yet after a startup error)

#### Scenario: Updating parent course and child file rows during sync
- **WHEN** `await SyncFrame.do_sync()` runs on checked courses
- **THEN** `hasattr(SyncFrame, "update_progress")` and `hasattr(SyncFrame, "exit")` are `False`, only checked courses are reset to `Status="Queued"`, `Progress="0%"` and passed to `async for event in self.idat_sync.sync_courses(path, selected):`, nested folder nodes use a folder icon and leaf file nodes use a file icon with `Synced` (`100%`) or `Pending` (`0%`) -> `Downloading` (`0%`) -> `Completed` (`100%`) / `Failed` (`0%`), and parent course progress updates as `f"{int(done * 100 / total)}%"` (`"100%"` when `total == 0`)

#### Scenario: Reporting completion error status in SyncFrame
- **WHEN** `await SyncFrame.do_sync()` finishes iterating `sync_courses` after at least one `CourseSyncCompleted` had `error is not None` vs when all had `error is None`
- **THEN** `status_var` is set to `"Sync completed with errors."` when any course had an error and `"Sync done."` when all succeeded

#### Scenario: Clearing selected course sections on repeat Sync without recreating IDATSync
- **WHEN** `await SyncFrame.do_sync()` runs a second time on checked courses with already-populated `sections` while unchecked courses retain cached `sections`
- **THEN** checked `Course.sections` are cleared before `self.idat_sync.sync_courses(path, selected)` runs, unchecked `Course.sections` remain untouched, and `create_sync_service` is not invoked again while `self.idat_sync` is already active
