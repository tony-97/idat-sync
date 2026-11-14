## ADDED Requirements

### Requirement: Academic term category and in-progress course detection
`Course` in `src/idat_sync/domain/course.py` SHALL define `category: str = ""`, and `get_in_progress_course_ids(courses: Sequence[Course]) -> set[int]` SHALL parse `r"(\d{4})-(I{1,3}|IV)([A-Z]?)$"` from `course.category.strip()` (falling back to the first whitespace token of `course.name`), rank terms by `(int(year), roman_value, suffix)` where `I=1, II=2, III=3, IV=4` and `"" < "A" < "B"`, and return `id`s matching the maximum term (`set()` if none match). Verified at `domain.course` seam.

#### Scenario: Ranking academic terms across categories and split sections
- **WHEN** `get_in_progress_course_ids` receives courses with categories `"I.01.2025-IIA"`, `"I.03.2025-IIA"`, `"II.01.2025-III"`, `"III.01.2026-I"`, `"III.01.2026-II"`, and `"IV.01.2026-III"`
- **THEN** it returns the set of `id`s belonging to `"IV.01.2026-III"` (or both `"I.01.2025-IIA"` and `"I.03.2025-IIA"` when `"2025-IIA"` is the highest term)

#### Scenario: Falling back to course name prefix or returning empty set when no term matches
- **WHEN** `get_in_progress_course_ids` receives courses with empty `category` and `name="IV.01.2026-III PATRONES"`, or courses where neither `category` nor `name` contains a valid `<YEAR>-<ROMAN><SUFFIX>` token
- **THEN** it extracts `(2026, 3, "")` from the leading token of `name` in the first case, and returns `set()` in the second case

### Requirement: Lazy per-course section population and eager loader removal
`MoodleAdapter`, `CourseGateway`, and `CourseRepository` SHALL define `list_courses() -> list[Course]` (mapping `category` with `sections=[]`) and `populate_course_sections(course: Course) -> Course` (caching `_get_assignments_by_course()` once per `MoodleAdapter` instance). `get_courses_with_contents`, `get_courses`, and `get_all_courses_contents` MUST NOT exist. Verified at `MoodleAdapter` and `CourseRepository` seams.

#### Scenario: Listing courses with categories without fetching sections or assignments
- **WHEN** `MoodleAdapter.list_courses()` or `CourseRepository.list_courses()` is called
- **THEN** `MoodleClient.fetch_enrolled_courses()` is called once, `fetch_assignments` and `fetch_course_contents` are not called, and each returned `Course` has `id`, `name`, `category`, and `sections == []`

#### Scenario: Caching assignments across lazy per-course section population
- **WHEN** `MoodleAdapter.populate_course_sections(course)` is called sequentially for two different `Course` instances on the same `MoodleAdapter`
- **THEN** `MoodleClient.fetch_assignments()` is called only once, `MoodleClient.fetch_course_contents(course.id)` is called once per course, and `MoodleAdapter`, `CourseGateway`, and `CourseRepository` do not define `get_courses_with_contents`, `get_courses`, or `get_all_courses_contents`

### Requirement: Structured sync progress reporting and actionable file filtering
`SyncProgressPort` in `src/idat_sync/application/ports.py` SHALL define `on_course_start`, `on_course_files`, `on_task_start`, `on_task_complete`, `on_task_error`, and `on_course_complete`. `TaskOrchestrator` SHALL invoke optional task lifecycle callbacks, and `IDATSync.sync_course` SHALL report only `sorted(diff.synced_paths)` and `[t.path for t in tasks]` to `on_course_files`, excluding non-downloadable empty `StringContent` items. Verified at `IDATSync` and `TaskOrchestrator` seams.

#### Scenario: Filtering non-downloadable empty StringContent items from reported course files
- **WHEN** `IDATSync.sync_course(course, course_dir, progress_listener=listener)` runs on a course containing a synced file, a downloadable missing file, and an empty `StringContent` summary rejected by `StringDownloader.can_handle`
- **THEN** `listener.on_course_files(course, Path(course_dir), synced_paths, pending_paths)` receives the synced file in `synced_paths` and only the downloadable missing file in `pending_paths` (excluding the empty `StringContent` path), followed by `on_task_start`, `on_task_complete`, and `on_course_complete(course, None)`

#### Scenario: Reporting task failure and course error to SyncProgressPort
- **WHEN** a `DownloadTask` raises a non-credential `Exception` during `TaskOrchestrator.run` or `IDATSync.sync_course`
- **THEN** `on_task_error` is invoked with `(task, exc)` (or `(course, task.path, exc)`), remaining tasks still execute, and `on_course_complete(course, error)` receives a non-`None` error when any task or course step failed

### Requirement: Treeview course selection UI and ProgressInterceptor removal
`src/idat_sync/ui/app.py` MUST NOT define `ProgressInterceptor`, and `SyncFrame` MUST NOT define `output_text`. `SyncFrame` SHALL render a `ttk.Treeview` (`#0` `Name`, `status`, `progress`) with checkbox, folder, and file icons, selection buttons (`In Progress`, `Select All`, `Clear`), and `status_var`, ordering in-progress courses first (checked by default) on startup and updating parent, nested folder, and leaf file rows (`0%`-`100%`) during sync. Verified at `MainApp` and `SyncFrame` seams.

#### Scenario: Ordering in-progress courses first and pre-selecting them on startup
- **WHEN** `SyncFrame.load_idat_sync` loads courses from `idat_sync.list_courses()` containing past and in-progress courses
- **THEN** `SyncFrame` inserts in-progress courses first in the `ttk.Treeview` with a checked icon and past courses after with an unchecked icon, and enables `sync_button` only when `not is_syncing`, `folder_path` is non-empty, and at least one course is checked (or when courses have not loaded yet after a startup error)

#### Scenario: Updating parent course and child file rows during sync
- **WHEN** `SyncFrame.do_sync()` runs on checked courses
- **THEN** only checked courses are reset to `Status="Queued"`, `Progress="0%"` and passed to `idat_sync.sync_courses(path, selected_courses, progress_listener=...)`, nested folder nodes use a folder icon and leaf file nodes use a file icon with `Synced` (`100%`) or `Pending` (`0%`) -> `Downloading` (`0%`) -> `Completed` (`100%`) / `Failed` (`0%`), and parent course progress updates as `f"{int(done * 100 / total)}%"` (`"100%"` when `total == 0`)

## MODIFIED Requirements

### Requirement: Per-course failure isolation and early credential abort in IDATSync
`IDATSync.sync_courses(root_folder, courses: Sequence[Course], progress_listener: SyncProgressPort | None = None)` SHALL require an explicit `courses` sequence, compute each `Course` folder via `Path(root_folder) / safe_filename(course.name)`, lazily populate `course.sections` when empty, log, notify `progress_listener.on_course_complete(course, exc)`, and skip single-`Course` failures other than `CredentialsExpiredError` so later courses still sync, and abort remaining courses immediately on `CredentialsExpiredError`. Verified at the `IDATSync` seam.

#### Scenario: Continuing remaining courses when one course fails
- **WHEN** `sync_course` raises a non-credential error on the first `Course` while a second `Course` in `courses` succeeds
- **THEN** `IDATSync.sync_courses(root_folder, [course1, course2], progress_listener)` logs the first `Course` error, calls `on_course_complete(course1, exc)`, still synchronizes the second `Course`, and finishes without raising

#### Scenario: Aborting remaining courses on CredentialsExpiredError
- **WHEN** the first `Course` raises `CredentialsExpiredError` inside `IDATSync.sync_courses(root_folder, [course1, course2], progress_listener)`
- **THEN** `on_course_complete(course1, exc)` is called, subsequent courses in `courses` MUST NOT be synchronized, and `CredentialsExpiredError` propagates immediately

### Requirement: Conditional real-data offline fixture and live Credentials smoke testing
`tests/test_real_data.py` SHALL define `TestRealCourseContentsFixture` and `TestLiveCredentialsIntegration` guarded by `@unittest.skipUnless` on `./all_courses_contents.json` and `./session.json`, with zero hardcoded course IDs, teacher names, or share tokens. Verified at the `MoodleAdapter`, `SharepointApiAdapter`, `CourseRepository`, `SyncDiff.from_trees`, `JsonCredentialStore`, and `MoodleDownloader` seams.

#### Scenario: Running offline fixture tests against all_courses_contents.json
- **WHEN** `TestRealCourseContentsFixture` runs with `./all_courses_contents.json` present (or skips via `@unittest.skipUnless(Path("./all_courses_contents.json").is_file(), "Requires ./all_courses_contents.json")` when absent)
- **THEN** it feeds `"course"`, `"contents"`, and `"assignments"` (defaulting missing `"assignments"` to `[]`) through `CourseRepository.list_courses()`, `CourseRepository.populate_course_sections(course)`, and `CourseRepository.get_course_content()`, verifies `get_in_progress_course_ids(courses)` is non-empty, verifies every emitted item is a domain `Content` (`StringContent`, `MoodleContent`, or `SharepointContent`) safely inside its `course_dir`, verifies `"modname": "assign"` modules with matching `"assignments"` emit `-[id]-intro.html` `StringContent` items and `introattachments` `ModuleContent` items, verifies both `sharepoint_host` and `sharepoint_personal_host` URLs route to `SharepointApiAdapter`, and verifies `SyncDiff.from_trees(remote_contents, {})` marks all resolved paths as `MISSING`

#### Scenario: Running live Credentials smoke tests against session.json and all_courses_contents.json
- **WHEN** `TestLiveCredentialsIntegration` runs with both `./all_courses_contents.json` and `./session.json` present (or skips via `@unittest.skipUnless(Path("./all_courses_contents.json").is_file() and Path("./session.json").is_file(), "Requires ./all_courses_contents.json and ./session.json")` when absent or when `JsonCredentialStore(Path("./session.json")).load()` returns `None`)
- **THEN** it initializes clients with `request_delay=0.0`, dynamically discovers the first matching URL from `./all_courses_contents.json` for each category (1 Moodle `/pluginfile.php/` URL, 1 `:b:` guest file URL on `sharepoint_host`, 1 `:f:` guest folder URL on `sharepoint_host`, 1 direct link with `uniqueid=`/`sourcedoc=` on `sharepoint_host`, and 1 `:b:` guest file URL on `sharepoint_personal_host`), downloads the Moodle file into a `tempfile.TemporaryDirectory()` via `MoodleDownloader` verifying a non-empty file on disk, and resolves the 4 SharePoint URLs via `SharepointApiAdapter.list_url_content` into non-empty `list[SharepointContent]` (failing immediately if `CredentialsExpiredError` is raised)
