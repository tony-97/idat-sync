# course-synchronization Specification

## Purpose

Synchronizes remote `Course` structures, inline HTML artifacts, external URLs, and class recordings into a local directory tree through inward-pointing hexagonal ports and adapters.

## Requirements

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

### Requirement: Moodle adapter translates vendor curriculum into domain models
`ModuleContent` in `src/idat_sync/domain/course.py` SHALL define `filepath: str = "/"` alongside `filename`, `fileurl`, `timemodified`, and `filesize`. `MoodleAdapter` SHALL map Moodle sections, modules, and assignments into `Course`, `CourseSection`, and `CourseModule` with `inline_contents: list[StringContent]` and `contents: list[ModuleContent]`, populating `ModuleContent.filepath` from `raw_content.get("filepath") or "/"` for both module `contents` and assignment `introattachments` while sanitizing generated `.html` filenames and resolved `MoodleContent` filenames with `safe_path_segment`. For non-`Bibliografía` sections, `MoodleAdapter` SHALL consolidate all `modname == "label"` modules with non-empty `description` into a single section-level `StringContent` named `info.html` rather than per-label `CourseModule` items; for sections whose normalized name is `"bibliografia"`, `MoodleAdapter` SHALL collect all non-empty `label` descriptions and `url` entries (`name` and `fileurl`) in module order into a single section-level `StringContent` named `bibliografia.html` without emitting `info.html` or `ModuleContent` items for those URLs. `domain/course.py` MUST NOT define `Assignment`, `Course.assignments`, `CourseSection.summary`, `CourseModule.modname`, or `CourseModule.description`. Verified at the `MoodleAdapter` public seam.

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
- **WHEN** `MoodleAdapter` converts a raw module with `modname == "assign"` and `id` matching an assignment's `cmid`
- **THEN** `CourseModule.inline_contents` is `[StringContent(Path(safe_path_segment(f"{assign_name}-[{assign_id}]-intro.html")), 0, [assign_intro])]` and `CourseModule.contents` contains the mapped `ModuleContent` items from `introattachments` (or both are `[]` when no matching assignment exists)

#### Scenario: Preserving ModuleContent filepath from Moodle payloads
- **WHEN** `MoodleAdapter` maps a module `contents` item or `assign` `introattachments` item with `filepath: "/Parte 1/"` alongside an item omitting `filepath` or setting `filepath: ""`
- **THEN** the first `ModuleContent` has `filepath == "/Parte 1/"` and the second defaults to `filepath == "/"`

#### Scenario: Sanitizing unsafe resource and inline filenames while preserving accents
- **WHEN** `MoodleAdapter.list_url_content` or inline content mapping receives resource `"Guía: Tema 1/2?.pdf"`, `"CON.txt"`, or assignment `"Aviso: CON/1"`
- **THEN** the resulting `MoodleContent.path` values are `Path("Guía Tema 1 2.pdf")` and `Path("_CON.txt")`, and the assignment `StringContent.path` is sanitized with `safe_path_segment` while preserving the `-[<assign_id>]-intro.html` suffix

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

### Requirement: CourseRepository orchestrates gateways and URL strategies
`CourseRepository` in `src/idat_sync/application/course_repository.py` SHALL accept `(course_gateway: CourseGateway, recording_gateway: RecordingGateway, url_strategies: Sequence[UrlContentStrategy])` and expose `async def list_courses(self) -> list[Course]`, `async def populate_course_sections(self, course: Course) -> Course`, `def invalidate_cache(self) -> None` (delegating to `self.course_gateway.invalidate_cache()`), `async def list_url_content(self, content: ModuleContent) -> list[Content]`, `async def stream_course_content(self, course: Course, course_dir: Path | str = "") -> AsyncIterator[Content]`, and `async def get_recordings(self, course_name: str, recordings_folder: str | Path = "") -> list[RecordingContent]`. `CourseRepository.list_url_content(content)` SHALL preserve non-root `ModuleContent.filepath` subfolders without modifying the `UrlContentStrategy.list_url_content(url, filename, last_modified)` signature by splitting `content.filepath` on `"/"`, raising `ValueError` on any `".."` traversal segment, ignoring empty and `"."` segments, sanitizing each remaining subfolder segment via `safe_path_segment`, and applying those relative subfolders via `Content.in_folder(...)`. When `await strategy.list_url_content(...)` raises `CredentialsExpiredError`, `list_url_content` SHALL propagate `CredentialsExpiredError` immediately; when `await strategy.list_url_content(...)` raises `RemoteServiceError`, `list_url_content` SHALL log a warning and return `[]` so remaining items in the course continue resolving. `stream_course_content(course, course_dir)` SHALL skip sections and modules with empty `name`, wrap section `inline_contents` in `safe_path_segment(section.name)` and `course_dir`, wrap module `inline_contents` and resolved `contents` in `<bounded_name>-[<module.id>]` (`<= 100` characters total, truncating the sanitized `module.name` prefix first so `-[<module.id>]` is never truncated), `safe_path_segment(section.name)`, and `course_dir`, build the pure inline and per-module resolution plan before spawning any tasks (so an unsafe inline path raises before any task is created), yield all `inline_contents` in section/module order first, spawn one concurrent `asyncio.Task` per named `CourseModule` with non-empty `contents` (resolving each module's `module.contents` sequentially in Moodle list order), iterate module completions via Python 3.12-compatible `for coro in asyncio.as_completed(tasks):`, and cancel and await all unfinished module tasks in `finally` on early consumer exit, cancellation, or `CredentialsExpiredError` with zero leaked tasks. Verified at the `CourseRepository` (`src/idat_sync/application/course_repository.py`) public seam.

#### Scenario: Composing course content hierarchy and ordering
- **WHEN** `CourseRepository.stream_course_content(course, course_dir)` iterates a `Course` with named sections and modules (including unsafe characters such as `:` or `/` in `section.name` or `module.name`, a slow SharePoint module alongside a fast Moodle module, and `CourseModule(id=201, name="Carpeta", ...)` containing `"guia.pdf"` with `filepath="/Parte 1/"` and `filepath="/Parte 2/"` and root items with `filepath="/"`)
- **THEN** it yields all section and module `inline_contents` first in section/module order, yields the fast module's resolved `Content` items before the slow module finishes, preserves the original `module.contents` order within each module while invoking `strategy.list_url_content(url, filename, timemodified)` with the unchanged 3-argument signature, and composes paths via subfolder `.in_folder(...)`, `.in_folder("<safe_bounded_module_name>-[<module.id>]")`, `.in_folder(safe_path_segment(section.name))`, and `.in_folder(course_dir)`

#### Scenario: Cancelling unfinished module tasks on early exit or CredentialsExpiredError
- **WHEN** a caller breaks out of `async for item in repo.stream_course_content(course, course_dir):` early, cancels the consuming task, encounters an unsafe inline path while planning, or a module task raises `CredentialsExpiredError`
- **THEN** all unfinished module resolution tasks are cancelled and awaited in `finally` (and zero tasks are spawned on a planning `ValueError`) with zero leaked pending tasks, subsequent `module.contents` in the failing module do not run, and `CredentialsExpiredError` propagates immediately

#### Scenario: Preserving module ID suffix when module name exceeds segment budget
- **WHEN** `CourseRepository.stream_course_content(course, course_dir)` processes two distinct `CourseModule` items (`id=1001`, `id=1002`) whose names exceed 100 characters and share an identical 95-character prefix
- **THEN** each module directory segment is truncated before appending its suffix so both segments have length `<= 100`, end with `-[1001]` and `-[1002]`, and remain distinct paths

#### Scenario: Rejecting traversal segments and sanitizing unsafe names in ModuleContent filepath
- **WHEN** `await CourseRepository.list_url_content(content)` receives `filepath="/Tema: 1/./CON/"` vs `filepath="/../outside/"`
- **THEN** `"/Tema: 1/./CON/"` resolves under `Path("Tema 1/_CON/<filename>")` and `"/../outside/"` raises `ValueError`

#### Scenario: Skipping broken URL links on RemoteServiceError while propagating CredentialsExpiredError
- **WHEN** `await CourseRepository.list_url_content(broken)` encounters `RemoteServiceError` vs `CredentialsExpiredError`
- **THEN** on `RemoteServiceError` it logs a warning and returns `[]`, whereas `CredentialsExpiredError` propagates immediately

#### Scenario: Wrapping recordings in target folder
- **WHEN** `await CourseRepository.get_recordings(course_name, recordings_folder)` is called
- **THEN** it awaits `recording_gateway.fetch_recordings(course_name)` and returns `[r.in_folder(recordings_folder) for r in recordings]`

#### Scenario: Delegating cache invalidation from CourseRepository to CourseGateway
- **WHEN** `CourseRepository.invalidate_cache()` is called
- **THEN** it invokes `self.course_gateway.invalidate_cache()` once

### Requirement: Composition root wires production dependencies outside application
`async def create_sync_service(credentials: Credentials, config: SyncConfig) -> IDATSync` in `src/idat_sync/bootstrap.py` SHALL require an explicit `SyncConfig` without a `publish` parameter, construct `moodle_limiter = AsyncRateLimiter(config.request_delay)` and `sharepoint_limiter = AsyncRateLimiter(config.request_delay)`, construct `MoodleClient(..., rate_limiter=moodle_limiter)` and `MoodleAdapter`, await `await SharepointClient.create(..., rate_limiter=sharepoint_limiter)` (awaiting `moodle_client.aclose()` before re-raising if `SharepointClient.create(...)` fails), construct `SharepointApiAdapter`, `LocalFileSystemAdapter`, and `Downloader` groups with `config` values, wire `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)` directly to the low-level clients without `request_delay`, wire `RecordingDownloader(cookies, sharepoint_personal_host=config.sharepoint_personal_host, rate_limiter=sharepoint_limiter)` sharing the exact `sharepoint_limiter` instance with `SharepointClient`, wire `TaskOrchestrator(downloader_group)`, and inject the composed collaborators, `recordings_folder_name`, and `closers=[moodle_client.aclose, sharepoint_raw_client.aclose]` into `IDATSync`. `IDATSync.__init__` SHALL accept optional `closers: Sequence[Callable[[], Awaitable[None]]] = ()` without instantiating clients or adapters from `Credentials` and MUST NOT accept `publish`; `IDATSync` SHALL expose `async def aclose(self) -> None` (invoking every closer once, ensuring remaining closers still execute if an earlier closer fails, and re-raising the first exception if any) and `__aenter__` / `__aexit__` delegating to `aclose()`. `configure_logging(log_file: Path) -> logging.Logger` in `src/idat_sync/bootstrap.py` SHALL configure `logging.getLogger("idat_sync")` at `logging.INFO` with `propagate = False`, closing and clearing existing handlers before attaching `logging.StreamHandler()` and `logging.FileHandler(log_file, mode="w", encoding="utf-8")`. Verified at `bootstrap.create_sync_service`, `bootstrap.configure_logging`, and `IDATSync` (`src/idat_sync/application/sync_service.py`) seams.

#### Scenario: Constructing IDATSync with injected collaborators
- **WHEN** `IDATSync(course_repository, download_manager, task_orchestrator, file_system, recordings_folder_name="Videos")` is instantiated and `async for _ in sync.sync_course(course, course_dir):` runs
- **THEN** it stores the injected collaborators without network or credential initialization in `__init__`, rejects a `publish` keyword argument with `TypeError`, and ensures and syncs recordings under `Path(course_dir) / "Videos"`

#### Scenario: Closing injected closers deterministically via IDATSync.aclose
- **WHEN** `IDATSync(..., closers=[closer_a, closer_b])` exits `async with` or awaits `await sync.aclose()`
- **THEN** each closer is awaited once (`closer_b` still runs if `closer_a` raises, re-raising the first failure) and repeated `aclose()` calls are safe no-ops

#### Scenario: Wiring SyncConfig through create_sync_service
- **WHEN** `configure_logging(log_file)` and `await create_sync_service(credentials, config=custom_config)` are called
- **THEN** `configure_logging` configures `logging.getLogger("idat_sync")` at `logging.INFO` (`propagate == False`) with one `StreamHandler` and one `FileHandler` targeting `log_file`, `await create_sync_service` forwards `custom_config` fields to `MoodleClient`, `MoodleAdapter`, `await SharepointClient.create(...)`, `SharepointApiAdapter` (without `request_delay`), `RecordingDownloader`, `TaskOrchestrator`, and `IDATSync`, wires `moodle_adapter.client.rate_limiter` and `sp_adapter.sp_client.rate_limiter` as distinct `AsyncRateLimiter(custom_config.request_delay)` instances with `rec_dl.rate_limiter is sp_adapter.sp_client.rate_limiter`, wires `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)` with `moodle_dl.moodle_client is moodle_adapter.client` and `sp_dl.sharepoint_client is sp_adapter.sp_client`, and `await idat_sync.aclose()` closes both `moodle_client` and `sharepoint_raw_client`

#### Scenario: Cleaning up MoodleClient when SharepointClient.create fails in create_sync_service
- **WHEN** `await SharepointClient.create(...)` raises during `await create_sync_service(credentials, config=custom_config)`
- **THEN** `moodle_client.aclose()` is awaited before the exception propagates

### Requirement: Mandatory FileSystemPort delegation in DownloadManager
`DownloadManager` SHALL require `FileSystemPort` as a non-nullable constructor dependency and delegate all local file reads to `FileSystemPort`. `FileSystemPort` and `LocalFileSystemAdapter` SHALL expose `scan_mtimes(root_dir)`, `ensure_dir(path)`, and `read_bytes(path) -> bytes | None` (returning `None` on `OSError`). `LocalFileSystemAdapter` in `src/idat_sync/infrastructure/filesystem.py` SHALL exclude temporary `*.part` files (`not p.name.endswith(".part")`) in `scan_mtimes(root_dir)`, apply Windows extended-length `\\?\` path normalization internally at the OS boundary (returning `scan_mtimes` keys relative to the caller's `root_dir` without `\\?\`) so directories and files exceeding 259 characters on Windows are created, read, and scanned accurately, and translate `OSError` in `scan_mtimes` and `ensure_dir` into `FileSystemError(...) from exc`. `DownloadManager` SHALL expose `create_download_task(self, content: Content) -> DownloadTask | None` and `is_pending(self, content: Content, local_mtimes: Mapping[Path, int]) -> bool`, delegating mtime/existence checks to `is_content_pending(content, local_mtimes)` and, for any `StringContent` already in `local_mtimes` with `last_modified == 0` and non-empty `content.rendered_bytes()`, returning `True` when `self.file_system.read_bytes(content.path) != rendered` (without hashing). `DownloadManager` MUST NOT perform direct filesystem I/O (`os`, `stat`, `rglob`, `mkdir`). Verified at the `DownloadManager` and `FileSystemPort` / `LocalFileSystemAdapter` public seams.

#### Scenario: Scanning local tree modification times
- **WHEN** `LocalFileSystemAdapter.scan_mtimes(root_dir)` runs on a directory containing completed files, `*.part` temporary files, or nested paths exceeding 259 characters on Windows
- **THEN** `LocalFileSystemAdapter.scan_mtimes` returns modification timestamps keyed under `root_dir` (including deep Windows paths) while omitting any file whose name ends with `".part"`

#### Scenario: Translating OSError in scan_mtimes and ensure_dir to FileSystemError
- **WHEN** `LocalFileSystemAdapter.scan_mtimes(root_dir)` or `LocalFileSystemAdapter.ensure_dir(path)` encounters an `OSError` vs `read_bytes(path)` encountering an `OSError`
- **THEN** `scan_mtimes` and `ensure_dir` raise `FileSystemError(...) from exc`, while `read_bytes` returns `None`

#### Scenario: Determining whether a task needs download
- **WHEN** `DownloadManager.is_pending(content, local_mtimes)` and `DownloadManager.create_download_task(content)` evaluate missing, outdated, synced, and `StringContent(last_modified=0)` items (with matching, modified, or unreadable local bytes)
- **THEN** `is_pending` returns `True` for missing, outdated, and byte-mismatched/unreadable `StringContent` items and `False` for synced items without calling `Path.rglob` or `Path.stat` directly, and `create_download_task` maps each supported `Content` to a `DownloadTask` without querying `file_system`

#### Scenario: Enforcing minimal FileSystemPort and DownloadTask surface
- **WHEN** `FileSystemPort`, `LocalFileSystemAdapter`, `DownloadManager`, and `DownloadTask` are inspected
- **THEN** `FileSystemPort` and `LocalFileSystemAdapter` expose `scan_mtimes`, `ensure_dir`, and `read_bytes`, `DownloadManager` exposes `create_download_task` and `is_pending` without direct filesystem I/O, and `DownloadTask` exposes `path` and `download`

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
`src/idat_sync/infrastructure/rate_limiter.py` SHALL define `AsyncRateLimiter(delay: float)` with `self.delay = delay`, `self._lock = asyncio.Lock()`, `self._next_allowed = 0.0`, `async def __aenter__(self) -> Self` (returning `self` immediately when `self.delay <= 0`, otherwise acquiring `self._lock`, awaiting `asyncio.sleep(self._next_allowed - now)` when `now < self._next_allowed` via `time.monotonic()`, and releasing `self._lock` in `except BaseException:` if cancelled or failing during `__aenter__`), and `async def __aexit__(self, exc_type, exc_val, exc_tb) -> None` (returning immediately when `self.delay <= 0`, otherwise stamping `self._next_allowed = time.monotonic() + self.delay` when `exc_type is None` and always releasing `self._lock` in `finally`). `MoodleClient`, `MoodleTokenClient`, `MoodleAdapter`, `SharepointClient`, `RecordingDownloader`, and `SharepointBrowserAuth` SHALL require injected tenant URL/host parameters without hardcoded defaults, and `MoodleClient`, `SharepointClient` (including `SharepointClient.create`), and `RecordingDownloader` SHALL require `rate_limiter: AsyncRateLimiter` (stored as `self.rate_limiter`) without hardcoded defaults and MUST NOT accept or store `request_delay`. `MoodleTokenClient`, `MoodleDownloader`, `SharepointDownloader`, and `SharepointApiAdapter` MUST NOT accept `request_delay` or `rate_limiter`. `src/idat_sync/infrastructure/moodle_client.py`, `src/idat_sync/infrastructure/sharepoint_client.py`, `src/idat_sync/infrastructure/sharepoint_adapter.py`, and `src/idat_sync/infrastructure/downloaders.py` MUST NOT import `requests` or `time`, `src/idat_sync/infrastructure/moodle_client.py` and `src/idat_sync/infrastructure/sharepoint_client.py` MUST NOT call `asyncio.sleep`, `REQUEST_HEADER` SHALL reside in `src/idat_sync/infrastructure/moodle_client.py`, and `src/idat_sync/utils.py` MUST NOT exist. Verified at `AsyncRateLimiter` (`src/idat_sync/infrastructure/rate_limiter.py`), client, adapter, downloader, and AST seams.

#### Scenario: Enforcing end-to-start cooldown intervals and lock release in AsyncRateLimiter
- **WHEN** `AsyncRateLimiter(30.0)` is entered sequentially, entered with `delay <= 0`, exited with an unhandled exception inside the `async with` block, or cancelled while sleeping in `__aenter__`
- **THEN** the first `async with limiter:` enters without awaiting `asyncio.sleep` and stamps `_next_allowed = now + 30.0` on normal exit, a subsequent `async with limiter:` entered `12.0` seconds after the first exited awaits `asyncio.sleep(18.0)` once (and skips `asyncio.sleep` when `elapsed >= 30.0` or `delay <= 0`), exiting with an exception leaves `_next_allowed` unchanged while releasing `_lock`, and cancelling `__aenter__` during `asyncio.sleep` releases `_lock` before `CancelledError` propagates

#### Scenario: Respecting injected Moodle and SharePoint hosts and request delays
- **WHEN** `MoodleClient`, `MoodleAdapter`, `await SharepointClient.create(...)`, and `RecordingDownloader` are constructed with custom `base_url`, `moodle_base_url`, `sharepoint_personal_host`, and `rate_limiter`
- **THEN** `MoodleClient`, `SharepointClient`, and `RecordingDownloader` store `self.rate_limiter` and reject `request_delay` with `TypeError` (`hasattr(..., "request_delay") == False`), `MoodleAdapter.can_handle` matches the hostname of `moodle_base_url`, `RecordingDownloader.can_handle` matches `f"{sharepoint_personal_host}/:v:"`, and `MoodleClient.call_ws`, `MoodleClient.download_file`, `SharepointClient.create`, `SharepointClient.load_entity`, `SharepointClient.search_for_recordings`, `SharepointClient.download_file`, `SharepointClient.fetch_document_library_items`, `SharepointClient.fetch_folder_files`, `SharepointClient.fetch_subfolders`, and `RecordingDownloader.download` each enter `async with self.rate_limiter:` once per operation

#### Scenario: Inspecting infrastructure imports and utils cleanup
- **WHEN** all Python modules under `src/idat_sync/infrastructure/` and `src/idat_sync/utils.py` are inspected
- **THEN** `src/idat_sync/utils.py` does not exist, no `infrastructure/` module imports `idat_sync.utils` or `requests`, `moodle_client.py`, `sharepoint_client.py`, `sharepoint_adapter.py`, and `downloaders.py` do not import `time`, `moodle_client.py` does not import `asyncio`, `sharepoint_adapter.py` does not import `ClientContext`, and `REQUEST_HEADER` is defined in `src/idat_sync/infrastructure/moodle_client.py`

### Requirement: Unconditional target file path in RecordingDownloader
`RecordingDownloader(cookies: list[CookieData], sharepoint_personal_host: str, rate_limiter: AsyncRateLimiter)` in `src/idat_sync/infrastructure/downloaders.py` SHALL store `self.rate_limiter = rate_limiter`, and `async def download(self, content: RecordingContent) -> None` SHALL enter `async with self.rate_limiter:` and unconditionally treat `content.path` as the target file path by creating `Path(content.path).parent` (using Windows `\\?\` extended-length normalization before `mkdir(parents=True, exist_ok=True)`), setting `yt_dlp` `outtmpl` to `f"{prefix}{os.path.abspath(Path(content.path))}"`, passing a `progress_hook` checked against a per-download `threading.Event` that raises `yt_dlp.utils.DownloadCancelled` when set, offloading `YoutubeDL(ydl_opts).download([content.url])` via `await asyncio.to_thread(...)`, catching `asyncio.CancelledError` to set the `threading.Event` before re-raising, translating `401`/`403` `yt_dlp.utils.DownloadError` to `CredentialsExpiredError(str(exc)) from exc`, translating non-auth `yt_dlp.utils.DownloadError` to `RemoteServiceError(str(exc)) from exc`, and translating `OSError` to `FileSystemError(...) from exc`. `RecordingDownloader.download` MUST NOT branch on `Path(content.path).suffix` or append `"%(title)s %(id)s.%(ext)s"`. Verified at the `RecordingDownloader` (`src/idat_sync/infrastructure/downloaders.py`) public seam.

#### Scenario: Downloading a recording with a file extension
- **WHEN** `await RecordingDownloader.download(content)` is called with `RecordingContent(Path("Grabaciones/Semana 1/video.mp4"), 1700000000, url)`
- **THEN** it enters `async with self.rate_limiter:`, creates the parent directory `Grabaciones/Semana 1`, and passes `f"{prefix}{os.path.abspath(Path('Grabaciones/Semana 1/video.mp4'))}"` as `outtmpl` to `yt_dlp.YoutubeDL` inside `await asyncio.to_thread(...)`

#### Scenario: Downloading a recording without a file extension in path
- **WHEN** `await RecordingDownloader.download(content)` is called with a `RecordingContent` whose `path` has no file suffix (`Path(content.path).suffix == ""`)
- **THEN** it still creates `Path(content.path).parent` (not `Path(content.path)` as a directory) and passes `f"{prefix}{os.path.abspath(Path(content.path))}"` as `outtmpl` without appending `"%(title)s %(id)s.%(ext)s"`

#### Scenario: Aborting worker thread download on asyncio task cancellation
- **WHEN** an active `RecordingDownloader.download(content)` task is cancelled via `asyncio.CancelledError` while `yt_dlp` is downloading in `asyncio.to_thread(...)`
- **THEN** the per-download `threading.Event` is set immediately before `CancelledError` propagates, and invoking the registered `progress_hook` raises `yt_dlp.utils.DownloadCancelled`

#### Scenario: Translating DownloadError and OSError in RecordingDownloader
- **WHEN** `RecordingDownloader.download(content)` encounters a `401`/`403` `DownloadError`, a non-auth `DownloadError`, or an `OSError` (such as directory creation failure)
- **THEN** it raises `CredentialsExpiredError(str(exc)) from exc`, `RemoteServiceError(str(exc)) from exc`, or `FileSystemError(...) from exc` respectively

### Requirement: Client-owned file download streaming and SharePoint SDK query execution
`MoodleClient` SHALL reuse a single shared `httpx.AsyncClient` across `call_ws` and `download_file` and expose `async def aclose(self) -> None`, `__aenter__`, and `__aexit__` closing the shared client (`MoodleTokenClient` retains single-call `async with httpx.AsyncClient()`). `MoodleClient` and `SharepointClient` SHALL provide `async def download_file(..., dest: BinaryIO) -> None` guarded by `async with self.rate_limiter:`, with `MoodleClient.download_file` merging `wstoken` and `token` into existing query parameters, entering `async with self.rate_limiter:` around `async with self._client.stream(...)` so the HTTP stream exits before `self.rate_limiter.__aexit__` stamps `_next_allowed`, stripping `wstoken` and `token` query params on `httpx.HTTPStatusError` or `httpx.RequestError`, and raising `RemoteServiceError(f"HTTP {response.status_code} for url '{safe_url}'") from None` or `RemoteServiceError(f"Network error downloading '{safe_url}'") from None`. `MoodleDownloader.download`, `SharepointDownloader.download`, and `StringDownloader.download` SHALL be `async def` coroutines, all three writing into a sibling `<filename>.part` temporary file using Windows `\\?\` extended-length path normalization at the OS boundary, atomically replacing `content.path` via `os.replace` on completion, unlinking the `.part` file (`missing_ok=True`) in `except BaseException:` if writing or `download_file` raises, and translating `OSError` into `FileSystemError(...) from exc` (`StringDownloader` writing UTF-8 encoded content). `SharepointClient.__init__` SHALL be non-blocking (`self.search_client: ClientContext | None = None`), SHALL provide `cookies_for_host(self, host: str) -> dict[str, str]` filtering `FedAuth`, `rtFa`, and `SPOIDCRL` cookies strictly per target host by exact lowercase domain equality or proper leading-dot domain suffix (with exact-host precedence over leading-dot wildcard matches and never substring matching), and SHALL instantiate a single `self._transport = HttpxTransport()` reused across all `get_client(site_url)` calls. `SharepointClient` SHALL expose `async def aclose(self) -> None` awaiting `self._transport.aclose()` and `__aenter__` / `__aexit__` delegating to `aclose()`, and `@classmethod async def create(..., rate_limiter: AsyncRateLimiter) -> Self` SHALL resolve `personal_site` via `await client._await_query(client.personal_client.web.current_user.get_personal_site().execute_query_async())`, calling `await client.aclose()` in `except BaseException:` before raising if initialization fails or is cancelled. `SharepointClient._await_query(self, coro: Awaitable[_ResultT]) -> _ResultT` SHALL wrap `await coro` inside `async with self.rate_limiter:` and close `coro` (`if isinstance(coro, Coroutine): coro.close()`) if `self.rate_limiter.__aenter__` raises before `await coro` executes. `get_client(site_url)` SHALL parse the target host from `site_url` and chain `.with_cookies(lambda host=target_host: self.cookies_for_host(host)).with_auto_retry()` and `ctx.pending_request().with_async_transport(self._transport)`. `get_client_from_url(url)` SHALL return a fresh `self.get_client(self._academic_site_url)` whenever `url` does not match `r"/(sites|teams|s|personal)/([^/]+)"` and MUST NOT return a shared mutable `ClientContext` on fallback so concurrent SharePoint URL resolution tasks never mutate the same pending query queue. `SharepointClient` SHALL execute async SDK queries directly (`ctx.execute_query_with_incremental_retry_async()` with `contextlib.suppress(TypeError)` inside the query coroutine passed to `_await_query` in `load_entity`, `await ...execute_query_async()` in `search_for_recordings`, `fetch_folder_files`, and `fetch_subfolders`, `await doc_lib.items.select(["File/Name", "File/ServerRelativeUrl", "File/TimeCreated", "File/TimeLastModified"]).expand(["File"]).get_all_async(page_size=50)` in `fetch_document_library_items`, and `await file.download_session_async(dest, use_path=False)` in `download_file`), and `SharepointApiAdapter.list_url_content` SHALL remain `async def -> list[SharepointContent]`, dispatch by URL path, and skip empty entities where `not entity.name and not entity.server_relative_url`. Verified at `MoodleClient`, `SharepointClient`, `SharepointApiAdapter`, and `*Downloader` seams.

#### Scenario: Streaming a Moodle file through MoodleClient
- **WHEN** `await MoodleClient.download_file(url, dest)` is called with a target URL (including query params) and writable binary stream
- **THEN** it merges `wstoken` and `token` into `url`'s query string, enters `async with self.rate_limiter:` around `self._client.stream("GET", ...)`, raises `CredentialsExpiredError("403 Forbidden: your session cookie is missing/expired.")` on HTTP `401` or `403`, raises token-redacted `RemoteServiceError(...) from None` on other `httpx.HTTPStatusError` or `httpx.RequestError`, writes non-empty chunks from `await response.aiter_bytes(chunk_size=1024 * 256)` into `dest`, closes the HTTP stream upon exiting `async with self._client.stream(...)`, and then exits `async with self.rate_limiter:`

#### Scenario: Reusing and closing shared httpx.AsyncClient in MoodleClient
- **WHEN** a `MoodleClient` instance executes multiple `call_ws` and `download_file` operations and then exits `async with client:` or awaits `await client.aclose()`
- **THEN** `httpx.AsyncClient` is instantiated once per `MoodleClient`, reused across all requests, and `aclose()` awaits `self._client.aclose()` while `MoodleTokenClient.request_token` retains its per-call `async with httpx.AsyncClient()`

#### Scenario: Delegating MoodleDownloader and SharepointDownloader file downloads to clients
- **WHEN** `await MoodleDownloader(moodle_client).download(moodle_content)` and `await SharepointDownloader(sharepoint_client).download(sp_content)` succeed vs when `download_file` or local `.part` filesystem operations fail (including targets > 259 characters on Windows)
- **THEN** each stores `self.moodle_client` or `self.sharepoint_client` without accepting `request_delay` or `rate_limiter`, creates `Path(content.path).parent`, streams into `<filename>.part` (using Windows `\\?\` prefix at the OS boundary), atomically moves the `.part` file to `content.path` via `os.replace` on success, removes the `.part` file without leaving a partial `content.path` on failure, and translates `OSError` into `FileSystemError(...) from exc` (where `SharepointClient.download_file` awaits `self._await_query(file.download_session_async(dest, use_path=False))`)

#### Scenario: Writing StringContent atomically via .part files in StringDownloader
- **WHEN** `await StringDownloader().download(string_content)` succeeds vs when writing fails mid-operation with `OSError`
- **THEN** it creates `Path(content.path).parent`, writes UTF-8 encoded content into `Path(content.path).with_name(f"{Path(content.path).name}.part")` via `_atomic_download`, atomically moves the `.part` file onto `content.path` via `os.replace` on success, and unlinks the `.part` file (`missing_ok=True`) and raises `FileSystemError(...) from exc` without corrupting an existing destination file on failure

#### Scenario: Isolating SharePoint cookies per target host with exact or leading-dot matching
- **WHEN** `SharepointClient` is created with cookies for `"intranet"`, `".sharepoint.com"`, `"evil.sharepoint.com.attacker.test"`, `"contoso.sharepoint.com"`, and `"contoso-my.sharepoint.com"` and `cookies_for_host(...)` or `get_client(site_url)` resolves cookies for each host
- **THEN** `cookies_for_host("intranet")` rejects `".sharepoint.com"` and `"evil.sharepoint.com.attacker.test"` while returning `intranet` exact matches, `"contoso.sharepoint.com"` never receives `"contoso-my.sharepoint.com"` cookies and rejects `"evil.sharepoint.com.attacker.test"`, exact host cookies take precedence over `".sharepoint.com"` suffix cookies regardless of input order, and `get_client(site_url)` supplies only cookies matching the parsed host of `site_url`

#### Scenario: Reusing and closing HttpxTransport in SharepointClient without onedrive_cookies
- **WHEN** `SharepointClient` is instantiated, `get_client` is called multiple times, and `async with client:` / `await client.aclose()` (or a failing/cancelled `await SharepointClient.create(...)`) runs
- **THEN** `HttpxTransport` is instantiated once per `SharepointClient` and passed to every `with_async_transport` call, and `await client.aclose()` / `__aexit__` (as well as a failing or cancelled `SharepointClient.create`, including when `rate_limiter.__aenter__` is cancelled before `get_personal_site` executes) awaits `self._transport.aclose()` and closes any unawaited query coroutine

#### Scenario: Creating a fresh ClientContext on fallback in SharepointClient.get_client_from_url
- **WHEN** `SharepointClient.get_client_from_url` is called twice with URLs lacking `/(sites|teams|s|personal)/` path segments
- **THEN** each call returns a distinct `ClientContext` targeting `academic_site_url.rstrip("/")` wired with the shared `self._transport`

#### Scenario: Delegating SharePoint guest URLs, direct links, and folder traversal to SharepointClient
- **WHEN** `await SharepointApiAdapter.list_url_content`, `await fetch_folder_contents`, and `await fetch_file_from_direct_link` resolve guest folder URLs (`^/:f:/`), guest file URLs (`^/:(\w+):/`) across `sharepoint_host` and `sharepoint_personal_host`, direct links with `uniqueid`/`sourcedoc`, and folder hierarchies
- **THEN** `SharepointApiAdapter` inspects `urlparse(url).path` without repeating the hostname check, awaits `sp_client.fetch_folder_by_guest_url`, `sp_client.fetch_file_by_guest_url`, and `sp_client.fetch_file_by_id(link, file_id)` (skipping the client call and returning `None` when neither `uniqueid` nor `sourcedoc` is present), and awaits `sp_client.fetch_folder_files(folder)` before `sp_client.fetch_subfolders(folder)`, while `SharepointClient.fetch_document_library_items` selects `["File/Name", "File/ServerRelativeUrl", "File/TimeCreated", "File/TimeLastModified"]` with `.expand(["File"])`, `SharepointClient.get_client` configures host-scoped `.with_cookies(...).with_auto_retry()` and `HttpxTransport()`, and `SharepointClient.get_client_from_url` matches `r"/(sites|teams|s|personal)/([^/]+)"` case-insensitively (mapping `/s/<name>` to `{base_url}/sites/<name}` and `/(sites|teams|personal)/<name>` to `{base_url}{path_match.group(0)}`)

#### Scenario: Skipping dead or expired SharePoint guest and direct links returning odata.null
- **WHEN** `await SharepointClient.load_entity(entity, ctx)` encounters `TypeError` because an `odata.null` guest URL response left `entity.resource_url` as `None`, or `await SharepointApiAdapter.list_url_content` receives a `Folder` or `File` where `not entity.name and not entity.server_relative_url`
- **THEN** `SharepointClient.load_entity` suppresses `TypeError` inside the coroutine passed to `_await_query` so `async with self.rate_limiter:` exits cleanly (`exc_type is None`) and stamps `_next_allowed` without any separate `asyncio.sleep` call (while translating `ClientRequestException` / `httpx.HTTPError` per `_await_query`), and `SharepointApiAdapter.list_url_content` returns `[]`

#### Scenario: Retrying SharePoint HTTP 429 and 503 with Retry-After backoff and re-raising on exhaustion
- **WHEN** `SharepointClient.get_client(site_url)` creates a `ClientContext` and `load_entity` executes a query
- **THEN** `get_client` chains host-scoped `.with_cookies(...).with_auto_retry()` and `ctx.pending_request().with_async_transport(self._transport)`, and `load_entity` awaits `ctx.execute_query_with_incremental_retry_async()` (`office365-rest-python-client` `v3.3.1` handles transient `429`/`503` retries and `Retry-After` headers natively)

### Requirement: ASCII compatibility and edge-case hardening in safe_filename
Per ADR-0002, `safe_filename(s, replacement=" ", max_len=100)` SHALL preserve ASCII NFKD normalization for top-level `Course` directories while checking `WINDOWS_RESERVED` against the stem before extensions and stripping trailing `[ .]+$` after `max_len` truncation. Verified at the `domain.path_utils` seam.

#### Scenario: Preserving ASCII course compatibility while fixing reserved stems and trailing dots
- **WHEN** `safe_filename` receives `"CON.txt"`, `"Lección 1: Diseño"`, or `"a" * 98 + "...b"` with `max_len=100`
- **THEN** it returns `"_CON.txt"`, `"Leccion 1 Diseno"`, and `"a" * 98` (with no trailing dot after truncation and length `<= 100`)

### Requirement: UTF-8 and extension-preserving segment sanitization in safe_path_segment
Per ADR-0002, `safe_path_segment(s, replacement=" ", max_len=100)` SHALL preserve UTF-8 accents and short file extensions (`1–15` characters after the last `.` with no whitespace) for `CourseSection`, `CourseModule`, and file names while stripping `<>:"/\|?*`, control characters `\x00-\x1F`/`\x7F`, `.`/`..`, post-truncation `[ .]+$`, and `WINDOWS_RESERVED` stems, and `safe_suffixed_segment(prefix, suffix, *, max_len=100)` SHALL truncate `prefix` directly within `max_len - len(suffix)` without letting `preserve_extension` truncate `suffix` or strip counters. Verified at the `domain.path_utils` seam.

#### Scenario: Preserving UTF-8 and extensions while making nested segments OS-safe
- **WHEN** `safe_path_segment` receives `"Lección 1: Diseño/Guía?.pdf"`, `"CON.txt"`, `".."`, or `"a" * 110 + ".mp4"` with `max_len=100`
- **THEN** it returns `"Lección 1 Diseño Guía.pdf"`, `"_CON.txt"`, `"untitled"`, and `"a" * 96 + ".mp4"` (length `<= 100`, UTF-8 and extension intact, no forbidden characters or trailing dots/spaces)

#### Scenario: Ignoring fake long or spaced extensions and preserving bounded suffixes
- **WHEN** `safe_path_segment` receives `"Publishing, M. (2007. " + "x" * 100` (`max_len=100`), or `safe_suffixed_segment("1", " (2)." + "x" * 98, max_len=100)` is called
- **THEN** `safe_path_segment` truncates to the 100-character prefix without chopping `"Publishing, M"` down to preserve a multi-word or `> 15`-character tail, and `safe_suffixed_segment` returns a segment of length `<= 100` without dropping `" (2)"`

### Requirement: Traversal-safe path containment in Content.in_folder
`Content.in_folder(folder)` in `src/idat_sync/domain/content.py` SHALL fail closed with `ValueError` whenever `self.path` is anchored/absolute (`is_absolute()` or non-empty `.anchor`) or contains `..` in `.parts`, or whenever `folder` contains `..` in `.parts`, ensuring relative paths MUST NOT escape `course_dir`. Verified at the `Content.in_folder` seam.

#### Scenario: Rejecting anchored paths and parent traversal segments
- **WHEN** `Content(Path("/etc/passwd"), 0).in_folder("Course")`, `Content(Path("../secret.txt"), 0).in_folder("")`, or `Content(Path("file.pdf"), 0).in_folder("../outside")` is called
- **THEN** each call raises `ValueError` instead of discarding `folder` or resolving above `course_dir`

#### Scenario: Allowing an absolute base course directory with safe relative content paths
- **WHEN** `Content(Path("file.pdf"), 0).in_folder(Path("/tmp/Course"))` is called
- **THEN** it returns a `Content` whose `path` equals `Path("/tmp/Course/file.pdf")`

### Requirement: Credentials expiration mapping in Moodle, SharePoint, and recording layers
`MoodleClient.call_ws`, `MoodleClient.download_file`, `SharepointClient.create` (and `SharepointClient.search_for_recordings` when `self.search_client is None`), `SharepointClient._await_query`, and `RecordingDownloader.download` SHALL map Moodle HTTP `401`/`403`, Moodle `errorcode == "invalidtoken"`, SharePoint `get_personal_site()` HTTP `401`/`403` or empty `personal_site.url`, uninitialized `self.search_client is None`, and recording HTTP `401`/`403` into `CredentialsExpiredError`, and SHALL translate all non-auth HTTP, WS, SDK, JSON, and `yt-dlp` failures (`MoodleClient.call_ws` Moodle `"exception"` dicts, `httpx.HTTPStatusError` without MDN URLs, `httpx.RequestError`, JSON `ValueError`; `MoodleClient.download_file` non-auth `httpx.HTTPStatusError`/`httpx.RequestError`; `SharepointClient._await_query` non-auth `ClientRequestException` and `httpx.HTTPError`; and `RecordingDownloader.download` non-auth `yt_dlp.utils.DownloadError`) into `RemoteServiceError`. Verified at the `MoodleClient`, `SharepointClient`, and `RecordingDownloader` seams.

#### Scenario: Mapping Moodle invalidtoken and HTTP 401 or 403 to CredentialsExpiredError
- **WHEN** `await MoodleClient.call_ws` receives `{"exception": "moodle_exception", "errorcode": "invalidtoken", ...}` or HTTP `401`/`403` vs `{"exception": "moodle_exception", "errorcode": "dmlreadexception", ...}`, HTTP `500`, `httpx.RequestError`, or invalid JSON, or `await MoodleClient.download_file` receives HTTP `401`/`403` vs `404`/`500` or `httpx.RequestError`
- **THEN** auth failures raise `CredentialsExpiredError` (which subclasses `IDATSyncError` and not `RuntimeError`), while non-auth Moodle WS, HTTP status, network, and JSON errors raise `RemoteServiceError` (stripping `httpx`'s MDN footer in `call_ws` and stripping `wstoken`/`token` params with `from None` in `download_file`)

#### Scenario: Mapping SharePoint and recording auth failures to CredentialsExpiredError
- **WHEN** `await SharepointClient.create(...)` observes empty `personal_site.url` or `ClientRequestException` with HTTP `401`/`403` from `get_personal_site().execute_query_async()`, `await client.search_for_recordings(...)` is called when `client.search_client is None`, `SharepointClient._await_query` catches non-auth `ClientRequestException` or `httpx.HTTPError`, or `await RecordingDownloader.download` fails with a `401`/`403` vs non-auth `DownloadError`
- **THEN** auth failures raise `CredentialsExpiredError` and non-auth SharePoint SDK/HTTP and `yt-dlp` errors raise `RemoteServiceError(...) from exc`

### Requirement: Per-item failure isolation and early credential abort in TaskOrchestrator
`TaskOrchestrator(task_group: list[list[Downloader]])` in `src/idat_sync/application/task_orchestrator.py` SHALL accept only `task_group` and SHALL NOT import `idat_sync.application.events` or `threading`. `TaskOrchestrator` SHALL expose `create_channel(self) -> TaskChannel`, returning a per-run `TaskChannel` that owns one unbounded `asyncio.Queue[DownloadTask | None]` per group in `self.task_group` and exposes `enqueue(self, task: DownloadTask) -> None` (routing by `task.downloader` and raising `ValueError` for an unregistered downloader), `close(self) -> None` (idempotently pushing `None` sentinels into every group queue so workers drain remaining tasks and exit), and `create_workers(self, run_task: Callable[[DownloadTask], Awaitable[None]] | None = None) -> list[Coroutine[object, object, None]]` (returning one sequential FIFO worker coroutine per group awaiting `run_task(task)` — or `task.download()` by default — until receiving `None`). Verified at the `TaskOrchestrator` (`src/idat_sync/application/task_orchestrator.py`) seam.

#### Scenario: Continuing remaining tasks after a single-item download failure
- **WHEN** `channel = TaskOrchestrator([[dl_a, dl_b], [dl_c]]).create_channel()` starts `create_workers(run_task)` concurrently with a producer calling `channel.enqueue(...)` for `dl_a`, `dl_b`, and `dl_c` followed by `channel.close()` (including repeated `channel.close()` calls)
- **THEN** `dl_a` and `dl_b` tasks execute sequentially in FIFO order inside the first group worker while `dl_c` executes concurrently in the second group worker, and repeated `channel.close()` calls are safe no-ops

#### Scenario: Aborting all remaining tasks on CredentialsExpiredError
- **WHEN** `channel.enqueue(task)` is called with a `Downloader` absent from `task_group`, or `run_task` raises an unhandled exception (such as `CredentialsExpiredError`) inside a worker coroutine returned by `channel.create_workers(run_task)`
- **THEN** `enqueue` raises `ValueError` immediately, and an unhandled worker exception stops subsequent tasks in that group and propagates to the caller awaiting or gathering the workers

### Requirement: Per-course failure isolation and early credential abort in IDATSync
`IDATSync` in `src/idat_sync/application/sync_service.py` SHALL expose `async def list_courses(self) -> list[Course]`, `async def sync_course(self, course: Course, course_dir: str | Path) -> AsyncIterator[SyncEvent]`, and `async def sync_courses(self, root_folder: str | Path, courses: Sequence[Course], *, refresh: bool = False) -> AsyncIterator[SyncEvent]`, calling `log_sync_event(event)` before yielding each `SyncEvent`. `sync_course` SHALL emit `CourseSyncStarted(course, course_path)`, perform pre-stream setup (`populate_course_sections` when `course.sections` is empty, `ensure_dir(course_path)`, `ensure_dir(recordings_folder)`, and `local_mtimes = file_system.scan_mtimes(course_path)`, catching `IDATSyncError` to emit `CourseSyncCompleted(course, exc)` and re-raise), then run discovery and group download workers concurrently inside `asyncio.TaskGroup`: running `course_repository.stream_course_content(course, course_path)` and `course_repository.get_recordings(course.name, recordings_folder)` concurrently, isolating non-auth `RemoteServiceError` from `get_recordings` by logging it immediately at `logging.ERROR` and appending to `errors` while letting `stream_course_content` and active downloads complete, disambiguating every discovered item's `path` via `PathDisambiguator`, skipping items where `download_manager.create_download_task(item)` is `None`, evaluating `download_manager.is_pending(item, local_mtimes)`, emitting `CourseFileDiscovered(course, item.path, is_pending)` strictly before calling `channel.enqueue(task)`, emitting `CourseDiscoveryCompleted(course, synced_count, pending_count)` and calling `channel.close()` once both discovery sources finish, continuing remaining tasks after per-task `(RemoteServiceError, FileSystemError)` (`TaskDownloadFailed`), cancelling all discovery tasks and sibling group workers immediately on `CredentialsExpiredError` (unwrapping `CredentialsExpiredError` from `ExceptionGroup`, emitting `CourseSyncCompleted(course, exc)`, and re-raising), and finally yielding `CourseSyncCompleted(course, errors[0] if errors else None)`. `sync_courses` SHALL remain sequential across `courses`, calling `self.course_repository.invalidate_cache()` once at the start of each sync pass, computing each `Course` folder via `Path(root_folder) / safe_filename(course.name)`, clearing `course.sections` when `refresh=True`, aborting remaining courses immediately on `CredentialsExpiredError`, and catching non-credential `IDATSyncError` per `Course` so later courses still sync. Verified at the `IDATSync` (`src/idat_sync/application/sync_service.py`) seam.

#### Scenario: Disambiguating case-insensitive and truncated leaf path collisions in get_course_remote_tree
- **WHEN** `[e async for e in IDATSync(...).sync_course(course, course_dir)]` receives streamed course and recording items containing three case-insensitively colliding paths (`"Course/Sec/Mod-[1]/Parte 1/guia.pdf"`, `"Course/Sec/Mod-[1]/parte 1/guia.pdf"`, `"Course/Sec/Mod-[1]/Parte 1/guia.pdf"`) or a second file whose 100-character name collides with an earlier file
- **THEN** `PathDisambiguator` keeps the first item's exact `path`, renames the second to `.../parte 1/guia (2).pdf` and the third to `.../Parte 1/guia (3).pdf` with every leaf segment `<= 100`, and emits distinct `CourseFileDiscovered` paths for all three

#### Scenario: Disambiguating long early-dot leaf path collisions without looping
- **WHEN** `[e async for e in IDATSync(...).sync_course(course, course_dir)]` receives two 100-character items named `"1." + "x" * 98` inside the same folder
- **THEN** the first item keeps its exact `path` and the second terminates immediately as a distinct path ending in `" (2)"` of length `<= 100`

#### Scenario: Overlapping discovery with downloads while guaranteeing FIFO event ordering
- **WHEN** `[e async for e in IDATSync(...).sync_course(course, course_dir)]` runs with a fast `StringContent` / `MoodleContent` stream, a slow SharePoint module, and `get_recordings`
- **THEN** fast pending items begin downloading (`TaskDownloadStarted` / `TaskDownloadCompleted`) before `CourseDiscoveryCompleted` fires, and every `CourseFileDiscovered(course, path, True)` precedes `TaskDownloadStarted(path)` for that path

#### Scenario: Isolating get_recordings RemoteServiceError mid-sync without aborting active downloads
- **WHEN** `course_repository.get_recordings` raises a non-auth `RemoteServiceError` during `IDATSync.sync_course` while `stream_course_content` items are being discovered and downloaded
- **THEN** the `RemoteServiceError` is logged immediately at `logging.ERROR`, remaining course content items still discover and download to completion, `CourseDiscoveryCompleted` is emitted, and `CourseSyncCompleted(course, exc)` reports the recorded error without cancelling active downloads

#### Scenario: Invalidating repository cache at the start of sync_courses
- **WHEN** `[e async for e in IDATSync.sync_courses(root_folder, [course1, course2])]` starts executing
- **THEN** `course_repository.invalidate_cache()` is called once before processing `course1` and `course2` sequentially

#### Scenario: Continuing remaining courses when one course fails
- **WHEN** `sync_course` raises a non-credential `IDATSyncError` (such as `RemoteServiceError` or `FileSystemError` during pre-stream setup) on the first `Course` while a second `Course` in `courses` succeeds
- **THEN** `[e async for e in IDATSync.sync_courses(root_folder, [course1, course2])]` yields `CourseSyncCompleted(course1, exc)`, still synchronizes the second `Course` and yields `CourseSyncCompleted(course2, None)`, and finishes without raising

#### Scenario: Aborting remaining courses on CredentialsExpiredError
- **WHEN** discovery (`stream_course_content` or `get_recordings`) or a `DownloadTask` raises `CredentialsExpiredError` inside `IDATSync.sync_course` or `IDATSync.sync_courses(root_folder, [course1, course2])`
- **THEN** sibling discovery tasks and group download workers are cancelled immediately, `TaskDownloadFailed(task.path, exc)` (if raised by a task) and `CourseSyncCompleted(course1, exc)` are logged and yielded, subsequent courses in `courses` MUST NOT run, and `CredentialsExpiredError` propagates immediately to the caller

### Requirement: Developer CLI extraction scripts and gitignored local test artifacts
`scripts/login_cli.py` and `scripts/moodle_json.py` SHALL reside under `scripts/` (without root copies), and `.gitignore` SHALL list `all_courses_contents.json` and `session*.json`. `scripts/login_cli.py` SHALL catch `IDATSyncError as exc` around `asyncio.run(auth_service.login(username, password, _get_mfa_code))`, print `f"Login failed: {exc}"` to `sys.stderr`, and return exit status `1`, otherwise saving `Credentials` to `config.session_file`. `scripts/moodle_json.py` SHALL define `async def main() -> int` invoked via `asyncio.run(main())`, loading `Credentials` from `JsonCredentialStore(config.session_file)` when present or prompting for `Username:` and `Password:` and awaiting `MoodleTokenAdapter(MoodleTokenClient(config.moodle_base_url)).fetch_token(username, password)` (catching `IDATSyncError as exc`, printing `f"Login failed: {exc}"` to `sys.stderr`, and returning `None` so `main()` exits with status `1`) before running `async with MoodleClient(token, base_url=config.moodle_base_url, rate_limiter=AsyncRateLimiter(config.request_delay)) as client:` to await `client.fetch_enrolled_courses()`, `client.fetch_assignments()`, and `client.fetch_course_contents(int(course["id"]))` and write `Path("./all_courses_contents.json")`. Verified at the `scripts/login_cli.py`, `scripts/moodle_json.py`, and `.gitignore` seams.

#### Scenario: Extracting courses, contents, and assignments to all_courses_contents.json
- **WHEN** `scripts/moodle_json.py` runs (reusing `Credentials` from `JsonCredentialStore(config.session_file)` when present, or prompting for `Username:` and `Password:` and awaiting `MoodleTokenAdapter.fetch_token(username, password)` when absent)
- **THEN** it obtains a token (or catches `IDATSyncError` and returns exit code `1` on rejected credentials or remote service error), enters `async with MoodleClient(token, base_url=config.moodle_base_url, rate_limiter=AsyncRateLimiter(config.request_delay)) as client:` (closing `client` on exit), awaits `fetch_enrolled_courses()`, `fetch_assignments()` (indexed by `int(course["id"])`), and `fetch_course_contents(int(course["id"]))`, and writes UTF-8 JSON (`indent=2, ensure_ascii=False`) with exact keys `{"course": course, "contents": ..., "assignments": ...}` per course directly to `Path("./all_courses_contents.json")` without dumping JSON to stdout

#### Scenario: Keeping extracted curriculum and Credentials files gitignored
- **WHEN** `.gitignore` and the repository layout are inspected or `scripts/login_cli.py` catches `IDATSyncError` from `auth_service.login(...)`
- **THEN** `.gitignore` contains `all_courses_contents.json` and `session*.json`, `scripts/login_cli.py` prints `f"Login failed: {exc}"` to `sys.stderr` and returns exit status `1`, and root `login_cli.py` and `moodle_json.py` do not exist

### Requirement: Conditional real-data offline fixture and live Credentials smoke testing
`tests/test_real_data.py` SHALL define `TestRealCourseContentsFixture` and `TestLiveCredentialsIntegration` guarded by `@unittest.skipUnless` on `./all_courses_contents.json` and `./session.json`, with zero hardcoded course IDs, teacher names, or share tokens, executing async repository, client, adapter, and downloader methods on `asyncio`. Verified at the `MoodleAdapter`, `SharepointApiAdapter`, `CourseRepository`, `domain.sync`, `domain.path_utils`, `JsonCredentialStore`, and `MoodleDownloader` seams.

#### Scenario: Running offline fixture tests against all_courses_contents.json
- **WHEN** `TestRealCourseContentsFixture` runs with `./all_courses_contents.json` present (or skips via `@unittest.skipUnless(Path("./all_courses_contents.json").is_file(), "Requires ./all_courses_contents.json")` when absent)
- **THEN** it feeds `"course"`, `"contents"`, and `"assignments"` (defaulting missing `"assignments"` to `[]`) through `await CourseRepository.list_courses()`, `await CourseRepository.populate_course_sections(course)`, and `async for item in CourseRepository.stream_course_content(course, course_dir):`, verifies `get_in_progress_course_ids(courses)` is non-empty, verifies every emitted item is a domain `Content` (`StringContent`, `MoodleContent`, or `SharepointContent`) safely inside its `course_dir`, verifies `"modname": "assign"` modules with matching `"assignments"` emit `-[id]-intro.html` `StringContent` items and `introattachments` `ModuleContent` items, verifies both `sharepoint_host` and `sharepoint_personal_host` URLs route to `SharepointApiAdapter`, verifies `PathDisambiguator` yields unique case-insensitive paths, and verifies `is_content_pending(item, {})` returns `True` for every resolved item

#### Scenario: Running live Credentials smoke tests against session.json and all_courses_contents.json
- **WHEN** `TestLiveCredentialsIntegration` runs with both `./all_courses_contents.json` and `./session.json` present (or skips via `@unittest.skipUnless(Path("./all_courses_contents.json").is_file() and Path("./session.json").is_file(), "Requires ./all_courses_contents.json and ./session.json")` when absent or when `JsonCredentialStore(Path("./session.json")).load()` returns `None`)
- **THEN** it initializes `MoodleClient` and `await SharepointClient.create(...)` with `rate_limiter=AsyncRateLimiter(0.0)`, dynamically discovers the first matching URL from `./all_courses_contents.json` for each category (1 Moodle `/pluginfile.php/` URL, 1 `:b:` guest file URL on `sharepoint_host`, 1 `:f:` guest folder URL on `sharepoint_host`, 1 direct link with `uniqueid=`/`sourcedoc=` on `sharepoint_host`, and 1 `:b:` guest file URL on `sharepoint_personal_host`), downloads the Moodle file into a `tempfile.TemporaryDirectory()` via `await MoodleDownloader.download` verifying a non-empty file on disk, and resolves the 4 SharePoint URLs via `await SharepointApiAdapter.list_url_content` into non-empty `list[SharepointContent]` (failing immediately if `CredentialsExpiredError` is raised)

### Requirement: Academic term category and in-progress course detection
`Course` in `src/idat_sync/domain/course.py` SHALL define `category: str = ""`, and `get_in_progress_course_ids(courses: Sequence[Course]) -> set[int]` SHALL parse `r"(\d{4})-(I{1,3}|IV)([A-Z]?)$"` from `course.category.strip()` (falling back to the first whitespace token of `course.name`), rank terms by `(int(year), roman_value, suffix)` where `I=1, II=2, III=3, IV=4` and `"" < "A" < "B"`, and return `id`s matching the maximum term (`set()` if none match). Verified at `domain.course` seam.

#### Scenario: Ranking academic terms across categories and split sections
- **WHEN** `get_in_progress_course_ids` receives courses with categories `"I.01.2025-IIA"`, `"I.03.2025-IIA"`, `"II.01.2025-III"`, `"III.01.2026-I"`, `"III.01.2026-II"`, and `"IV.01.2026-III"`
- **THEN** it returns the set of `id`s belonging to `"IV.01.2026-III"` (or both `"I.01.2025-IIA"` and `"I.03.2025-IIA"` when `"2025-IIA"` is the highest term)

#### Scenario: Falling back to course name prefix or returning empty set when no term matches
- **WHEN** `get_in_progress_course_ids` receives courses with empty `category` and `name="IV.01.2026-III PATRONES"`, or courses where neither `category` nor `name` contains a valid `<YEAR>-<ROMAN><SUFFIX>` token
- **THEN** it extracts `(2026, 3, "")` from the leading token of `name` in the first case, and returns `set()` in the second case

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

### Requirement: Structured sync progress reporting and actionable file filtering
`src/idat_sync/application/events.py` SHALL define frozen dataclasses `CourseSyncStarted(course: Course, course_dir: Path)`, `CourseFileDiscovered(course: Course, path: Path, is_pending: bool)`, `CourseDiscoveryCompleted(course: Course, synced_count: int, pending_count: int)`, `TaskDownloadStarted(path: Path)`, `TaskDownloadCompleted(path: Path)`, `TaskDownloadFailed(path: Path, error: IDATSyncError)`, and `CourseSyncCompleted(course: Course, error: IDATSyncError | None = None)`, type alias `SyncEvent`, and `log_sync_event(event: SyncEvent) -> None` logging `CourseFileDiscovered` at `logging.DEBUG`, logging `CourseSyncStarted`, `CourseDiscoveryCompleted` (`"Course '%s': %d synced, %d pending."`), `TaskDownloadStarted`, `TaskDownloadCompleted`, and `CourseSyncCompleted(course, None)` at `logging.INFO`, and logging `TaskDownloadFailed` and `CourseSyncCompleted` with non-`None` `error` at `logging.ERROR` on `logging.getLogger("idat_sync")`. `IDATSync.sync_course` SHALL call `log_sync_event(event)` before yielding each `SyncEvent`, filter out non-downloadable items (such as empty `StringContent` items rejected by `StringDownloader.can_handle`) before emitting `CourseFileDiscovered` or counting them in `CourseDiscoveryCompleted`, and pass `errors[0] if errors else None` into `CourseSyncCompleted`. Verified at `idat_sync.application.events` (`src/idat_sync/application/events.py`) and `IDATSync` (`src/idat_sync/application/sync_service.py`) seams.

#### Scenario: Filtering non-downloadable empty StringContent items from reported course files
- **WHEN** `[e async for e in IDATSync(...).sync_course(course, course_dir)]` runs on a course containing a synced file, a downloadable missing file, and an empty `StringContent` summary rejected by `StringDownloader.can_handle`
- **THEN** the yielded events include `CourseSyncStarted(course, Path(course_dir))`, `CourseFileDiscovered(course, synced_file, False)`, `CourseFileDiscovered(course, missing_file, True)` (excluding the empty `StringContent` path), `CourseDiscoveryCompleted(course, 1, 1)`, `TaskDownloadStarted(missing_file)`, `TaskDownloadCompleted(missing_file)`, and `CourseSyncCompleted(course, None)`

#### Scenario: Reporting task failure and course error to SyncProgressPort
- **WHEN** a `DownloadTask` raises a non-credential `IDATSyncError` (such as `RemoteServiceError` or `FileSystemError`) during `IDATSync.sync_course`
- **THEN** `TaskDownloadFailed(task.path, exc)` and `CourseSyncCompleted(course, exc)` are logged via `log_sync_event` at `logging.ERROR` and yielded from the async generator, remaining tasks still execute, and `log_sync_event` logs `CourseFileDiscovered` at `logging.DEBUG` and `CourseSyncStarted`, `CourseDiscoveryCompleted`, `TaskDownloadStarted`, `TaskDownloadCompleted`, and `CourseSyncCompleted(course, None)` at `logging.INFO` on `logging.getLogger("idat_sync")`

### Requirement: Treeview course selection UI and ProgressInterceptor removal
`src/idat_sync/ui/app.py` MUST NOT reassign `sys.stdout` or `sys.stderr`, and `idat_sync.main.main()` SHALL invoke `configure_logging(config.log_file)`. `SyncFrame` in `src/idat_sync/ui/sync_frame.py` MUST NOT call `loop.call_soon_threadsafe` or `asyncio.to_thread`. `SyncFrame` SHALL await `await create_sync_service(self.credentials, self.config)`, `await self.idat_sync.list_courses()`, and `async for event in self.idat_sync.sync_courses(path, selected, refresh=True): self._handle_event(event)` directly on the UI event loop, delegating tree rendering, `RowStatus` (`StrEnum`, including `RowStatus.FETCHING_AND_SYNCING = "Fetching & Syncing"`), `Progress`, `Icons` (`src/idat_sync/ui/icons.py`), and per-course `_CourseNodes` tracking to `CourseTree` (`src/idat_sync/ui/course_tree.py`), while `IDATSync.sync_courses(..., refresh=True)` clears `course.sections.clear()` on each passed `Course` so repeat `Sync` runs refresh Moodle sections without recreating the active `IDATSync` service, and `SyncFrame` sets `status_var` to `"Sync completed with errors."` when any yielded `CourseSyncCompleted` has `error is not None` or `"Sync done."` when all checked courses succeed. `CourseTree` SHALL expose `start_course(course_id, course_dir)`, `add_file(course_id, path, *, is_pending: bool)`, `finish_discovery(course_id)`, `set_file_status(path, status)`, and `set_course_status(course_id, status)`. `CourseTree` SHALL render a `ttk.Treeview` (`#0` `Name`, `status`, `progress`) with checkbox, folder, and file icons, `SyncFrame` SHALL provide selection buttons (`In Progress`, `Select All`, `Clear`) and `status_var`, ordering in-progress courses first (checked by default) on startup and transitioning course rows from `(RowStatus.FETCHING, "...")` -> `(RowStatus.FETCHING_AND_SYNCING, f"{done}/{total}+")` while discovering (including when `TaskDownloadCompleted` fires before `finish_discovery`) -> `(RowStatus.SYNCING, f"{int(done * 100 / total)}%")` (`"100%"` when `total == 0`) on `finish_discovery` -> `(RowStatus.COMPLETED, "100%")` or `(RowStatus.FAILED, current_progress)` on `CourseSyncCompleted`. Verified at `MainApp`, `idat_sync.main.main`, `CourseTree` (`src/idat_sync/ui/course_tree.py`), `IDATSync` (`src/idat_sync/application/sync_service.py`), and `SyncFrame` (`src/idat_sync/ui/sync_frame.py`) seams.

#### Scenario: Ordering in-progress courses first and pre-selecting them on startup
- **WHEN** `await SyncFrame.load_idat_sync(credentials)` loads courses from `await idat_sync.list_courses()` containing past and in-progress courses
- **THEN** `SyncFrame` delegates to `CourseTree` to insert in-progress courses first in the `ttk.Treeview` with a checked icon and past courses after with an unchecked icon, and enables `sync_button` only when `not is_syncing`, `folder_path` is non-empty, and at least one course is checked (or when courses have not loaded yet after a startup error)

#### Scenario: Updating parent course and child file rows during sync
- **WHEN** `await SyncFrame.do_sync()` runs on checked courses receiving `CourseSyncStarted`, `CourseFileDiscovered`, early `TaskDownloadStarted`/`TaskDownloadCompleted` before `CourseDiscoveryCompleted`, `CourseDiscoveryCompleted`, and `CourseSyncCompleted`
- **THEN** only checked courses reset to `(RowStatus.QUEUED, "0%")`, `start_course` sets `(RowStatus.FETCHING, "...")`, `add_file` expands the course and inserts nested folder and leaf file rows (`Synced` `"100%"` or `Pending` `"0%"`) with parent row `(RowStatus.FETCHING_AND_SYNCING, f"{done}/{total}+")`, `TaskDownloadCompleted` while discovering increments `done` while keeping `(RowStatus.FETCHING_AND_SYNCING, f"{done}/{total}+")`, `finish_discovery` switches the parent row to `(RowStatus.SYNCING, f"{int(done * 100 / total)}%")` (`"100%"` on an empty course with `0` files), and `CourseSyncCompleted` marks `(RowStatus.COMPLETED, "100%")` or `(RowStatus.FAILED, current_progress)`

#### Scenario: Reporting completion error status in SyncFrame
- **WHEN** `await SyncFrame.do_sync()` finishes iterating `sync_courses` after at least one `CourseSyncCompleted` had `error is not None` vs when all had `error is None`
- **THEN** `status_var` is set to `"Sync completed with errors."` when any course had an error and `"Sync done."` when all succeeded

#### Scenario: Clearing selected course sections on repeat Sync without recreating IDATSync
- **WHEN** `await SyncFrame.do_sync()` runs a second time on checked courses with already-populated `sections` while unchecked courses retain cached `sections`
- **THEN** `IDATSync.sync_courses(path, selected, refresh=True)` clears `Course.sections` for checked courses, unchecked `Course.sections` remain untouched, and `create_sync_service` is not invoked again while `self.idat_sync` is already active

### Requirement: Paginated enrolled-course retrieval in MoodleClient
`MoodleClient.fetch_enrolled_courses(self, classification: str = "all", limit: int = 100, offset: int = 0) -> list[dict]` SHALL page through `core_course_get_enrolled_courses_by_timeline_classification` from `offset` with page size `limit`, accumulating `courses` across advancing `nextoffset` values until `nextoffset` is absent, stops advancing, or a page is empty. Verified at `MoodleClient` (`src/idat_sync/infrastructure/moodle_client.py`).

#### Scenario: Returning single-page enrolled courses when nextoffset is absent
- **WHEN** `await MoodleClient.fetch_enrolled_courses()` receives `{"courses": [{"id": 101, "fullname": "Course 101"}]}` without `nextoffset`
- **THEN** `call_ws` is awaited once with `("core_course_get_enrolled_courses_by_timeline_classification", classification="all", limit=100, offset=0)` and returns `[{"id": 101, "fullname": "Course 101"}]`

#### Scenario: Fetching subsequent pages while nextoffset advances
- **WHEN** `await MoodleClient.fetch_enrolled_courses(classification="all", limit=2, offset=0)` receives `{"courses": [c1, c2], "nextoffset": 2}` on the first call, `{"courses": [c3], "nextoffset": 3}` on the second call, and `{"courses": [], "nextoffset": 3}` (or a payload omitting `nextoffset`) on the third call
- **THEN** `call_ws` is awaited across pages with advancing `offset` values (`0`, `2`, `3`) until an empty page or missing/non-advancing `nextoffset` stops pagination, returning `[c1, c2, c3]`

### Requirement: Pure per-item pending classification in domain.sync
`is_content_pending(content: Content, local_mtimes: Mapping[Path, int]) -> bool` in `src/idat_sync/domain/sync.py` (re-exported in `domain/__init__.py`) SHALL return `True` purely in memory when `content.path not in local_mtimes` or `content.last_modified > 0 and content.last_modified > local_mtimes[content.path]`, and `False` otherwise. Verified at `domain.sync`.

#### Scenario: Classifying missing, outdated, and synced items purely in memory
- **WHEN** `is_content_pending(content, local_mtimes)` is called for a path absent from `local_mtimes`, a path with `content.last_modified > local_mtimes[path]` (`> 0`), and a path with `content.last_modified <= local_mtimes[path]` or `0`
- **THEN** it returns `True` for the missing and outdated items and `False` for the synced item without performing disk I/O

### Requirement: Streaming case-insensitive path disambiguation in PathDisambiguator
`PathDisambiguator` in `src/idat_sync/domain/path_utils.py` (re-exported in `domain/__init__.py`) SHALL expose `disambiguate(path: Path) -> Path` keyed by `path.as_posix().casefold()` without importing `Content`. First occurrences SHALL stay unchanged; collisions SHALL rename the leaf via `split_extension` and `safe_suffixed_segment(stem, f" ({counter}){ext}")` from `counter = 2` (`<= 100` chars). Verified at `domain.path_utils`.

#### Scenario: Disambiguating case-insensitive and truncated leaf path collisions
- **WHEN** `PathDisambiguator().disambiguate(path)` receives `"Course/Sec/Mod-[1]/Parte 1/guia.pdf"`, `"Course/Sec/Mod-[1]/parte 1/guia.pdf"`, and `"Course/Sec/Mod-[1]/Parte 1/guia.pdf"`, or two 100-character filenames colliding in the same folder
- **THEN** the first keeps its exact `Path`, the second becomes `.../parte 1/guia (2).pdf`, the third becomes `.../Parte 1/guia (3).pdf`, and every leaf segment has length `<= 100`

#### Scenario: Disambiguating long early-dot leaf path collisions without looping
- **WHEN** `PathDisambiguator().disambiguate(path)` receives two 100-character paths whose leaf is `"1." + "x" * 98` in the same folder
- **THEN** the first keeps its exact `Path` and the second terminates immediately as a distinct path ending in `" (2)"` of length `<= 100`
