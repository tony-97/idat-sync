# Spec Delta

## MODIFIED Requirements

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

### Requirement: CourseRepository orchestrates gateways and URL strategies
`CourseRepository` in `src/idat_sync/application/course_repository.py` SHALL accept `(course_gateway: CourseGateway, recording_gateway: RecordingGateway, url_strategies: Sequence[UrlContentStrategy])` and expose `async def list_courses(self) -> list[Course]`, `async def populate_course_sections(self, course: Course) -> Course`, `def invalidate_cache(self) -> None` (delegating to `self.course_gateway.invalidate_cache()`), `async def get_course_content(self, course: Course, course_dir: Path | str = Path()) -> list[Content]`, and `async def get_recordings(self, course_name: str, recordings_folder: str) -> list[Content]`. `CourseRepository.list_url_content` SHALL preserve non-root `ModuleContent.filepath` subfolders without modifying the `UrlContentStrategy.list_url_content(url, filename, last_modified)` signature by splitting `content.filepath` on `"/"`, raising `ValueError` on any `".."` traversal segment, ignoring empty and `"."` segments, sanitizing each remaining subfolder segment via `safe_path_segment`, and applying those relative subfolders via `Content.in_folder(...)`. `get_course_content(course, course_dir)` SHALL skip sections and modules with empty `name`, place each module's `inline_contents` before resolved `contents` inside a module folder formatted with `safe_path_segment` to `<bounded_name>-[<module.id>]` (`<= 100` characters total, truncating the sanitized `module.name` prefix first so `-[<module.id>]` is never truncated), prepend `section.inline_contents` before module items in `safe_path_segment(section.name)` (keeping `Course` and `CourseSection` directory names without IDs), and wrap items in `course_dir`. Verified at the `CourseRepository` (`src/idat_sync/application/course_repository.py`) public seam.

#### Scenario: Composing course content hierarchy and ordering
- **WHEN** `await CourseRepository.get_course_content(course, course_dir)` is called on a `Course` with sections and modules (including unsafe characters such as `:` or `/` in `section.name` or `module.name`, plus `CourseModule(id=201, name="Carpeta", ...)` containing `"guia.pdf"` with `filepath="/Parte 1/"` and `filepath="/Parte 2/"` and root items with `filepath="/"`)
- **THEN** it awaits `strategy.list_url_content(url, filename, timemodified)` with the unchanged 3-argument signature for each matching URL strategy and emits each section's `inline_contents` followed by each named module's `inline_contents` and URL-resolved `contents`, composed bottom-up via subfolder `.in_folder(...)` segments (`Parte 1/guia.pdf`, `Parte 2/guia.pdf`), `.in_folder("<safe_bounded_module_name>-[<module.id>]")`, `.in_folder(safe_path_segment(section.name))`, and `.in_folder(course_dir)`

#### Scenario: Preserving module ID suffix when module name exceeds segment budget
- **WHEN** `await CourseRepository.get_course_content(course, course_dir)` processes two distinct `CourseModule` items (`id=1001`, `id=1002`) whose names exceed 100 characters and share an identical 95-character prefix
- **THEN** each module directory segment is truncated before appending its suffix so both segments have length `<= 100`, end with `-[1001]` and `-[1002]`, and remain distinct paths

#### Scenario: Rejecting traversal segments and sanitizing unsafe names in ModuleContent filepath
- **WHEN** `await CourseRepository.list_url_content(contents)` receives `filepath="/Tema: 1/./CON/"` vs `filepath="/../outside/"`
- **THEN** `"/Tema: 1/./CON/"` resolves under `Path("Tema 1/_CON/<filename>")` and `"/../outside/"` raises `ValueError`

#### Scenario: Wrapping recordings in target folder
- **WHEN** `await CourseRepository.get_recordings(course_name, recordings_folder)` is called
- **THEN** it awaits `recording_gateway.fetch_recordings(course_name)` and returns `[r.in_folder(recordings_folder) for r in recordings]`

#### Scenario: Delegating cache invalidation from CourseRepository to CourseGateway
- **WHEN** `CourseRepository.invalidate_cache()` is called
- **THEN** it invokes `self.course_gateway.invalidate_cache()` once

### Requirement: Pure domain SyncDiff calculation and FileSystemPort orchestration
`SyncDiff.from_trees(remote_contents, local_mtimes)` in `src/idat_sync/domain/sync.py` SHALL reject duplicate exact `Content.path` keys across `remote_contents` by raising `ValueError` naming the colliding path instead of silently overwriting entries, and otherwise SHALL compute `missing_paths`, `outdated_paths` (where `remote.last_modified > 0 and remote.last_modified > local_mtime`), `synced_paths`, `local_only_paths`, and `entries` sorted by `e.path` without disk I/O. `DownloadManager` and `IDATSync` SHALL perform all local disk reads and directory creation via `FileSystemPort` (`LocalFileSystemAdapter`). Verified at `SyncDiff.from_trees` and `DownloadManager` seams.

#### Scenario: Computing SyncDiff purely in memory
- **WHEN** `SyncDiff.from_trees(remote_contents, local_mtimes)` is called with unique in-memory `Content` items and a `Mapping[Path, int]`
- **THEN** it partitions paths into `missing_paths`, `outdated_paths`, `synced_paths`, and `local_only_paths` and sorts `entries` by `e.path` without touching the filesystem

#### Scenario: Failing fast on duplicate remote Content paths in SyncDiff.from_trees
- **WHEN** `SyncDiff.from_trees(remote_contents, local_mtimes)` receives two `Content` items sharing the exact same `path`
- **THEN** it raises `ValueError` containing the duplicate path string rather than dropping the first item

#### Scenario: Orchestrating sync pipeline through FileSystemPort and Downloader groups
- **WHEN** `DownloadManager.diff_trees(root_dir, remote_contents)` and `IDATSync.sync_course(course, course_dir)` run
- **THEN** directory creation and file timestamp scanning are delegated to `FileSystemPort` and pending tasks execute through `TaskOrchestrator`

### Requirement: Per-course failure isolation and early credential abort in IDATSync
`IDATSync` in `src/idat_sync/application/sync_service.py` MUST NOT accept `publish` in `__init__` or `progress_listener` in `sync_course` or `sync_courses`. `list_courses() -> list[Course]`, `get_course_remote_tree(course, course_dir) -> list[Content]`, and `check_course_sync(course, course_dir) -> SyncDiff` SHALL be `async def` coroutines. `get_course_remote_tree(course, course_dir)` SHALL merge `course_repository.get_course_content(course, course_path)` and `course_repository.get_recordings(course.name, recordings_folder)` and disambiguate any duplicate leaf paths compared case-insensitively (`path.as_posix().casefold()`): the first occurrence SHALL remain unchanged, and each subsequent colliding item SHALL be renamed in its final segment to `<stem> (n)<ext>` (starting at `n = 2` and incrementing until unused case-insensitively, bounding the filename to `<= 100` characters via `safe_path_segment`). `sync_course(course: Course, course_dir: str | Path) -> AsyncIterator[SyncEvent]` and `sync_courses(root_folder: str | Path, courses: Sequence[Course]) -> AsyncIterator[SyncEvent]` SHALL be async generators that call `log_sync_event(event)` before yielding each `SyncEvent`. `sync_course` SHALL drive `task_orchestrator.create_coroutines(tasks, run_task)` concurrently across downloader groups, continuing remaining tasks after non-credential task errors and cancelling sibling group tasks immediately on `CredentialsExpiredError`. `sync_courses` SHALL call `self.course_repository.invalidate_cache()` at the start of each sync pass before iterating `courses`, compute each `Course` folder via `Path(root_folder) / safe_filename(course.name)`, lazily await `populate_course_sections(course)` when `course.sections` is empty, emit `CourseSyncCompleted(course, exc)` and skip single-`Course` failures other than `CredentialsExpiredError` so later courses still sync, and abort remaining courses immediately on `CredentialsExpiredError`. Verified at the `IDATSync` (`src/idat_sync/application/sync_service.py`) seam.

#### Scenario: Disambiguating case-insensitive and truncated leaf path collisions in get_course_remote_tree
- **WHEN** `await IDATSync.get_course_remote_tree(course, course_dir)` receives merged course and recording items containing three case-insensitively colliding paths (`"Course/Sec/Mod-[1]/Parte 1/guia.pdf"`, `"Course/Sec/Mod-[1]/parte 1/guia.pdf"`, `"Course/Sec/Mod-[1]/Parte 1/guia.pdf"`) or a second file whose 100-character name collides with an earlier file
- **THEN** the first item keeps its exact `path`, the second becomes `.../parte 1/guia (2).pdf`, the third becomes `.../Parte 1/guia (3).pdf`, every leaf segment has length `<= 100`, and passing the returned list into `SyncDiff.from_trees` succeeds without raising `ValueError`

#### Scenario: Invalidating repository cache at the start of sync_courses
- **WHEN** `[e async for e in IDATSync.sync_courses(root_folder, [course1, course2])]` starts executing
- **THEN** `course_repository.invalidate_cache()` is called once before processing `course1` and `course2`

#### Scenario: Continuing remaining courses when one course fails
- **WHEN** `sync_course` raises a non-credential error on the first `Course` while a second `Course` in `courses` succeeds
- **THEN** `[e async for e in IDATSync.sync_courses(root_folder, [course1, course2])]` yields `CourseSyncCompleted(course1, exc)`, still synchronizes the second `Course` and yields `CourseSyncCompleted(course2, None)`, and finishes without raising

#### Scenario: Aborting remaining courses on CredentialsExpiredError
- **WHEN** a `DownloadTask` or course step raises `CredentialsExpiredError` inside `IDATSync.sync_course` or `IDATSync.sync_courses(root_folder, [course1, course2])`
- **THEN** `TaskDownloadFailed(task.path, exc)` (if raised by a task) and `CourseSyncCompleted(course1, exc)` are logged and yielded, remaining unstarted tasks across all downloader groups and subsequent courses in `courses` MUST NOT run, and `CredentialsExpiredError` propagates immediately to the caller
