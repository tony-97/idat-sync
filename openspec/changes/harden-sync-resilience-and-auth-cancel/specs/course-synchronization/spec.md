# course-synchronization Specification Delta

## MODIFIED Requirements

### Requirement: Moodle adapter translates vendor curriculum into domain models
`ModuleContent` in `src/idat_sync/domain/course.py` SHALL define `filepath: str = "/"` alongside `filename`, `fileurl`, `timemodified`, and `filesize`. `MoodleAdapter` SHALL map Moodle sections, modules, and assignments into `Course`, `CourseSection`, and `CourseModule` with `inline_contents: list[StringContent]` and `contents: list[ModuleContent]`, populating `ModuleContent.filepath` from `raw_content.get("filepath") or "/"` for both module `contents` and assignment `introattachments` while sanitizing generated `.html` filenames and resolved `MoodleContent` filenames with `safe_path_segment` and populating `assign` intro `StringContent.last_modified` from `int(assign.get("timemodified") or 0)`. For non-`Bibliografía` sections, `MoodleAdapter` SHALL consolidate all `modname == "label"` modules with non-empty `description` into a single section-level `StringContent` named `info.html` rather than per-label `CourseModule` items; for sections whose normalized name is `"bibliografia"`, `MoodleAdapter` SHALL collect all non-empty `label` descriptions and `url` entries (`name` and `fileurl`) in module order into a single section-level `StringContent` named `bibliografia.html` without emitting `info.html` or `ModuleContent` items for those URLs. `domain/course.py` MUST NOT define `Assignment`, `Course.assignments`, `CourseSection.summary`, `CourseModule.modname`, or `CourseModule.description`. Verified at the `MoodleAdapter` public seam.

#### Scenario: Generating section summary inline content
- **WHEN** `MoodleAdapter` converts a raw section with `id`, `name`, and `summary` (and no non-empty labels or `Bibliografía` entries)
- **THEN** the resulting `CourseSection` has `inline_contents` set to `[StringContent(Path(safe_path_segment(f"{section_name}-[{section_id}]-resumen.html")), 0, [summary])]` (and when labels or `Bibliografía` entries are also present, `resumen.html` precedes `info.html` or `bibliografia.html`)

#### Scenario: Mapping label modules with non-empty and empty descriptions
- **WHEN** `MoodleAdapter` converts a non-`Bibliografía` section containing `modname == "label"` modules (with non-empty or empty `description`)
- **THEN** all non-empty `label` `description` strings are appended in module order into a single `StringContent(Path("info.html"), 0, ...)` on `CourseSection.inline_contents`, empty-description labels are ignored, no per-label `<module_name>.html` `CourseModule` items are emitted, and no `info.html` is emitted when a section has no non-empty `label` descriptions

#### Scenario: Dumping Bibliografía labels and URLs into bibliografia.html
- **WHEN** `MoodleAdapter` converts a section whose trimmed, lowercase, accent-stripped name equals `"bibliografia"` containing `modname == "label"` modules and `modname == "url"` modules (each with `name` and `contents[0]["fileurl"]`)
- **THEN** non-empty `label` `description` HTML and HTML-escaped `url` entries (`name` + `fileurl`) are collected in module order into a single `StringContent(Path("bibliografia.html"), 0, ...)` on `CourseSection.inline_contents`, no `info.html` is emitted, no `ModuleContent` items are emitted for those `url` modules (so `UrlContentStrategy` is never invoked for them), and any non-`label`/non-`url` modules in the section still map into `CourseSection.modules`

#### Scenario: Merging assignment intro and attachments by cmid
- **WHEN** `MoodleAdapter` converts a raw module with `modname == "assign"` and `id` matching an assignment's `cmid` (with `timemodified` present or omitted)
- **THEN** `CourseModule.inline_contents` is `[StringContent(Path(safe_path_segment(f"{assign_name}-[{assign_id}]-intro.html")), int(assign.get("timemodified") or 0), [assign_intro])]` and `CourseModule.contents` contains the mapped `ModuleContent` items from `introattachments` (or both are `[]` when no matching assignment exists)

#### Scenario: Preserving ModuleContent filepath from Moodle payloads
- **WHEN** `MoodleAdapter` maps a module `contents` item or `assign` `introattachments` item with `filepath: "/Parte 1/"` alongside an item omitting `filepath` or setting `filepath: ""`
- **THEN** the first `ModuleContent` has `filepath == "/Parte 1/"` and the second defaults to `filepath == "/"`

#### Scenario: Sanitizing unsafe resource and inline filenames while preserving accents
- **WHEN** `MoodleAdapter.list_url_content` or inline content mapping receives resource `"Guía: Tema 1/2?.pdf"`, `"CON.txt"`, or assignment `"Aviso: CON/1"`
- **THEN** the resulting `MoodleContent.path` values are `Path("Guía Tema 1 2.pdf")` and `Path("_CON.txt")`, and the assignment `StringContent.path` is sanitized with `safe_path_segment` while preserving the `-[<assign_id>]-intro.html` suffix

### Requirement: CourseRepository orchestrates gateways and URL strategies
`CourseRepository` in `src/idat_sync/application/course_repository.py` SHALL accept `(course_gateway: CourseGateway, recording_gateway: RecordingGateway, url_strategies: Sequence[UrlContentStrategy])` and expose `async def list_courses(self) -> list[Course]`, `async def populate_course_sections(self, course: Course) -> Course`, `def invalidate_cache(self) -> None` (delegating to `self.course_gateway.invalidate_cache()`), `async def get_course_content(self, course: Course, course_dir: Path | str = Path()) -> list[Content]`, and `async def get_recordings(self, course_name: str, recordings_folder: str) -> list[Content]`. `CourseRepository.list_url_content` SHALL preserve non-root `ModuleContent.filepath` subfolders without modifying the `UrlContentStrategy.list_url_content(url, filename, last_modified)` signature by splitting `content.filepath` on `"/"`, raising `ValueError` on any `".."` traversal segment, ignoring empty and `"."` segments, sanitizing each remaining subfolder segment via `safe_path_segment`, and applying those relative subfolders via `Content.in_folder(...)`. When `await strategy.list_url_content(...)` raises `CredentialsExpiredError`, `list_url_content` SHALL re-raise `CredentialsExpiredError` immediately; when `await strategy.list_url_content(...)` raises any other `Exception`, `list_url_content` SHALL log a warning and skip that `ModuleContent` item so remaining items in the course continue resolving. `get_course_content(course, course_dir)` SHALL skip sections and modules with empty `name`, place each module's `inline_contents` before resolved `contents` inside a module folder formatted with `safe_path_segment` to `<bounded_name>-[<module.id>]` (`<= 100` characters total, truncating the sanitized `module.name` prefix first so `-[<module.id>]` is never truncated), prepend `section.inline_contents` before module items in `safe_path_segment(section.name)` (keeping `Course` and `CourseSection` directory names without IDs), and wrap items in `course_dir`. Verified at the `CourseRepository` (`src/idat_sync/application/course_repository.py`) public seam.

#### Scenario: Composing course content hierarchy and ordering
- **WHEN** `await CourseRepository.get_course_content(course, course_dir)` is called on a `Course` with sections and modules (including unsafe characters such as `:` or `/` in `section.name` or `module.name`, plus `CourseModule(id=201, name="Carpeta", ...)` containing `"guia.pdf"` with `filepath="/Parte 1/"` and `filepath="/Parte 2/"` and root items with `filepath="/"`)
- **THEN** it awaits `strategy.list_url_content(url, filename, timemodified)` with the unchanged 3-argument signature for each matching URL strategy and emits each section's `inline_contents` followed by each named module's `inline_contents` and URL-resolved `contents`, composed bottom-up via subfolder `.in_folder(...)` segments (`Parte 1/guia.pdf`, `Parte 2/guia.pdf`), `.in_folder("<safe_bounded_module_name>-[<module.id>]")`, `.in_folder(safe_path_segment(section.name))`, and `.in_folder(course_dir)`

#### Scenario: Preserving module ID suffix when module name exceeds segment budget
- **WHEN** `await CourseRepository.get_course_content(course, course_dir)` processes two distinct `CourseModule` items (`id=1001`, `id=1002`) whose names exceed 100 characters and share an identical 95-character prefix
- **THEN** each module directory segment is truncated before appending its suffix so both segments have length `<= 100`, end with `-[1001]` and `-[1002]`, and remain distinct paths

#### Scenario: Rejecting traversal segments and sanitizing unsafe names in ModuleContent filepath
- **WHEN** `await CourseRepository.list_url_content(contents)` receives `filepath="/Tema: 1/./CON/"` vs `filepath="/../outside/"`
- **THEN** `"/Tema: 1/./CON/"` resolves under `Path("Tema 1/_CON/<filename>")` and `"/../outside/"` raises `ValueError`

#### Scenario: Skipping broken URL links without aborting remaining items while propagating CredentialsExpiredError
- **WHEN** `await CourseRepository.list_url_content([broken, valid])` encounters a non-credential `Exception` (such as a 404 client error) on `broken` vs `CredentialsExpiredError`
- **THEN** on a non-credential `Exception` it logs a warning, skips `broken`, and still resolves and returns `valid`, whereas `CredentialsExpiredError` propagates immediately

#### Scenario: Wrapping recordings in target folder
- **WHEN** `await CourseRepository.get_recordings(course_name, recordings_folder)` is called
- **THEN** it awaits `recording_gateway.fetch_recordings(course_name)` and returns `[r.in_folder(recordings_folder) for r in recordings]`

#### Scenario: Delegating cache invalidation from CourseRepository to CourseGateway
- **WHEN** `CourseRepository.invalidate_cache()` is called
- **THEN** it invokes `self.course_gateway.invalidate_cache()` once

### Requirement: Pure domain SyncDiff calculation and FileSystemPort orchestration
`SyncDiff.from_trees(remote_contents, local_mtimes, *, outdated_paths=())` in `src/idat_sync/domain/sync.py` SHALL reject duplicate exact `Content.path` keys across `remote_contents` by raising `ValueError` naming the colliding path instead of silently overwriting entries, and otherwise SHALL compute `missing_paths`, `outdated_paths` (paths in `common_paths` where `remote.last_modified > 0 and remote.last_modified > local_mtime`, OR present in the optional `outdated_paths` argument), `synced_paths`, `local_only_paths`, and `entries` sorted by `e.path` without disk I/O. Calling `SyncDiff.from_trees(remote_contents, local_mtimes)` with two positional arguments SHALL retain `outdated_paths=()` as its default. `DownloadManager` and `IDATSync` SHALL perform all local disk reads and directory creation via `FileSystemPort` (`LocalFileSystemAdapter`). Verified at `SyncDiff.from_trees` and `DownloadManager` seams.

#### Scenario: Computing SyncDiff purely in memory
- **WHEN** `SyncDiff.from_trees(remote_contents, local_mtimes)` is called with unique in-memory `Content` items and a `Mapping[Path, int]`, with or without `outdated_paths`
- **THEN** it partitions paths into `missing_paths`, `outdated_paths` (including any `common_paths` present in `outdated_paths`), `synced_paths`, and `local_only_paths` and sorts `entries` by `e.path` without touching the filesystem

#### Scenario: Failing fast on duplicate remote Content paths in SyncDiff.from_trees
- **WHEN** `SyncDiff.from_trees(remote_contents, local_mtimes)` receives two `Content` items sharing the exact same `path`
- **THEN** it raises `ValueError` containing the duplicate path string rather than dropping the first item

#### Scenario: Orchestrating sync pipeline through FileSystemPort and Downloader groups
- **WHEN** `DownloadManager.diff_trees(root_dir, remote_contents)` and `IDATSync.sync_course(course, course_dir)` run
- **THEN** directory creation and file timestamp/byte reads are delegated to `FileSystemPort` and pending tasks execute through `TaskOrchestrator`

### Requirement: Mandatory FileSystemPort delegation in DownloadManager
`DownloadManager` SHALL require `FileSystemPort` as a non-nullable constructor dependency and delegate all local directory tree modification timestamp scans and file reads to `FileSystemPort`. `FileSystemPort` and `LocalFileSystemAdapter` SHALL expose `scan_mtimes(root_dir)`, `ensure_dir(path)`, and `read_bytes(path) -> bytes | None` (returning `None` on `OSError`, never defining `exists` or `get_mtime`). `LocalFileSystemAdapter.scan_mtimes(root_dir)` in `src/idat_sync/infrastructure/filesystem.py` SHALL exclude temporary `*.part` files (`not p.name.endswith(".part")`). In `DownloadManager.diff_trees(root_dir, remote_contents)`, for any `StringContent` in `remote_contents` whose `path` exists in `local_mtimes`, `last_modified == 0`, and rendered text `"".join(content.contents).strip()` is non-empty, `DownloadManager` SHALL compare `self.file_system.read_bytes(content.path)` directly against `rendered.encode("utf-8")` (without hashing) and pass mismatched (or unreadable) paths via `outdated_paths` to `SyncDiff.from_trees`. `DownloadManager.prepare_download_tasks(contents: Sequence[Content]) -> list[DownloadTask]` SHALL accept only `contents` and map each `Content` to a `DownloadTask` via `create_download_task` without performing filesystem queries. `DownloadManager` MUST NOT define `should_download`, accept `root_dir` in `prepare_download_tasks`, or perform direct filesystem I/O (`os`, `stat`, `rglob`, `mkdir`). `DownloadTask` MUST NOT define `last_modified`. Verified at the `DownloadManager` and `FileSystemPort` / `LocalFileSystemAdapter` public seams.

#### Scenario: Scanning local tree modification times
- **WHEN** `DownloadManager.diff_trees(root_dir, remote_contents)` or `LocalFileSystemAdapter.scan_mtimes(root_dir)` runs on a directory containing both completed files and `*.part` temporary files
- **THEN** `DownloadManager.diff_trees` delegates scanning to `file_system.scan_mtimes(Path(root_dir))` without calling `Path.rglob` or `Path.stat` directly, and `LocalFileSystemAdapter.scan_mtimes` returns modification timestamps for regular files while omitting any file whose name ends with `".part"`

#### Scenario: Detecting edited StringContent by byte comparison
- **WHEN** `DownloadManager.diff_trees(root_dir, remote_contents)` runs on a directory containing an unchanged `StringContent(last_modified=0)` HTML file and a modified `StringContent(last_modified=0)` HTML file
- **THEN** `DownloadManager.diff_trees` delegates `read_bytes` to `file_system` without calling `Path.read_bytes` directly, keeps the unchanged `StringContent` path in `diff.synced_paths`, and places the changed `StringContent` path in `diff.outdated_paths`

#### Scenario: Determining whether a task needs download
- **WHEN** `DownloadManager.prepare_download_tasks(contents)` evaluates a sequence of `Content` items (such as `diff.pending_contents`)
- **THEN** it maps each `Content` to a `DownloadTask` via `create_download_task` without querying `file_system`, and rejects any `root_dir` argument with `TypeError`

#### Scenario: Enforcing minimal FileSystemPort and DownloadTask surface
- **WHEN** `FileSystemPort`, `LocalFileSystemAdapter`, `DownloadManager`, and `DownloadTask` are inspected
- **THEN** `FileSystemPort` and `LocalFileSystemAdapter` expose only `scan_mtimes`, `ensure_dir`, and `read_bytes` (neither `exists` nor `get_mtime`), `DownloadManager` does not expose `should_download`, and `DownloadTask` exposes `path` and `download` without `last_modified`

### Requirement: Client-owned file download streaming and SharePoint SDK query execution
`MoodleClient` SHALL reuse a single shared `httpx.AsyncClient` across `call_ws` and `download_file` and expose `async def aclose(self) -> None`, `__aenter__`, and `__aexit__` closing the shared client (`MoodleTokenClient` retains single-call `async with httpx.AsyncClient()`). `MoodleClient` and `SharepointClient` SHALL provide `async def download_file(..., dest: BinaryIO) -> None` and `await asyncio.sleep(self.request_delay)`. `MoodleClient.download_file` SHALL merge `wstoken` and `token` into any existing query parameters on `url` rather than replacing them. `MoodleDownloader.download`, `SharepointDownloader.download`, and `StringDownloader.download` SHALL be `async def` coroutines, all three writing into a sibling `<filename>.part` temporary file, atomically replacing `content.path` via `os.replace` on completion, and unlinking the `.part` file (`missing_ok=True`) if writing or `download_file` raises (`StringDownloader` writing UTF-8 encoded content). `SharepointClient.__init__` SHALL be non-blocking (`self.search_client: ClientContext | None = None`), MUST NOT set `self.onedrive_cookies` or flatten all hosts into a shared `self.sharepoint_cookies` dictionary, SHALL provide `cookies_for_host(self, host: str) -> dict[str, str]` filtering `FedAuth`, `rtFa`, and `SPOIDCRL` cookies strictly per target host by exact lowercase domain equality or proper leading-dot domain suffix (with exact-host precedence over leading-dot wildcard matches and never substring matching), and SHALL instantiate a single `self._transport = HttpxTransport()` reused across all `get_client(site_url)` calls. `SharepointClient` SHALL expose `async def aclose(self) -> None` awaiting `self._transport.aclose()` and `__aenter__` / `__aexit__` delegating to `aclose()`, and `@classmethod async def create(...) -> Self` SHALL resolve `personal_site` via `await client.personal_client.web.current_user.get_personal_site().execute_query_async()` and `await asyncio.sleep(client.request_delay)`, calling `await client.aclose()` before raising if initialization fails. `get_client(site_url)` SHALL parse the target host from `site_url` and chain `.with_cookies(lambda host=target_host: self.cookies_for_host(host)).with_auto_retry()` and `ctx.pending_request().with_async_transport(self._transport)`. `SharepointClient` MUST NOT define `_run_query` or `_retry_delay`. `SharepointClient` SHALL execute async SDK queries directly (`await ctx.execute_query_with_incremental_retry_async()` in `load_entity`, `await ...execute_query_async()` in `search_for_recordings`, `fetch_folder_files`, and `fetch_subfolders`, `await ...get_all_async(page_size=50)` in `fetch_document_library_items`, and `await file.download_session_async(dest, use_path=False)` in `download_file`), and `SharepointApiAdapter.list_url_content` SHALL be `async def`, dispatch by URL path, and skip empty entities where `not entity.name and not entity.server_relative_url`. Verified at `MoodleClient`, `SharepointClient`, `SharepointApiAdapter`, and `*Downloader` seams.

#### Scenario: Streaming a Moodle file through MoodleClient
- **WHEN** `await MoodleClient.download_file(url, dest)` is called with a target URL (including existing query parameters such as `?forcedownload=1` or `?id=42`) and a writable binary stream
- **THEN** it merges `wstoken=self.token` and `token=self.token` into `url`'s existing query string (preserving `forcedownload=1` / `id=42`), opens `self._client.stream("GET", merged_url, follow_redirects=True, timeout=60, headers=REQUEST_HEADER)`, raises `CredentialsExpiredError("403 Forbidden: your session cookie is missing/expired.")` on HTTP `401` or `403`, calls `response.raise_for_status()`, writes non-empty chunks from `await response.aiter_bytes(chunk_size=1024 * 256)` into `dest`, and awaits `asyncio.sleep(self.request_delay)`

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

### Requirement: Developer CLI extraction scripts and gitignored local test artifacts
`scripts/login_cli.py` and `scripts/moodle_json.py` SHALL reside under `scripts/` (without root copies), and `.gitignore` SHALL list `all_courses_contents.json` and `session*.json`. `scripts/login_cli.py` SHALL save `Credentials` to `config.session_file`, and `scripts/moodle_json.py` SHALL define `async def main() -> int` invoked via `asyncio.run(main())`, loading `Credentials` from `JsonCredentialStore(config.session_file)` when present or prompting for `Username:` and `Password:` and awaiting `MoodleTokenAdapter(MoodleTokenClient(config.moodle_base_url)).fetch_token(username, password)` (catching `AuthenticationError` and exiting with status `1` when rejected) before awaiting `client.fetch_enrolled_courses()`, `client.fetch_assignments()`, and `client.fetch_course_contents(int(course["id"]))` and writing `Path("./all_courses_contents.json")`. Verified at the `scripts/login_cli.py`, `scripts/moodle_json.py`, and `.gitignore` seams.

#### Scenario: Extracting courses, contents, and assignments to all_courses_contents.json
- **WHEN** `scripts/moodle_json.py` runs (reusing `Credentials` from `JsonCredentialStore(config.session_file)` when present, or prompting for `Username:` and `Password:` and awaiting `MoodleTokenAdapter.fetch_token(username, password)` when absent)
- **THEN** it obtains a token (or catches `AuthenticationError` and returns exit code `1` on rejected credentials), awaits `fetch_enrolled_courses()`, `fetch_assignments()` (indexed by `int(course["id"])`), and `fetch_course_contents(int(course["id"]))` on `MoodleClient`, and writes UTF-8 JSON (`indent=2, ensure_ascii=False`) with exact keys `{"course": course, "contents": ..., "assignments": ...}` per course directly to `Path("./all_courses_contents.json")` without dumping JSON to stdout

#### Scenario: Keeping extracted curriculum and Credentials files gitignored
- **WHEN** `.gitignore` and the repository layout are inspected
- **THEN** `.gitignore` contains `all_courses_contents.json` and `session*.json`, `scripts/login_cli.py` and `scripts/moodle_json.py` exist using `Credentials` terminology, and root `login_cli.py` and `moodle_json.py` do not exist
