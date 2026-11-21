# course-synchronization Specification Delta

## MODIFIED Requirements

### Requirement: Strict inward package dependency boundary
The `src/idat_sync/domain/` and `src/idat_sync/application/` packages SHALL have zero imports from `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `httpx`, `office365`, `yt_dlp`, `playwright`, `tkinter`, `async_tkinter_loop`, `webview`, or `jnius`. No module under `src/idat_sync/` SHALL import `requests`, `playwright`, `tkinter`, or `async_tkinter_loop`, or call `print(...)`, `src/idat_sync/ui/` MUST NOT import `idat_sync.infrastructure`, `src/idat_sync/application/task_orchestrator.py` SHALL have zero imports from `idat_sync.application.events` or `threading`, `src/idat_sync/infrastructure/auth.py`, `src/idat_sync/utils.py`, `src/idat_sync/ui/dialogs.py`, `src/idat_sync/ui/icons.py`, and `src/idat_sync/ui/mfa_dialog.py` MUST NOT exist, and `safe_filename` and `WINDOWS_RESERVED` SHALL reside in `src/idat_sync/domain/path_utils.py`. Verified at the AST architecture boundary (`src/idat_sync/`) and `domain.path_utils` (`src/idat_sync/domain/path_utils.py`) seams.

#### Scenario: Inspecting domain and application imports
- **WHEN** all Python modules under `src/idat_sync/domain/` and `src/idat_sync/application/` are parsed for import statements
- **THEN** none of them import `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `httpx`, `office365`, `yt_dlp`, `playwright`, `tkinter`, `async_tkinter_loop`, `webview`, or `jnius`, and `src/idat_sync/application/task_orchestrator.py` does not import `idat_sync.application.events` or `threading`

#### Scenario: Sanitizing course filenames without importing infrastructure
- **WHEN** `safe_filename(s, replacement=" ", max_len=100)` in `src/idat_sync/domain/path_utils.py` is called with accents, forbidden characters, or Windows reserved names (`WINDOWS_RESERVED`)
- **THEN** it returns the normalized, safe ASCII filename of at most `max_len` characters

#### Scenario: Enforcing ui and playwright isolation and legacy auth/utils removal
- **WHEN** all Python modules under `src/idat_sync/` are parsed via AST for imports and calls and removed legacy paths are checked
- **THEN** `src/idat_sync/utils.py`, `src/idat_sync/infrastructure/auth.py`, `src/idat_sync/ui/dialogs.py`, `src/idat_sync/ui/icons.py`, and `src/idat_sync/ui/mfa_dialog.py` do not exist, no module imports `idat_sync.utils`, `idat_sync.infrastructure.auth`, `requests`, `playwright`, `tkinter`, or `async_tkinter_loop`, no module under `src/idat_sync/ui/` imports `idat_sync.infrastructure`, and zero modules under `src/idat_sync/` contain a `print(...)` call

### Requirement: Treeview course selection UI and ProgressInterceptor removal
`src/idat_sync/ui/app.py` MUST NOT define `ProgressInterceptor` and MUST NOT reassign `sys.stdout` or `sys.stderr`, and `idat_sync.main.main()` SHALL invoke `configure_logging(config.log_file)`. `SyncFrame` in `src/idat_sync/ui/sync_frame.py` MUST NOT define `output_text`, `_TreeProgressListener`, `progress_queue`, `update_progress`, `exit`, or `_queue_error`, and MUST NOT call `loop.call_soon_threadsafe` or `asyncio.to_thread`. `SyncFrame` SHALL await `await create_sync_service(self.credentials, self.config)`, `await self.idat_sync.list_courses()`, and `async for event in self.idat_sync.sync_courses(path, selected, refresh=True): self._handle_event(event)` directly on the UI event loop, delegating pure-Python tree state management, `RowStatus` (`StrEnum`), `Progress`, and per-course node tracking (`CourseTree.to_dict()`) to `CourseTree` (`src/idat_sync/ui/course_tree.py`) without `tkinter` or `Icons`, while `IDATSync.sync_courses(..., refresh=True)` clears `course.sections.clear()` on each passed `Course` so repeat `Sync` runs refresh Moodle sections without recreating the active `IDATSync` service, and `SyncFrame` sets `self.status` to `"Sync completed with errors."` when any yielded `CourseSyncCompleted` has `error is not None` or `"Sync done."` when all checked courses succeed. `CourseTree` SHALL maintain course rows (`id`, `name`, `checked`, `status`, `progress`, `open`, `children`), nested folder nodes, and leaf file nodes, `CourseTree.show_files` SHALL append synced and pending file nodes additively and increment `Progress` (initialized on `reset_for_sync` or `RowStatus.FETCHING` without clearing previously discovered children of that course mid-sync), and `SyncFrame` SHALL provide selection actions (`select_in_progress`, `select_all`, `clear_selection`, `toggle_course`) and `status`, ordering in-progress courses first (checked by default) on startup and updating parent, nested folder, and leaf file rows (`0%`-`100%`) during sync while pushing state snapshots to the Astro + Tailwind frontend (`src/idat_sync/ui/index.html`). Verified at `MainApp` (`src/idat_sync/ui/app.py`), `idat_sync.main.main` (`src/idat_sync/main.py`), `CourseTree` (`src/idat_sync/ui/course_tree.py`), `IDATSync` (`src/idat_sync/application/sync_service.py`), and `SyncFrame` (`src/idat_sync/ui/sync_frame.py`) seams.

#### Scenario: Ordering in-progress courses first and pre-selecting them on startup
- **WHEN** `await SyncFrame.load_idat_sync(credentials)` loads courses from `await idat_sync.list_courses()` containing past and in-progress courses
- **THEN** `SyncFrame` delegates to `CourseTree` to order in-progress courses first with `checked == True` and past courses after with `checked == False`, and sets `can_sync == True` only when `not is_syncing`, `folder_path` is non-empty, and at least one course is checked (or when courses have not loaded yet after a startup error)

#### Scenario: Updating parent course and child file rows during sync
- **WHEN** `await SyncFrame.do_sync()` runs on checked courses and receives one or more `CourseFilesDiscovered` batches
- **THEN** `hasattr(SyncFrame, "update_progress")` and `hasattr(SyncFrame, "exit")` are `False`, only checked courses are reset to `RowStatus.QUEUED` (`"Queued"`), `"0%"` and passed to `async for event in self.idat_sync.sync_courses(path, selected, refresh=True):`, `CourseTree.show_files` appends each batch without clearing prior batches for that course, nested folder nodes and leaf file nodes track `Synced` (`100%`) or `Pending` (`0%`) -> `Downloading` (`0%`) -> `Completed` (`100%`) / `Failed` (`0%`), and parent course progress updates as `f"{int(done * 100 / total)}%"` (`"100%"` when `total == 0`)

#### Scenario: Reporting completion error status in SyncFrame
- **WHEN** `await SyncFrame.do_sync()` finishes iterating `sync_courses` after at least one `CourseSyncCompleted` had `error is not None` vs when all had `error is None`
- **THEN** `self.status` is set to `"Sync completed with errors."` when any course had an error and `"Sync done."` when all succeeded

#### Scenario: Clearing selected course sections on repeat Sync without recreating IDATSync
- **WHEN** `await SyncFrame.do_sync()` runs a second time on checked courses with already-populated `sections` while unchecked courses retain cached `sections`
- **THEN** `IDATSync.sync_courses(path, selected, refresh=True)` clears `Course.sections` for checked courses, unchecked `Course.sections` remain untouched, and `create_sync_service` is not invoked again while `self.idat_sync` is already active
