# course-synchronization Specification

## Purpose

Synchronizes remote `Course` structures, inline HTML artifacts, external URLs, and class recordings into a local directory tree through inward-pointing hexagonal ports and adapters.

## Requirements

### Requirement: Strict inward package dependency boundary
The `src/idat_sync/domain/` and `src/idat_sync/application/` packages SHALL have zero imports from `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `office365`, `yt_dlp`, or `playwright`. `src/idat_sync/ui/` MUST NOT import `idat_sync.infrastructure`, `src/idat_sync/infrastructure/sharepoint_auth.py` SHALL be the sole module importing `playwright`, `src/idat_sync/infrastructure/auth.py` MUST NOT exist, `src/idat_sync/utils.py` MUST NOT exist, and `safe_filename` and `WINDOWS_RESERVED` SHALL reside in `src/idat_sync/domain/path_utils.py`. Verified at the architecture boundary and `domain.path_utils` seams.

#### Scenario: Inspecting domain and application imports
- **WHEN** all Python modules under `src/idat_sync/domain/` and `src/idat_sync/application/` are parsed for import statements
- **THEN** none of them import `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `office365`, `yt_dlp`, or `playwright`

#### Scenario: Sanitizing course filenames without importing infrastructure
- **WHEN** `safe_filename(s, replacement=" ", max_len=100)` in `src/idat_sync/domain/path_utils.py` is called with accents, forbidden characters, or Windows reserved names (`WINDOWS_RESERVED`)
- **THEN** it returns the normalized, safe ASCII filename of at most `max_len` characters

#### Scenario: Enforcing ui and playwright isolation and legacy auth/utils removal
- **WHEN** all Python modules under `src/idat_sync/` are parsed for imports and `src/idat_sync/utils.py` and `src/idat_sync/infrastructure/auth.py` are checked
- **THEN** `src/idat_sync/utils.py` and `src/idat_sync/infrastructure/auth.py` do not exist, no module imports `idat_sync.utils` or `idat_sync.infrastructure.auth`, no module under `src/idat_sync/ui/` imports `idat_sync.infrastructure`, and `src/idat_sync/infrastructure/sharepoint_auth.py` is the only module importing `playwright`

### Requirement: Moodle adapter translates vendor curriculum into domain models
`MoodleAdapter` SHALL map Moodle sections, modules, and assignments into `Course`, `CourseSection`, and `CourseModule` with `inline_contents: list[StringContent]` and `contents: list[ModuleContent]`, sanitizing generated `.html` filenames and resolved `MoodleContent` filenames with `safe_path_segment`. `domain/course.py` MUST NOT define `Assignment`, `Course.assignments`, `CourseSection.summary`, `CourseModule.modname`, or `CourseModule.description`. Verified at the `MoodleAdapter` public seam.

#### Scenario: Generating section summary inline content
- **WHEN** `MoodleAdapter` converts a raw section with `id` and `name` and `summary`
- **THEN** the resulting `CourseSection` has `inline_contents` set to `[StringContent(Path(safe_path_segment(f"{section_name}-[{section_id}]-resumen.html")), 0, [summary])]`

#### Scenario: Mapping label modules with non-empty and empty descriptions
- **WHEN** `MoodleAdapter` converts a raw module with `modname == "label"`
- **THEN** `CourseModule.inline_contents` is `[StringContent(Path(safe_path_segment(f"{module_name}.html")), 0, [description])]` with `contents = []` when `description` is non-empty, and both `inline_contents` and `contents` are `[]` when `description` is empty

#### Scenario: Merging assignment intro and attachments by cmid
- **WHEN** `MoodleAdapter` converts a raw module with `modname == "assign"` and `id` matching an assignment's `cmid`
- **THEN** `CourseModule.inline_contents` is `[StringContent(Path(safe_path_segment(f"{assign_name}-[{assign_id}]-intro.html")), 0, [assign_intro])]` and `CourseModule.contents` contains the mapped `ModuleContent` items from `introattachments` (or both are `[]` when no matching assignment exists)

#### Scenario: Sanitizing unsafe resource and inline filenames while preserving accents
- **WHEN** `MoodleAdapter.list_url_content` or inline content mapping receives `"Guía: Tema 1/2?.pdf"` or label `"Aviso: CON/1"`
- **THEN** the resulting `MoodleContent.path` is `Path("Guía Tema 1 2.pdf")` and the label `StringContent.path` is `Path("Aviso CON 1.html")`

### Requirement: SharePoint adapter projects recordings into weekly domain content
`SharepointApiAdapter` SHALL accept `(client: SharepointClient, semester_start: date, sharepoint_host: str, sharepoint_personal_host: str, recordings_folder_name: str, week_folder_prefix: str)` without `request_delay` or defaults, and MUST NOT store `request_delay`, `properties_to_retrieve`, `client`, or `search_client`, or import `time` or `ClientContext`. `can_handle(url)` SHALL match `{self.sharepoint_host, self.sharepoint_personal_host}` and `fetch_recordings` SHALL return weekly `list[RecordingContent]`. Verified at `SharepointApiAdapter` seam.

#### Scenario: Computing weekly folder and SharePoint stream URL per recording
- **WHEN** `SharepointApiAdapter.fetch_recordings(course_name)` finds recording files with `time_created` and `serverRelativeUrl`
- **THEN** it returns `RecordingContent(Path(safe_path_segment(f"{self.week_folder_prefix} {week}")) / safe_path_segment(name), last_mod, f"https://{self.sharepoint_personal_host}/:v:/r{quote(file.serverRelativeUrl)}?csf=1&web=1")` for each recording inside `self.recordings_folder_name` (matched case-insensitively)

#### Scenario: Skipping recordings missing creation timestamp without shifting subsequent weeks
- **WHEN** `SharepointApiAdapter.fetch_recordings(course_name)` encounters a recording file with `time_created = None` before a valid recording file
- **THEN** the file missing `time_created` is excluded and the subsequent file's `week` is still computed from its own `time_created.date()`

#### Scenario: Customizing SharePoint hosts, recordings folder name, and week prefix
- **WHEN** `SharepointApiAdapter` is initialized with custom `sharepoint_host`, `sharepoint_personal_host`, `recordings_folder_name="Videos"`, and `week_folder_prefix="Week"`
- **THEN** `can_handle` matches `(urlparse(url).hostname or "").lower() in {self.sharepoint_host, self.sharepoint_personal_host}` (and rejects other hosts), and `fetch_recordings` matches parent folders named `"videos"` and returns `RecordingContent` paths under `Path(f"Week {week}")` with stream URLs on `sharepoint_personal_host`

#### Scenario: Sanitizing unsafe SharePoint folder and file names without losing accents
- **WHEN** `SharepointApiAdapter` maps a recording, file, or subfolder named `"Sesión: 01/Parte?.mp4"` or `"CON"`
- **THEN** emitted `SharepointContent` and `RecordingContent` path segments become `"Sesión 01 Parte.mp4"` and `"_CON"`

### Requirement: CourseRepository orchestrates gateways and URL strategies
`CourseRepository` SHALL accept `(course_gateway: CourseGateway, recording_gateway: RecordingGateway, url_strategies: Sequence[UrlContentStrategy])`. `get_course_content(course, course_dir)` SHALL skip sections and modules with empty `name`, place each module's `inline_contents` before resolved `contents` in `safe_path_segment(module.name)`, prepend `section.inline_contents` before module items in `safe_path_segment(section.name)`, and wrap items in `course_dir`. Verified at the `CourseRepository` public seam.

#### Scenario: Composing course content hierarchy and ordering
- **WHEN** `CourseRepository.get_course_content(course, course_dir)` is called on a `Course` with sections and modules (including unsafe characters such as `:` or `/` in `section.name` or `module.name`)
- **THEN** it emits each section's `inline_contents` followed by each named module's `inline_contents` and URL-resolved `contents`, composed bottom-up via `.in_folder(safe_path_segment(module.name))`, `.in_folder(safe_path_segment(section.name))`, and `.in_folder(course_dir)`

#### Scenario: Wrapping recordings in target folder
- **WHEN** `CourseRepository.get_recordings(course_name, recordings_folder)` is called
- **THEN** it delegates to `recording_gateway.fetch_recordings(course_name)` and returns `[r.in_folder(recordings_folder) for r in recordings]`

### Requirement: Pure domain SyncDiff calculation and FileSystemPort orchestration
`SyncDiff.from_trees(remote_contents, local_mtimes)` in `src/idat_sync/domain/sync.py` SHALL compute `missing_paths`, `outdated_paths` (where `remote.last_modified > 0 and remote.last_modified > local_mtime`), `synced_paths`, `local_only_paths`, and `entries` sorted by `e.path` without disk I/O. `DownloadManager` and `IDATSync` SHALL perform all local disk reads and directory creation via `FileSystemPort` (`LocalFileSystemAdapter`). Verified at `SyncDiff.from_trees` and `DownloadManager` seams.

#### Scenario: Computing SyncDiff purely in memory
- **WHEN** `SyncDiff.from_trees(remote_contents, local_mtimes)` is called with in-memory `Content` items and a `Mapping[Path, int]`
- **THEN** it partitions paths into `missing_paths`, `outdated_paths`, `synced_paths`, and `local_only_paths` and sorts `entries` by `e.path` without touching the filesystem

#### Scenario: Orchestrating sync pipeline through FileSystemPort and Downloader groups
- **WHEN** `DownloadManager.diff_trees(root_dir, remote_contents)` and `IDATSync.sync_course(course, course_dir)` run
- **THEN** directory creation and file timestamp scanning are delegated to `FileSystemPort` and pending tasks execute through `TaskOrchestrator`

### Requirement: Composition root wires production dependencies outside application
`create_sync_service(credentials: Credentials, config: SyncConfig) -> IDATSync` in `src/idat_sync/bootstrap.py` SHALL require an explicit `SyncConfig`, construct all clients, adapters, `LocalFileSystemAdapter`, and `Downloader` groups with `config` values, wire `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)` directly to the low-level clients without `request_delay`, and inject the composed collaborators plus `recordings_folder_name` into `IDATSync`. `IDATSync.__init__` MUST NOT instantiate clients or adapters from `Credentials`. Verified at `bootstrap.create_sync_service` and `IDATSync` seams.

#### Scenario: Constructing IDATSync with injected collaborators
- **WHEN** `IDATSync(course_repository, download_manager, task_orchestrator, file_system, recordings_folder_name="Videos")` is instantiated and `sync_course(course, course_dir)` runs
- **THEN** it stores the injected collaborators without network or credential initialization in `__init__` and ensures and syncs recordings under `Path(course_dir) / "Videos"`

#### Scenario: Wiring SyncConfig through create_sync_service
- **WHEN** `create_sync_service(credentials, config=custom_config)` is called with a `SyncConfig` instance
- **THEN** it forwards `custom_config` fields to `MoodleClient`, `MoodleAdapter`, `SharepointClient`, `SharepointApiAdapter` (without `request_delay`), `RecordingDownloader`, and `IDATSync`, and wires `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)` with `moodle_dl.moodle_client is moodle_adapter.client` and `sp_dl.sharepoint_client is sp_adapter.sp_client`

### Requirement: Mandatory FileSystemPort delegation in DownloadManager
`DownloadManager` SHALL require `FileSystemPort` as a non-nullable constructor dependency and delegate all local directory tree modification timestamp scans to `FileSystemPort.scan_mtimes`. `DownloadManager.prepare_download_tasks(contents: Sequence[Content]) -> list[DownloadTask]` SHALL accept only `contents` and map each `Content` to a `DownloadTask` via `create_download_task` without performing filesystem queries. `DownloadManager` MUST NOT define `should_download`, accept `root_dir` in `prepare_download_tasks`, or perform direct filesystem I/O (`os`, `stat`, `rglob`, `mkdir`). `FileSystemPort` and `LocalFileSystemAdapter` MUST NOT define `exists` or `get_mtime`, and `DownloadTask` MUST NOT define `last_modified`. Verified at the `DownloadManager` and `FileSystemPort` / `LocalFileSystemAdapter` public seams.

#### Scenario: Scanning local tree modification times
- **WHEN** `DownloadManager.diff_trees(root_dir, remote_contents)` is called
- **THEN** it delegates scanning to `file_system.scan_mtimes(Path(root_dir))` without calling `Path.rglob` or `Path.stat` directly

#### Scenario: Determining whether a task needs download
- **WHEN** `DownloadManager.prepare_download_tasks(contents)` evaluates a sequence of `Content` items (such as `diff.pending_contents`)
- **THEN** it maps each `Content` to a `DownloadTask` via `create_download_task` without querying `file_system`, and rejects any `root_dir` argument with `TypeError`

#### Scenario: Enforcing minimal FileSystemPort and DownloadTask surface
- **WHEN** `FileSystemPort`, `LocalFileSystemAdapter`, `DownloadManager`, and `DownloadTask` are inspected
- **THEN** `FileSystemPort` and `LocalFileSystemAdapter` expose only `scan_mtimes` and `ensure_dir` (neither `exists` nor `get_mtime`), `DownloadManager` does not expose `should_download`, and `DownloadTask` exposes `path` and `download` without `last_modified`

### Requirement: Sealed Anti-Corruption Layer on Moodle adapter
`MoodleAdapter` public methods SHALL return only `domain/` models (`Course`, `CourseSection`, `CourseModule`, `ModuleContent`, `Content`), and `MoodleAdapter` MUST NOT define or expose a `token` property (`hasattr(MoodleAdapter, "token") == False`). Any helper that retrieves or processes raw vendor structures (`dict`, raw JSON payloads) without converting to domain entities SHALL be private. Verified at the `MoodleAdapter` public seam.

#### Scenario: Inspecting MoodleAdapter public method return types
- **WHEN** all public methods and attributes of `MoodleAdapter` are invoked or inspected
- **THEN** none of them return raw vendor dictionaries or SDK objects, and `MoodleAdapter` does not expose `token`

### Requirement: Ubiquitous domain language for specialized content types
The domain model and `GLOSSARY.md` SHALL define canonical terms for in-memory text artifacts (`StringContent`) and remote class video recordings (`RecordingContent`) as distinct subtypes of `Content`. Verified at the domain vocabulary seam.

#### Scenario: Consulting GLOSSARY.md for specialized content terms
- **WHEN** `GLOSSARY.md` is inspected under `Synchronization & Files`
- **THEN** both `StringContent` and `RecordingContent` are documented with their domain responsibilities and forbidden synonyms

### Requirement: Immutable SyncConfig schema and standard-library validation
`SyncConfig` in `src/idat_sync/config.py` SHALL be a frozen dataclass with defaults (`semester_start=date(2025, 9, 22)`, `request_delay=30.0`, `session_file=Path("./session.json")`, `log_file=Path("./log.err")`, `moodle_base_url`, `sharepoint_host`, `sharepoint_personal_host`, `sharepoint_academic_site_url`, `email_domain="idat.pe"`, `recordings_folder_name="Grabaciones"`, `week_folder_prefix="Semana"`) validated via `urllib.parse` and `PurePath`. Verified at `idat_sync.config` seam.

#### Scenario: Instantiating SyncConfig with defaults and URL normalization
- **WHEN** `SyncConfig(moodle_base_url="https://aulavirtual.idat.edu.pe/", sharepoint_academic_site_url="https://idat628.sharepoint.com/sites/MATERIALESTED-ACADEMICOIDAT/")` is instantiated
- **THEN** `urlsplit` and `urlunsplit` strip trailing slashes from the URL fields while preserving all default values (`semester_start == date(2025, 9, 22)`, `request_delay == 30.0`, `session_file == Path("./session.json")`, `log_file == Path("./log.err")`, `sharepoint_host == "idat628.sharepoint.com"`, `sharepoint_personal_host == "idat628-my.sharepoint.com"`, `email_domain == "idat.pe"`, `recordings_folder_name == "Grabaciones"`, `week_folder_prefix == "Semana"`)

#### Scenario: Rejecting malformed URLs, hostnames, folder names, or delays
- **WHEN** `SyncConfig` is constructed with a URL missing an `http`/`https` scheme or hostname, a host field (`sharepoint_host`, `sharepoint_personal_host`, `email_domain`) containing a scheme or path, a multi-segment or dot folder name (`recordings_folder_name`, `week_folder_prefix`) where `PurePath(val.strip()).name != val.strip()`, or a negative/non-finite/boolean `request_delay`
- **THEN** `SyncConfig.__post_init__` raises `ValueError`

### Requirement: Runtime configuration loading with environment and JSON precedence
`load_config(config_path: str | Path | None = None) -> SyncConfig` in `src/idat_sync/config.py` SHALL resolve settings with precedence `IDAT_SYNC_<UPPER_FIELD_NAME>` env vars > JSON keys from `config_path` or `IDAT_SYNC_CONFIG` (default `"./config.json"`) > `SyncConfig` defaults. Unknown JSON keys, boolean `request_delay`, or invalid `YYYY-MM-DD` dates SHALL raise `ValueError`. Verified at the `idat_sync.config.load_config` seam.

#### Scenario: Falling back to defaults when default config file is absent
- **WHEN** `load_config()` is called with no arguments, `IDAT_SYNC_CONFIG` is unset, and `./config.json` does not exist
- **THEN** it returns a `SyncConfig` instance populated with built-in defaults (plus any set `IDAT_SYNC_<UPPER_FIELD_NAME>` environment variables) without raising `FileNotFoundError`

#### Scenario: Raising FileNotFoundError when an explicit config file is missing
- **WHEN** `load_config(config_path)` or `IDAT_SYNC_CONFIG` specifies a path that does not exist on disk
- **THEN** `load_config` raises `FileNotFoundError`

#### Scenario: Applying precedence across defaults, JSON file, and environment variables
- **WHEN** `config.json` defines `{"semester_start": "2026-03-16", "request_delay": 5}` and the environment defines `IDAT_SYNC_REQUEST_DELAY="1.5"` and `IDAT_SYNC_RECORDINGS_FOLDER_NAME="Videos"`
- **THEN** `load_config` returns `SyncConfig` with `semester_start == date(2026, 3, 16)`, `request_delay == 1.5`, and `recordings_folder_name == "Videos"`

#### Scenario: Rejecting unknown JSON keys and invalid types in load_config
- **WHEN** `load_config` reads a JSON file with unknown keys, a non-object root, `request_delay: true`, or an invalid `semester_start` date string
- **THEN** `load_config` raises `ValueError`

### Requirement: Configurable tenant endpoints and throttling across clients, adapters, and downloaders
`MoodleClient`, `MoodleTokenClient`, `MoodleAdapter`, `SharepointClient`, `RecordingDownloader`, and `SharepointBrowserAuth` SHALL require injected tenant URL/host parameters without hardcoded defaults, and `MoodleClient` and `SharepointClient` SHALL require `request_delay` without hardcoded defaults as the sole owners of `time.sleep(self.request_delay)`. `MoodleTokenClient`, `MoodleDownloader`, `SharepointDownloader`, and `SharepointApiAdapter` MUST NOT accept `request_delay`. `src/idat_sync/infrastructure/downloaders.py` MUST NOT import `requests`, `REQUEST_HEADER`, `RQ_HEADER`, or `time`, `REQUEST_HEADER` SHALL reside in `src/idat_sync/infrastructure/moodle_client.py`, and `src/idat_sync/utils.py` MUST NOT exist. Verified at client, adapter, downloader, and AST seams.

#### Scenario: Respecting injected Moodle and SharePoint hosts and request delays
- **WHEN** `MoodleClient`, `MoodleAdapter`, `SharepointClient`, and `RecordingDownloader` are constructed with custom `base_url`, `moodle_base_url`, `sharepoint_personal_host`, and `request_delay`
- **THEN** `MoodleAdapter.can_handle` matches the hostname of `moodle_base_url`, `RecordingDownloader.can_handle` matches `f"{sharepoint_personal_host}/:v:"`, and `MoodleClient.call_ws`, `MoodleClient.download_file`, `SharepointClient.download_file`, `SharepointClient.fetch_document_library_items`, `SharepointClient.fetch_folder_files`, and `SharepointClient.fetch_subfolders` each sleep once per query for the injected `request_delay`

#### Scenario: Inspecting infrastructure imports and utils cleanup
- **WHEN** all Python modules under `src/idat_sync/infrastructure/` and `src/idat_sync/utils.py` are inspected
- **THEN** `src/idat_sync/utils.py` does not exist, no `infrastructure/` module imports `idat_sync.utils`, `downloaders.py` does not import `requests`, `REQUEST_HEADER`, `RQ_HEADER`, or `time`, `sharepoint_adapter.py` does not import `time` or `ClientContext`, and `REQUEST_HEADER` is defined in `src/idat_sync/infrastructure/moodle_client.py`

### Requirement: Unconditional target file path in RecordingDownloader
`RecordingDownloader.download(content: RecordingContent)` SHALL unconditionally treat `content.path` as the target file path by creating `Path(content.path).parent` (`mkdir(parents=True, exist_ok=True)`) and setting `yt_dlp` `outtmpl` to `f"{prefix}{os.path.abspath(Path(content.path))}"`. `RecordingDownloader.download` MUST NOT branch on `Path(content.path).suffix` or append `"%(title)s %(id)s.%(ext)s"`. Verified at the `RecordingDownloader` public seam.

#### Scenario: Downloading a recording with a file extension
- **WHEN** `RecordingDownloader.download(content)` is called with `RecordingContent(Path("Grabaciones/Semana 1/video.mp4"), 1700000000, url)`
- **THEN** it creates the parent directory `Grabaciones/Semana 1` and passes `f"{prefix}{os.path.abspath(Path('Grabaciones/Semana 1/video.mp4'))}"` as `outtmpl` to `yt_dlp.YoutubeDL`

#### Scenario: Downloading a recording without a file extension in path
- **WHEN** `RecordingDownloader.download(content)` is called with a `RecordingContent` whose `path` has no file suffix (`Path(content.path).suffix == ""`)
- **THEN** it still creates `Path(content.path).parent` (not `Path(content.path)` as a directory) and passes `f"{prefix}{os.path.abspath(Path(content.path))}"` as `outtmpl` without appending `"%(title)s %(id)s.%(ext)s"`

### Requirement: Client-owned file download streaming and SharePoint SDK query execution
`MoodleClient` and `SharepointClient` SHALL provide `download_file(..., dest: BinaryIO) -> None`, sleep `self.request_delay`, and raise `CredentialsExpiredError` on auth failure. `MoodleDownloader` and `SharepointDownloader` SHALL delegate to `client.download_file`. `SharepointClient` SHALL execute SDK queries via `.execute_query()` through `_run_query` (retrying HTTP `429` and `503` up to 5 attempts using `Retry-After` or `self.request_delay` backoff and re-raising on exhaustion, while failing immediately without retry on HTTP `401`/`403` or non-throttling errors), execute guest URL, file-by-ID, and folder SDK queries (including `/personal/<user>` sites and `odata.null` `TypeError` recovery), and `SharepointApiAdapter.list_url_content` SHALL dispatch by URL path and skip empty entities. Verified at client, adapter, and downloader seams.

#### Scenario: Streaming a Moodle file through MoodleClient
- **WHEN** `MoodleClient.download_file(url, dest)` is called with a target URL and writable binary stream
- **THEN** it performs `requests.get(url, params={"wstoken": self.token, "token": self.token}, stream=True, allow_redirects=True, timeout=60, headers=REQUEST_HEADER)`, raises `CredentialsExpiredError("403 Forbidden: your session cookie is missing/expired.")` on HTTP `401` or `403`, calls `raise_for_status()`, writes non-empty `1024 * 256`-byte chunks into `dest`, and calls `time.sleep(self.request_delay)`

#### Scenario: Delegating MoodleDownloader and SharepointDownloader file downloads to clients
- **WHEN** `MoodleDownloader(moodle_client).download(moodle_content)` and `SharepointDownloader(sharepoint_client).download(sp_content)` are called
- **THEN** each stores `self.moodle_client` or `self.sharepoint_client` without accepting `request_delay`, creates `Path(content.path).parent`, opens `content.path` as `"wb"`, and calls `moodle_client.download_file(moodle_content.url, f)` and `sharepoint_client.download_file(sp_content.file, local_file)` respectively (where `SharepointClient.download_file` runs `file.download_session(dest, use_path=False).execute_query()` and sleeps `self.request_delay`)

#### Scenario: Delegating SharePoint guest URLs, direct links, and folder traversal to SharepointClient
- **WHEN** `SharepointApiAdapter.list_url_content`, `fetch_folder_contents`, and `fetch_file_from_direct_link` resolve guest folder URLs (`^/:f:/`), guest file URLs (`^/:(\w+):/`) across `sharepoint_host` and `sharepoint_personal_host`, direct links with `uniqueid`/`sourcedoc`, and folder hierarchies
- **THEN** `SharepointApiAdapter` inspects `urlparse(url).path` without repeating the hostname check, calls `sp_client.fetch_folder_by_guest_url`, `sp_client.fetch_file_by_guest_url`, and `sp_client.fetch_file_by_id(link, file_id)` (skipping the client call and returning `None` when neither `uniqueid` nor `sourcedoc` is present), and calls `sp_client.fetch_folder_files(folder)` before `sp_client.fetch_subfolders(folder)`, while `SharepointClient.get_client_from_url` matches `r"/(sites|teams|s|personal)/([^/]+)"` case-insensitively (mapping `/s/<name>` to `{base_url}/sites/<name}` and `/(sites|teams|personal)/<name>` to `{base_url}{path_match.group(0)}`) and sleeps `self.request_delay`

#### Scenario: Skipping dead or expired SharePoint guest and direct links returning odata.null
- **WHEN** `SharepointClient.load_entity(entity, ctx)` encounters `TypeError` because an `odata.null` guest URL response left `entity.resource_url` as `None`, or `SharepointApiAdapter.list_url_content` receives a `Folder` or `File` where `not entity.name and not entity.serverRelativeUrl`
- **THEN** `SharepointClient.load_entity` catches `TypeError` and still sleeps `self.request_delay` (while propagating `CredentialsExpiredError` and non-`TypeError` exceptions unchanged), and `SharepointApiAdapter.list_url_content` returns `[]`

#### Scenario: Retrying SharePoint HTTP 429 and 503 with Retry-After backoff and re-raising on exhaustion
- **WHEN** a SharePoint SDK query inside `SharepointClient._run_query` raises `ClientRequestException` or `requests.HTTPError` with HTTP `429` or `503`
- **THEN** `SharepointClient._run_query` sleeps `int(Retry-After)` when a valid `Retry-After` response header is present (or `self.request_delay` otherwise), retries the query up to 5 total attempts, and re-raises the exception if all 5 attempts fail without retrying HTTP `401`, `403`, or `404`

### Requirement: ASCII compatibility and edge-case hardening in safe_filename
Per ADR-0002, `safe_filename(s, replacement=" ", max_len=100)` SHALL preserve ASCII NFKD normalization for top-level `Course` directories while checking `WINDOWS_RESERVED` against the stem before extensions and stripping trailing `[ .]+$` after `max_len` truncation. Verified at the `domain.path_utils` seam.

#### Scenario: Preserving ASCII course compatibility while fixing reserved stems and trailing dots
- **WHEN** `safe_filename` receives `"CON.txt"`, `"Lección 1: Diseño"`, or `"a" * 98 + "...b"` with `max_len=100`
- **THEN** it returns `"_CON.txt"`, `"Leccion 1 Diseno"`, and `"a" * 98` (with no trailing dot after truncation and length `<= 100`)

### Requirement: UTF-8 and extension-preserving segment sanitization in safe_path_segment
Per ADR-0002, `safe_path_segment(s, replacement=" ", max_len=100)` SHALL preserve UTF-8 accents and file extensions for `CourseSection`, `CourseModule`, and file names while stripping `<>:"/\|?*`, control characters `\x00-\x1F`/`\x7F`, `.`/`..`, post-truncation `[ .]+$`, and `WINDOWS_RESERVED` stems. Verified at the `domain.path_utils` seam.

#### Scenario: Preserving UTF-8 and extensions while making nested segments OS-safe
- **WHEN** `safe_path_segment` receives `"Lección 1: Diseño/Guía?.pdf"`, `"CON.txt"`, `".."`, or `"a" * 110 + ".mp4"` with `max_len=100`
- **THEN** it returns `"Lección 1 Diseño Guía.pdf"`, `"_CON.txt"`, `"untitled"`, and `"a" * 96 + ".mp4"` (length `<= 100`, UTF-8 and extension intact, no forbidden characters or trailing dots/spaces)

### Requirement: Traversal-safe path containment in Content.in_folder
`Content.in_folder(folder)` in `src/idat_sync/domain/content.py` SHALL fail closed with `ValueError` whenever `self.path` is anchored/absolute (`is_absolute()` or non-empty `.anchor`) or contains `..` in `.parts`, or whenever `folder` contains `..` in `.parts`, ensuring relative paths MUST NOT escape `course_dir`. Verified at the `Content.in_folder` seam.

#### Scenario: Rejecting anchored paths and parent traversal segments
- **WHEN** `Content(Path("/etc/passwd"), 0).in_folder("Course")`, `Content(Path("../secret.txt"), 0).in_folder("")`, or `Content(Path("file.pdf"), 0).in_folder("../outside")` is called
- **THEN** each call raises `ValueError` instead of discarding `folder` or resolving above `course_dir`

#### Scenario: Allowing an absolute base course directory with safe relative content paths
- **WHEN** `Content(Path("file.pdf"), 0).in_folder(Path("/tmp/Course"))` is called
- **THEN** it returns a `Content` whose `path` equals `Path("/tmp/Course/file.pdf")`

### Requirement: Credentials expiration mapping in Moodle, SharePoint, and recording layers
`MoodleClient.call_ws`, `MoodleClient.download_file`, `SharepointClient`, and `RecordingDownloader.download` SHALL map HTTP `401`, HTTP `403`, Moodle `errorcode == "invalidtoken"`, and empty `personal_site.url` into `CredentialsExpiredError`, while preserving non-auth exceptions unchanged. Verified at the `MoodleClient`, `SharepointClient`, and `RecordingDownloader` seams.

#### Scenario: Mapping Moodle invalidtoken and HTTP 401 or 403 to CredentialsExpiredError
- **WHEN** `MoodleClient.call_ws` receives `{"exception": "moodle_exception", "errorcode": "invalidtoken", ...}` or HTTP `401`/`403`, or `MoodleClient.download_file` receives HTTP `401`/`403`
- **THEN** `MoodleClient` raises `CredentialsExpiredError` (which is also a `RuntimeError`), while HTTP `500` or non-auth Moodle exceptions raise `requests.HTTPError` or `RuntimeError`

#### Scenario: Mapping SharePoint and recording auth failures to CredentialsExpiredError
- **WHEN** `SharepointClient` observes empty `personal_site.url` or an SDK HTTP `401`/`403` response, or `RecordingDownloader.download` fails with an HTTP `401`/`403` download error
- **THEN** both raise `CredentialsExpiredError`, while non-auth `404`/`500` errors propagate without masking

### Requirement: Per-item failure isolation and early credential abort in TaskOrchestrator
`TaskOrchestrator` SHALL preserve sequential task execution order within each downloader group, log and skip individual `DownloadTask` failures other than `CredentialsExpiredError`, and immediately abort remaining queued tasks across all downloader groups and propagate `CredentialsExpiredError`. Verified at the `TaskOrchestrator` seam.

#### Scenario: Continuing remaining tasks after a single-item download failure
- **WHEN** a `DownloadTask` raises a non-credential `Exception` (such as `requests.HTTPError` `404` or `OSError`) during `TaskOrchestrator.run`
- **THEN** the orchestrator logs the failure, executes all subsequent tasks in that downloader group and sibling groups in order, and returns without raising

#### Scenario: Aborting all remaining tasks on CredentialsExpiredError
- **WHEN** any `DownloadTask` raises `CredentialsExpiredError` during `TaskOrchestrator.run`
- **THEN** remaining unstarted tasks in that group and sibling groups MUST NOT run, and `CredentialsExpiredError` propagates to the caller

### Requirement: Per-course failure isolation and early credential abort in IDATSync
`IDATSync.sync_courses(root_folder)` SHALL compute each `Course` folder via `Path(root_folder) / safe_filename(course.name)`, log and skip single-`Course` failures other than `CredentialsExpiredError` so later courses still sync, and abort remaining courses immediately on `CredentialsExpiredError`. Verified at the `IDATSync` seam.

#### Scenario: Continuing remaining courses when one course fails
- **WHEN** `sync_course` raises a non-credential error on the first `Course` while a second `Course` succeeds
- **THEN** `IDATSync.sync_courses` logs the first `Course` error, still synchronizes the second `Course`, and finishes without raising

#### Scenario: Aborting remaining courses on CredentialsExpiredError
- **WHEN** the first `Course` raises `CredentialsExpiredError` inside `IDATSync.sync_courses`
- **THEN** subsequent courses MUST NOT be synchronized and `CredentialsExpiredError` propagates immediately

### Requirement: Developer CLI extraction scripts and gitignored local test artifacts
`scripts/login_cli.py` and `scripts/moodle_json.py` SHALL reside under `scripts/` (without root copies), and `.gitignore` SHALL list `all_courses_contents.json` and `session*.json`. `scripts/login_cli.py` SHALL save `Credentials` to `config.session_file`, and `scripts/moodle_json.py` SHALL write courses, contents, and assignments to `Path("./all_courses_contents.json")`. Verified at the `scripts/login_cli.py`, `scripts/moodle_json.py`, and `.gitignore` seams.

#### Scenario: Extracting courses, contents, and assignments to all_courses_contents.json
- **WHEN** `scripts/moodle_json.py` runs (reusing `Credentials` from `JsonCredentialStore(config.session_file)` when present, or prompting for `Username:` and `Password:` when absent)
- **THEN** it obtains a token, calls `fetch_enrolled_courses()`, `fetch_assignments()` (indexed by `int(course["id"])`), and `fetch_course_contents(int(course["id"]))` on `MoodleClient`, and writes UTF-8 JSON (`indent=2, ensure_ascii=False`) with exact keys `{"course": course, "contents": ..., "assignments": ...}` per course directly to `Path("./all_courses_contents.json")` without dumping JSON to stdout

#### Scenario: Keeping extracted curriculum and Credentials files gitignored
- **WHEN** `.gitignore` and the repository layout are inspected
- **THEN** `.gitignore` contains `all_courses_contents.json` and `session*.json`, `scripts/login_cli.py` and `scripts/moodle_json.py` exist using `Credentials` terminology, and root `login_cli.py` and `moodle_json.py` do not exist

### Requirement: Conditional real-data offline fixture and live Credentials smoke testing
`tests/test_real_data.py` SHALL define `TestRealCourseContentsFixture` and `TestLiveCredentialsIntegration` guarded by `@unittest.skipUnless` on `./all_courses_contents.json` and `./session.json`, with zero hardcoded course IDs, teacher names, or share tokens, and zero edits to `domain/`, `application/`, `ui/`, `bootstrap.py`, or `config.py`. Verified at the `MoodleAdapter`, `SharepointApiAdapter`, `CourseRepository`, `SyncDiff.from_trees`, `JsonCredentialStore`, and `MoodleDownloader` seams.

#### Scenario: Running offline fixture tests against all_courses_contents.json
- **WHEN** `TestRealCourseContentsFixture` runs with `./all_courses_contents.json` present (or skips via `@unittest.skipUnless(Path("./all_courses_contents.json").is_file(), "Requires ./all_courses_contents.json")` when absent)
- **THEN** it feeds `"course"`, `"contents"`, and `"assignments"` (defaulting missing `"assignments"` to `[]`) through `MoodleAdapter.get_courses_with_contents()` and `CourseRepository.get_course_content()`, verifies every emitted item is a domain `Content` (`StringContent`, `MoodleContent`, or `SharepointContent`) safely inside its `course_dir`, verifies `"modname": "assign"` modules with matching `"assignments"` emit `-[id]-intro.html` `StringContent` items and `introattachments` `ModuleContent` items, verifies both `sharepoint_host` and `sharepoint_personal_host` URLs route to `SharepointApiAdapter`, and verifies `SyncDiff.from_trees(remote_contents, {})` marks all resolved paths as `MISSING`

#### Scenario: Running live Credentials smoke tests against session.json and all_courses_contents.json
- **WHEN** `TestLiveCredentialsIntegration` runs with both `./all_courses_contents.json` and `./session.json` present (or skips via `@unittest.skipUnless(Path("./all_courses_contents.json").is_file() and Path("./session.json").is_file(), "Requires ./all_courses_contents.json and ./session.json")` when absent or when `JsonCredentialStore(Path("./session.json")).load()` returns `None`)
- **THEN** it initializes clients with `request_delay=0.0`, dynamically discovers the first matching URL from `./all_courses_contents.json` for each category (1 Moodle `/pluginfile.php/` URL, 1 `:b:` guest file URL on `sharepoint_host`, 1 `:f:` guest folder URL on `sharepoint_host`, 1 direct link with `uniqueid=`/`sourcedoc=` on `sharepoint_host`, and 1 `:b:` guest file URL on `sharepoint_personal_host`), downloads the Moodle file into a `tempfile.TemporaryDirectory()` via `MoodleDownloader` verifying a non-empty file on disk, and resolves the 4 SharePoint URLs via `SharepointApiAdapter.list_url_content` into non-empty `list[SharepointContent]` (failing immediately if `CredentialsExpiredError` is raised)
