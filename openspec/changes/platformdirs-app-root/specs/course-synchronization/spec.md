# course-synchronization Specification Delta

## MODIFIED Requirements

### Requirement: Composition root wires production dependencies outside application
`async def create_sync_service(credentials: Credentials, config: SyncConfig) -> IDATSync` in `src/idat_sync/bootstrap.py` SHALL require an explicit `SyncConfig` without a `publish` parameter, construct `MoodleClient`, `MoodleAdapter`, await `await SharepointClient.create(...)` (awaiting `moodle_client.aclose()` before re-raising if `SharepointClient.create(...)` fails), construct `SharepointApiAdapter`, `LocalFileSystemAdapter`, and `Downloader` groups with `config` values, wire `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)` directly to the low-level clients without `request_delay`, wire `TaskOrchestrator(downloader_group)`, and inject the composed collaborators, `recordings_folder_name`, and `closers=[moodle_client.aclose, sharepoint_raw_client.aclose]` into `IDATSync`. `IDATSync.__init__` SHALL accept optional `closers: Sequence[Callable[[], Awaitable[None]]] = ()` without instantiating clients or adapters from `Credentials` and MUST NOT accept `publish`; `IDATSync` SHALL expose `async def aclose(self) -> None` (invoking every closer once, ensuring remaining closers still execute if an earlier closer fails, and re-raising the first exception if any) and `__aenter__` / `__aexit__` delegating to `aclose()`. `configure_logging(log_file: Path) -> logging.Logger` in `src/idat_sync/bootstrap.py` SHALL ensure `log_file.parent` exists (`mkdir(parents=True, exist_ok=True)`), configure `logging.getLogger("idat_sync")` at `logging.INFO` with `propagate = False`, and close and clear existing handlers before attaching `logging.StreamHandler()` and `logging.FileHandler(log_file, mode="w", encoding="utf-8")`. Verified at `bootstrap.create_sync_service`, `bootstrap.configure_logging`, and `IDATSync` (`src/idat_sync/application/sync_service.py`) seams.

#### Scenario: Constructing IDATSync with injected collaborators
- **WHEN** `IDATSync(course_repository, download_manager, task_orchestrator, file_system, recordings_folder_name="Videos")` is instantiated and `async for _ in sync.sync_course(course, course_dir):` runs
- **THEN** it stores the injected collaborators without network or credential initialization in `__init__`, rejects a `publish` keyword argument with `TypeError`, and ensures and syncs recordings under `Path(course_dir) / "Videos"`

#### Scenario: Closing injected closers deterministically via IDATSync.aclose
- **WHEN** `IDATSync(..., closers=[closer_a, closer_b])` exits `async with` or awaits `await sync.aclose()`
- **THEN** each closer is awaited once (`closer_b` still runs if `closer_a` raises, re-raising the first failure) and repeated `aclose()` calls are safe no-ops

#### Scenario: Wiring SyncConfig through create_sync_service
- **WHEN** `configure_logging(log_file)` and `await create_sync_service(credentials, config=custom_config)` are called
- **THEN** `configure_logging` configures `logging.getLogger("idat_sync")` at `logging.INFO` (`propagate == False`) with one `StreamHandler` and one `FileHandler` targeting `log_file`, `await create_sync_service` forwards `custom_config` fields to `MoodleClient`, `MoodleAdapter`, `await SharepointClient.create(...)`, `SharepointApiAdapter` (without `request_delay`), `RecordingDownloader`, `TaskOrchestrator`, and `IDATSync`, wires `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)` with `moodle_dl.moodle_client is moodle_adapter.client` and `sp_dl.sharepoint_client is sp_adapter.sp_client`, and `await idat_sync.aclose()` closes both `moodle_client` and `sharepoint_raw_client`

#### Scenario: Creating missing parent directories in configure_logging
- **WHEN** `configure_logging(Path(tmpdir) / "nested" / "logs" / "log.err")` is called before the parent directory exists
- **THEN** it creates `log_file.parent` and attaches the `FileHandler` targeting that file without raising `FileNotFoundError`

#### Scenario: Cleaning up MoodleClient when SharepointClient.create fails in create_sync_service
- **WHEN** `await SharepointClient.create(...)` raises during `await create_sync_service(credentials, config=custom_config)`
- **THEN** `moodle_client.aclose()` is awaited before the exception propagates

### Requirement: Runtime configuration loading with environment and JSON precedence
`load_config(root: str | Path = ".") -> SyncConfig` in `src/idat_sync/config.py` SHALL validate `root` as a non-empty path that is not an existing non-directory file (raising `ValueError` otherwise), resolve settings with precedence `IDAT_SYNC_<UPPER_FIELD_NAME>` env vars > JSON keys from `IDAT_SYNC_CONFIG` (raising `FileNotFoundError` when missing or `ValueError` when not a file) or optional `Path(root) / "config.json"` (falling back cleanly without raising when `root` or `Path(root) / "config.json"` does not exist) > `root` defaults (`session_file=Path(root) / "session.json"`, `log_file=Path(root) / "log.err"`, and remaining `SyncConfig` defaults). Unknown JSON keys, non-object JSON roots, boolean `request_delay`, or invalid `YYYY-MM-DD` dates SHALL raise `ValueError`. Verified at the `idat_sync.config.load_config` seam.

#### Scenario: Falling back to defaults when default config file is absent
- **WHEN** `load_config()` or `load_config(missing_or_empty_root)` is called with `IDAT_SYNC_CONFIG` unset and no `config.json` in `root`
- **THEN** it returns a `SyncConfig` instance populated with `session_file == Path(root) / "session.json"`, `log_file == Path(root) / "log.err"`, and built-in defaults (plus any set `IDAT_SYNC_<UPPER_FIELD_NAME>` environment variables) without raising `FileNotFoundError`

#### Scenario: Raising FileNotFoundError when an explicit config file is missing
- **WHEN** `IDAT_SYNC_CONFIG` specifies a path that does not exist on disk
- **THEN** `load_config` raises `FileNotFoundError`

#### Scenario: Rejecting blank or file root values in load_config
- **WHEN** `load_config("")`, `load_config("   ")`, or `load_config(existing_file_path)` is called
- **THEN** `load_config` raises `ValueError`

#### Scenario: Applying precedence across defaults, JSON file, and environment variables
- **WHEN** `Path(root) / "config.json"` (or `IDAT_SYNC_CONFIG`) defines `{"semester_start": "2026-03-16", "request_delay": 5}` without overriding `session_file` or `log_file`, and the environment defines `IDAT_SYNC_REQUEST_DELAY="1.5"` and `IDAT_SYNC_RECORDINGS_FOLDER_NAME="Videos"`
- **THEN** `load_config(root)` returns `SyncConfig` with `semester_start == date(2026, 3, 16)`, `request_delay == 1.5`, `recordings_folder_name == "Videos"`, `session_file == Path(root) / "session.json"`, and `log_file == Path(root) / "log.err"`

#### Scenario: Rejecting unknown JSON keys and invalid types in load_config
- **WHEN** `load_config` reads a JSON file with unknown keys, a non-object root, `request_delay: true`, or an invalid `semester_start` date string
- **THEN** `load_config` raises `ValueError`

### Requirement: Treeview course selection UI and ProgressInterceptor removal
`src/idat_sync/ui/app.py` MUST NOT define `ProgressInterceptor` and MUST NOT reassign `sys.stdout` or `sys.stderr`, and `ui.app.main()` SHALL invoke `load_config(user_data_path("idat-sync", appauthor=False))` (with `"platformdirs >= 2.1"` declared in `pyproject.toml`) and `configure_logging(config.log_file)`. `SyncFrame` in `src/idat_sync/ui/sync_frame.py` MUST NOT define `output_text`, `_TreeProgressListener`, `progress_queue`, `update_progress`, `exit`, or `_queue_error`, and MUST NOT call `loop.call_soon_threadsafe` or `asyncio.to_thread`. `SyncFrame` SHALL await `await create_sync_service(self.credentials, self.config)`, `await self.idat_sync.list_courses()`, and `async for event in self.idat_sync.sync_courses(path, selected): self._handle_event(event)` directly on the UI event loop while indexing `self._file_iids` by `Path -> (course_id, child_iid)`, clearing `course.sections.clear()` on each checked `Course` in `do_sync()` before iterating `sync_courses(path, selected)` so repeat `Sync` runs refresh Moodle sections without recreating the active `IDATSync` service, and setting `status_var` to `"Sync completed with errors."` when any yielded `CourseSyncCompleted` has `error is not None` or `"Sync done."` when all checked courses succeed. `SyncFrame` SHALL render a `ttk.Treeview` (`#0` `Name`, `status`, `progress`) with checkbox, folder, and file icons, selection buttons (`In Progress`, `Select All`, `Clear`), and `status_var`, ordering in-progress courses first (checked by default) on startup and updating parent, nested folder, and leaf file rows (`0%`-`100%`) during sync. Verified at `MainApp`, `ui.app.main`, and `SyncFrame` (`src/idat_sync/ui/sync_frame.py`) seams.

#### Scenario: Passing platformdirs user data root from ui.app.main
- **WHEN** `ui.app.main()` runs
- **THEN** it invokes `load_config(user_data_path("idat-sync", appauthor=False))` (without creating the directory inside `user_data_path`), passes `config.log_file` to `configure_logging`, and runs `MainApp(config=config)`

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
