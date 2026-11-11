# Spec Delta

## Purpose

Synchronizes remote `Course` structures, inline HTML artifacts, external URLs, and class recordings into a local directory tree through inward-pointing hexagonal ports and adapters.

## ADDED Requirements

### Requirement: Strict inward package dependency boundary
The `src/idat_sync/domain/` and `src/idat_sync/application/` packages SHALL have zero imports from `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `office365`, `yt_dlp`, or `playwright`. `safe_filename` and `WINDOWS_RESERVED` SHALL reside in `src/idat_sync/domain/path_utils.py` (and be re-exported in `idat_sync.utils`). Verified at the architecture boundary seam and `domain.path_utils` seam.

#### Scenario: Inspecting domain and application imports
- **WHEN** all Python modules under `src/idat_sync/domain/` and `src/idat_sync/application/` are parsed for import statements
- **THEN** none of them import `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `office365`, `yt_dlp`, or `playwright`

#### Scenario: Sanitizing course filenames without importing infrastructure
- **WHEN** `safe_filename(s, replacement=" ", max_len=100)` in `src/idat_sync/domain/path_utils.py` is called with accents, forbidden characters, or Windows reserved names (`WINDOWS_RESERVED`)
- **THEN** it returns the normalized, safe ASCII filename of at most `max_len` characters

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
`SharepointApiAdapter` SHALL accept `(client: SharepointClient, semester_start: date = date(2025, 9, 22))` without instantiating `SharepointClient` from cookies in `__init__`. `fetch_recordings(course_name)` SHALL filter for files with both `time_created` and `serverRelativeUrl`, compute `week = 1 + (file.time_created.date() - self.semester_start).days // 7` per file, and return `list[RecordingContent]`. Verified at the `SharepointApiAdapter` public seam.

#### Scenario: Computing weekly folder and SharePoint stream URL per recording
- **WHEN** `SharepointApiAdapter.fetch_recordings(course_name)` finds recording files with `time_created` and `serverRelativeUrl`
- **THEN** it returns `RecordingContent(Path(f"Semana {week}") / name, last_mod, f"https://idat628-my.sharepoint.com/:v:/r{quote(file.serverRelativeUrl)}?csf=1&web=1")` for each recording

#### Scenario: Skipping recordings missing creation timestamp without shifting subsequent weeks
- **WHEN** `SharepointApiAdapter.fetch_recordings(course_name)` encounters a recording file with `time_created = None` before a valid recording file
- **THEN** the file missing `time_created` is excluded and the subsequent file's `week` is still computed from its own `time_created.date()`

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
`create_sync_service(credentials: Credentials) -> IDATSync` in `src/idat_sync/bootstrap.py` SHALL construct all concrete clients, adapters, `LocalFileSystemAdapter`, and rate-limit `Downloader` groups and inject them into `IDATSync(course_repository, download_manager, task_orchestrator, file_system)`. `IDATSync.__init__` MUST NOT instantiate clients or adapters from `Credentials`. Verified at the `bootstrap.create_sync_service` and `IDATSync` seams.

#### Scenario: Constructing IDATSync with injected collaborators
- **WHEN** `IDATSync(course_repository, download_manager, task_orchestrator, file_system)` is instantiated
- **THEN** it stores the injected collaborators without performing network or credential initialization in `__init__`
