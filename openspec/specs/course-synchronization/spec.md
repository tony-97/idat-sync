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
`MoodleAdapter` SHALL map Moodle sections, modules, and assignments into `Course`, `CourseSection`, and `CourseModule` with `inline_contents: list[StringContent]` and `contents: list[ModuleContent]`. `domain/course.py` MUST NOT define `Assignment`, `Course.assignments`, `CourseSection.summary`, `CourseModule.modname`, or `CourseModule.description`. Verified at the `MoodleAdapter` public seam.

#### Scenario: Generating section summary inline content
- **WHEN** `MoodleAdapter` converts a raw section with `id` and `name` and `summary`
- **THEN** the resulting `CourseSection` has `inline_contents` set to `[StringContent(Path(f"{section_name}-[{section_id}]-resumen.html"), 0, [summary])]`

#### Scenario: Mapping label modules with non-empty and empty descriptions
- **WHEN** `MoodleAdapter` converts a raw module with `modname == "label"`
- **THEN** `CourseModule.inline_contents` is `[StringContent(Path(f"{module_name}.html"), 0, [description])]` with `contents = []` when `description` is non-empty, and both `inline_contents` and `contents` are `[]` when `description` is empty

#### Scenario: Merging assignment intro and attachments by cmid
- **WHEN** `MoodleAdapter` converts a raw module with `modname == "assign"` and `id` matching an assignment's `cmid`
- **THEN** `CourseModule.inline_contents` is `[StringContent(Path(f"{assign_name}-[{assign_id}]-intro.html"), 0, [assign_intro])]` and `CourseModule.contents` contains the mapped `ModuleContent` items from `introattachments` (or both are `[]` when no matching assignment exists)

### Requirement: SharePoint adapter projects recordings into weekly domain content
`SharepointApiAdapter` SHALL accept `(client: SharepointClient, semester_start: date, sharepoint_host: str, sharepoint_personal_host: str, recordings_folder_name: str, week_folder_prefix: str)` without `request_delay`, without hardcoded defaults, and without instantiating `SharepointClient` in `__init__`. `SharepointApiAdapter` MUST NOT store `request_delay`, `properties_to_retrieve`, `client`, or `search_client`, and MUST NOT import `time` or `ClientContext`. `fetch_recordings(course_name)` SHALL compute `week = 1 + (file.time_created.date() - self.semester_start).days // 7` and return `list[RecordingContent]`. Verified at `SharepointApiAdapter` seam.

#### Scenario: Computing weekly folder and SharePoint stream URL per recording
- **WHEN** `SharepointApiAdapter.fetch_recordings(course_name)` finds recording files with `time_created` and `serverRelativeUrl`
- **THEN** it returns `RecordingContent(Path(f"{self.week_folder_prefix} {week}") / name, last_mod, f"https://{self.sharepoint_personal_host}/:v:/r{quote(file.serverRelativeUrl)}?csf=1&web=1")` for each recording inside `self.recordings_folder_name` (matched case-insensitively)

#### Scenario: Skipping recordings missing creation timestamp without shifting subsequent weeks
- **WHEN** `SharepointApiAdapter.fetch_recordings(course_name)` encounters a recording file with `time_created = None` before a valid recording file
- **THEN** the file missing `time_created` is excluded and the subsequent file's `week` is still computed from its own `time_created.date()`

#### Scenario: Customizing SharePoint hosts, recordings folder name, and week prefix
- **WHEN** `SharepointApiAdapter` is initialized with custom `sharepoint_host`, `sharepoint_personal_host`, `recordings_folder_name="Videos"`, and `week_folder_prefix="Week"`
- **THEN** `can_handle` matches `sharepoint_host`, and `fetch_recordings` matches parent folders named `"videos"` and returns `RecordingContent` paths under `Path(f"Week {week}")` with stream URLs on `sharepoint_personal_host`

### Requirement: CourseRepository orchestrates gateways and URL strategies
`CourseRepository` SHALL accept `(course_gateway: CourseGateway, recording_gateway: RecordingGateway, url_strategies: Sequence[UrlContentStrategy])`. `get_course_content(course, course_dir)` SHALL skip sections and modules with empty `name`, place each module's `inline_contents` before resolved `contents` in `module.name`, prepend `section.inline_contents` before module items in `section.name`, and wrap items in `course_dir`. Verified at the `CourseRepository` public seam.

#### Scenario: Composing course content hierarchy and ordering
- **WHEN** `CourseRepository.get_course_content(course, course_dir)` is called on a `Course` with sections and modules
- **THEN** it emits each section's `inline_contents` followed by each named module's `inline_contents` and URL-resolved `contents`, composed bottom-up via `.in_folder(module.name)`, `.in_folder(section.name)`, and `.in_folder(course_dir)`

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
- **THEN** `MoodleAdapter.can_handle` matches the hostname of `moodle_base_url`, `RecordingDownloader.can_handle` matches `f"{sharepoint_personal_host}/:v:"`, and `MoodleClient.call_ws`, `MoodleClient.download_file`, `SharepointClient.download_file`, `SharepointClient.fetch_folder_files`, and `SharepointClient.fetch_subfolders` sleep for the injected `request_delay`

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
`MoodleClient` and `SharepointClient` SHALL provide `download_file(..., dest: BinaryIO) -> None` to stream remote files into an open binary stream and sleep `self.request_delay`. `MoodleDownloader` and `SharepointDownloader` SHALL accept their `*Client`, create parent directories, open `content.path` as `"wb"`, and delegate to `client.download_file`. `SharepointClient` SHALL execute all guest URL, file-by-ID, and folder SDK queries. Verified at client, adapter, and downloader seams.

#### Scenario: Streaming a Moodle file through MoodleClient
- **WHEN** `MoodleClient.download_file(url, dest)` is called with a target URL and writable binary stream
- **THEN** it performs `requests.get(url, params={"wstoken": self.token, "token": self.token}, stream=True, allow_redirects=True, timeout=60, headers=REQUEST_HEADER)`, raises `RuntimeError("403 Forbidden: your session cookie is missing/expired.")` on HTTP `403`, calls `raise_for_status()`, writes non-empty `1024 * 256`-byte chunks into `dest`, and calls `time.sleep(self.request_delay)`

#### Scenario: Delegating MoodleDownloader and SharepointDownloader file downloads to clients
- **WHEN** `MoodleDownloader(moodle_client).download(moodle_content)` and `SharepointDownloader(sharepoint_client).download(sp_content)` are called
- **THEN** each stores `self.moodle_client` or `self.sharepoint_client` without accepting `request_delay`, creates `Path(content.path).parent`, opens `content.path` as `"wb"`, and calls `moodle_client.download_file(moodle_content.url, f)` and `sharepoint_client.download_file(sp_content.file, local_file)` respectively (where `SharepointClient.download_file` runs `file.download_session(dest).execute_query_with_incremental_retry()` and sleeps `self.request_delay`)

#### Scenario: Delegating SharePoint guest URLs, direct links, and folder traversal to SharepointClient
- **WHEN** `SharepointApiAdapter.list_url_content`, `fetch_folder_contents`, and `fetch_file_from_direct_link` resolve guest folder URLs, guest file URLs, direct links with `uniqueid`/`sourcedoc`, and folder hierarchies
- **THEN** `SharepointApiAdapter` calls `sp_client.fetch_folder_by_guest_url`, `sp_client.fetch_file_by_guest_url`, `sp_client.fetch_file_by_id(link, file_id)` (skipping the client call and returning `None` when neither `uniqueid` nor `sourcedoc` is present), and calls `sp_client.fetch_folder_files(folder)` before `sp_client.fetch_subfolders(folder)`, while `SharepointClient` executes all `ClientContext` / `execute_query_retry` calls and `time.sleep(self.request_delay)`
