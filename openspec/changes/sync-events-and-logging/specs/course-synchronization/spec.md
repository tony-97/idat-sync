# course-synchronization Specification

## MODIFIED Requirements

### Requirement: Strict inward package dependency boundary
The `src/idat_sync/domain/` and `src/idat_sync/application/` packages SHALL have zero imports from `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `office365`, `yt_dlp`, or `playwright`. `src/idat_sync/ui/` MUST NOT import `idat_sync.infrastructure`, `src/idat_sync/infrastructure/sharepoint_auth.py` SHALL be the sole module importing `playwright`, `src/idat_sync/infrastructure/auth.py` MUST NOT exist, `src/idat_sync/utils.py` MUST NOT exist, no module under `src/idat_sync/` SHALL call `print(...)`, and `safe_filename` and `WINDOWS_RESERVED` SHALL reside in `src/idat_sync/domain/path_utils.py`. Verified at the AST architecture boundary and `domain.path_utils` seams.

#### Scenario: Inspecting domain and application imports
- **WHEN** all Python modules under `src/idat_sync/domain/` and `src/idat_sync/application/` are parsed for import statements
- **THEN** none of them import `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `office365`, `yt_dlp`, or `playwright`

#### Scenario: Sanitizing course filenames without importing infrastructure
- **WHEN** `safe_filename(s, replacement=" ", max_len=100)` in `src/idat_sync/domain/path_utils.py` is called with accents, forbidden characters, or Windows reserved names (`WINDOWS_RESERVED`)
- **THEN** it returns the normalized, safe ASCII filename of at most `max_len` characters

#### Scenario: Enforcing ui and playwright isolation and legacy auth/utils removal
- **WHEN** all Python modules under `src/idat_sync/` are parsed via AST for imports and calls and `src/idat_sync/utils.py` and `src/idat_sync/infrastructure/auth.py` are checked
- **THEN** `src/idat_sync/utils.py` and `src/idat_sync/infrastructure/auth.py` do not exist, no module imports `idat_sync.utils` or `idat_sync.infrastructure.auth`, no module under `src/idat_sync/ui/` imports `idat_sync.infrastructure`, `src/idat_sync/infrastructure/sharepoint_auth.py` is the only module importing `playwright`, and zero modules under `src/idat_sync/` contain a `print(...)` call

### Requirement: Composition root wires production dependencies outside application
`create_sync_service(credentials: Credentials, config: SyncConfig, publish: EventPublisher = _noop) -> IDATSync` in `src/idat_sync/bootstrap.py` SHALL require an explicit `SyncConfig`, construct all clients, adapters, `LocalFileSystemAdapter`, and `Downloader` groups with `config` values, wire `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)` directly to the low-level clients without `request_delay`, compose a publisher that calls `log_sync_event(event)` followed by `publish(event)`, and inject that publisher into `TaskOrchestrator` and `IDATSync`. `configure_logging(log_file: Path) -> logging.Logger` in `src/idat_sync/bootstrap.py` SHALL configure `logging.getLogger("idat_sync")` at `logging.INFO` with `propagate = False`, closing and clearing existing handlers before attaching `logging.StreamHandler()` and `logging.FileHandler(log_file, mode="w", encoding="utf-8")`. `IDATSync.__init__` MUST NOT instantiate clients or adapters from `Credentials`. Verified at `bootstrap.create_sync_service`, `bootstrap.configure_logging`, and `IDATSync` seams.

#### Scenario: Constructing IDATSync with injected collaborators
- **WHEN** `IDATSync(course_repository, download_manager, task_orchestrator, file_system, recordings_folder_name="Videos", publish=custom_publish)` is instantiated and `sync_course(course, course_dir)` runs
- **THEN** it stores the injected collaborators and `publish` callable without network or credential initialization in `__init__` and ensures and syncs recordings under `Path(course_dir) / "Videos"`

#### Scenario: Wiring SyncConfig through create_sync_service
- **WHEN** `configure_logging(log_file)` and `create_sync_service(credentials, config=custom_config, publish=custom_publish)` are called
- **THEN** `configure_logging` configures `logging.getLogger("idat_sync")` at `logging.INFO` (`propagate == False`) with one `StreamHandler` and one `FileHandler` targeting `log_file`, and `create_sync_service` forwards `custom_config` fields to `MoodleClient`, `MoodleAdapter`, `SharepointClient`, `SharepointApiAdapter` (without `request_delay`), `RecordingDownloader`, and `IDATSync`, wires `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)`, and injects a composite publisher into `TaskOrchestrator` and `IDATSync` that invokes both `log_sync_event` and `custom_publish`

### Requirement: Per-item failure isolation and early credential abort in TaskOrchestrator
`TaskOrchestrator(task_group: list[list[Downloader]], publish: EventPublisher = _noop)` SHALL preserve sequential task execution order within each downloader group, publish `TaskDownloadStarted(task.path)` before each task and `TaskDownloadCompleted(task.path)` or `TaskDownloadFailed(task.path, exc)` after each task, return `list[Exception]` of non-credential task failures in execution order from `run(tasks)` and `run_async(tasks)`, and immediately abort remaining queued tasks across all downloader groups and propagate `CredentialsExpiredError`. `TaskOrchestrator.run` and `TaskOrchestrator.run_async` MUST NOT accept `on_task_start`, `on_task_complete`, or `on_task_error` parameters and MUST NOT call `print(...)`. Verified at the `TaskOrchestrator` seam.

#### Scenario: Continuing remaining tasks after a single-item download failure
- **WHEN** a `DownloadTask` raises a non-credential `Exception` (such as `requests.HTTPError` `404` or `OSError`) during `TaskOrchestrator.run(tasks)`
- **THEN** the orchestrator publishes `TaskDownloadStarted(task.path)` and `TaskDownloadFailed(task.path, exc)`, executes all subsequent tasks in that downloader group and sibling groups in order, and returns `[exc]` without raising

#### Scenario: Aborting all remaining tasks on CredentialsExpiredError
- **WHEN** any `DownloadTask` raises `CredentialsExpiredError` during `TaskOrchestrator.run(tasks)`
- **THEN** `TaskDownloadFailed(task.path, exc)` is published for the failing task, remaining unstarted tasks in that group and sibling groups MUST NOT run, and `CredentialsExpiredError` propagates to the caller

### Requirement: Per-course failure isolation and early credential abort in IDATSync
`IDATSync` SHALL accept `publish: EventPublisher = _noop` in `__init__`. `IDATSync.sync_courses(root_folder, courses: Sequence[Course]) -> None` and `IDATSync.sync_course(course: Course, course_dir: str | Path) -> SyncDiff` MUST NOT accept `progress_listener` and MUST NOT call `print(...)`. `sync_courses` SHALL require an explicit `courses` sequence, compute each `Course` folder via `Path(root_folder) / safe_filename(course.name)`, lazily populate `course.sections` when empty, publish `CourseSyncCompleted(course, exc)` and skip single-`Course` failures other than `CredentialsExpiredError` so later courses still sync, and abort remaining courses immediately on `CredentialsExpiredError`. Verified at the `IDATSync` seam.

#### Scenario: Continuing remaining courses when one course fails
- **WHEN** `sync_course` raises a non-credential error on the first `Course` while a second `Course` in `courses` succeeds
- **THEN** `IDATSync.sync_courses(root_folder, [course1, course2])` publishes `CourseSyncCompleted(course1, exc)`, still synchronizes the second `Course` and publishes `CourseSyncCompleted(course2, None)`, and finishes without raising

#### Scenario: Aborting remaining courses on CredentialsExpiredError
- **WHEN** the first `Course` raises `CredentialsExpiredError` inside `IDATSync.sync_courses(root_folder, [course1, course2])`
- **THEN** `CourseSyncCompleted(course1, exc)` is published, subsequent courses in `courses` MUST NOT be synchronized, and `CredentialsExpiredError` propagates immediately

### Requirement: Structured sync progress reporting and actionable file filtering
`src/idat_sync/application/events.py` SHALL define frozen dataclasses `CourseSyncStarted(course: Course)`, `CourseFilesDiscovered(course: Course, course_dir: Path, synced_paths: tuple[Path, ...], pending_paths: tuple[Path, ...])`, `TaskDownloadStarted(path: Path)`, `TaskDownloadCompleted(path: Path)`, `TaskDownloadFailed(path: Path, error: Exception)`, and `CourseSyncCompleted(course: Course, error: Exception | None = None)`, type aliases `SyncEvent` and `EventPublisher = Callable[[SyncEvent], None]`, and `log_sync_event(event: SyncEvent) -> None` logging progress/completion events at `logging.INFO` and failure events (`TaskDownloadFailed` and `CourseSyncCompleted` with non-`None` `error`) at `logging.ERROR` on `logging.getLogger("idat_sync")`. `SyncProgressPort` MUST NOT exist in `src/idat_sync/application/ports.py`. `IDATSync.sync_course` SHALL report only `tuple(sorted(diff.synced_paths))` and `tuple(t.path for t in tasks)` in `CourseFilesDiscovered`, excluding non-downloadable empty `StringContent` items, and pass `errors[0] if errors else None` from `TaskOrchestrator.run(tasks)` into `CourseSyncCompleted`. Verified at `idat_sync.application.events`, `IDATSync`, and `TaskOrchestrator` seams.

#### Scenario: Filtering non-downloadable empty StringContent items from reported course files
- **WHEN** `IDATSync(..., publish=events.append).sync_course(course, course_dir)` runs on a course containing a synced file, a downloadable missing file, and an empty `StringContent` summary rejected by `StringDownloader.can_handle`
- **THEN** `events` receives `CourseSyncStarted(course)`, `CourseFilesDiscovered(course, Path(course_dir), (synced_file,), (missing_file,))` (excluding the empty `StringContent` path), `TaskDownloadStarted(missing_file)`, `TaskDownloadCompleted(missing_file)`, and `CourseSyncCompleted(course, None)`, and `hasattr(ports, "SyncProgressPort")` is `False`

#### Scenario: Reporting task failure and course error to SyncProgressPort
- **WHEN** a `DownloadTask` raises a non-credential `Exception` during `IDATSync.sync_course` and emitted events are passed to `log_sync_event`
- **THEN** `TaskDownloadFailed(task.path, exc)` and `CourseSyncCompleted(course, exc)` are published, remaining tasks still execute, and `log_sync_event` logs `CourseSyncStarted`, `CourseFilesDiscovered`, `TaskDownloadStarted`, `TaskDownloadCompleted`, and `CourseSyncCompleted(course, None)` at `logging.INFO` and `TaskDownloadFailed` and `CourseSyncCompleted(course, exc)` at `logging.ERROR` on `logging.getLogger("idat_sync")`

### Requirement: Treeview course selection UI and ProgressInterceptor removal
`src/idat_sync/ui/app.py` MUST NOT define `ProgressInterceptor` and MUST NOT reassign `sys.stdout` or `sys.stderr`, and `ui.app.main()` SHALL invoke `configure_logging(config.log_file)`. `SyncFrame` in `src/idat_sync/ui/sync_frame.py` MUST NOT define `output_text` or `_TreeProgressListener`, SHALL pass `publish=lambda event: loop.call_soon_threadsafe(self.progress_queue.put_nowait, event)` into `create_sync_service`, and SHALL consume `SyncEvent` instances directly in `update_progress` while indexing `self._file_iids` by `Path -> (course_id, child_iid)`. `SyncFrame` SHALL render a `ttk.Treeview` (`#0` `Name`, `status`, `progress`) with checkbox, folder, and file icons, selection buttons (`In Progress`, `Select All`, `Clear`), and `status_var`, ordering in-progress courses first (checked by default) on startup and updating parent, nested folder, and leaf file rows (`0%`-`100%`) during sync. Verified at `MainApp`, `ui.app.main`, and `SyncFrame` seams.

#### Scenario: Ordering in-progress courses first and pre-selecting them on startup
- **WHEN** `SyncFrame.load_idat_sync` loads courses from `idat_sync.list_courses()` containing past and in-progress courses
- **THEN** `SyncFrame` inserts in-progress courses first in the `ttk.Treeview` with a checked icon and past courses after with an unchecked icon, and enables `sync_button` only when `not is_syncing`, `folder_path` is non-empty, and at least one course is checked (or when courses have not loaded yet after a startup error)

#### Scenario: Updating parent course and child file rows during sync
- **WHEN** `SyncFrame.do_sync()` runs on checked courses and `SyncEvent` instances arrive on `self.progress_queue`
- **THEN** `_TreeProgressListener` does not exist in `src/idat_sync/ui/sync_frame.py`, only checked courses are reset to `Status="Queued"`, `Progress="0%"` and passed to `idat_sync.sync_courses(path, selected_courses)`, nested folder nodes use a folder icon and leaf file nodes use a file icon with `Synced` (`100%`) or `Pending` (`0%`) -> `Downloading` (`0%`) -> `Completed` (`100%`) / `Failed` (`0%`), and parent course progress updates as `f"{int(done * 100 / total)}%"` (`"100%"` when `total == 0`)
