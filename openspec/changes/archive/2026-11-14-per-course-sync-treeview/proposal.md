# Proposal

## Why

Currently, `IDATSync` eagerly fetches Moodle sections for all 35 enrolled historical and current courses upfront before syncing begins, and `SyncFrame` dumps raw stdout logs into a `tk.Text` widget with no way to select individual courses or only in-progress courses (since IDAT's Moodle sets `enddate = 0` on all courses). Adding lazy per-course content loading, term-based in-progress course detection, and a collapsible `ttk.Treeview` progress table lets users quickly select in-progress courses, all courses, or specific courses and track file-level and course-level sync status.

## What Changes

- **In-progress course detection in domain**: Add `category: str = ""` to `Course` and `get_in_progress_course_ids(courses)` in `src/idat_sync/domain/course.py`, ranking `<YEAR>-<ROMAN><SUFFIX>` academic term codes from `Course.category` (falling back to the leading token of `Course.name`) per ADR-0003.
- **BREAKING — Replace eager all-course loading with single-path lazy per-course loading**:
  - Replace `get_courses_with_contents()` on `CourseGateway` and `MoodleAdapter` with `list_courses() -> list[Course]` and `populate_course_sections(course: Course) -> Course` (caching `_get_assignments_by_course()` once per `MoodleAdapter` instance).
  - Replace `get_courses()` and remove `get_all_courses_contents()` on `CourseRepository` in favor of `list_courses()` and `populate_course_sections(course)`.
  - Update `IDATSync` to expose `list_courses()`, lazily populate sections inside `sync_course(course, course_dir, progress_listener=None)`, and require an explicit `courses: Sequence[Course]` argument in `sync_courses(root_folder, courses, progress_listener=None)`.
- **Structured progress reporting seam**: Add `SyncProgressPort` in `src/idat_sync/application/ports.py` and task lifecycle callbacks (`on_task_start`, `on_task_complete`, `on_task_error`) on `TaskOrchestrator.run` and `run_async`. Exclude non-downloadable empty `StringContent` items from reported course files and progress totals.
- **BREAKING — Treeview UI and `ProgressInterceptor` removal**:
  - Remove `ProgressInterceptor` from `src/idat_sync/ui/app.py` and `output_text` from `src/idat_sync/ui/sync_frame.py`.
  - Replace the console log widget in `SyncFrame` with a collapsible `ttk.Treeview` (`Name`, `Status`, `Progress`), quick-select controls (`In Progress`, `Select All`, `Clear`), row-click checkbox toggling, and a bottom status bar (`status_var`).

## Non-Goals

- Displaying downloaded byte counts, file sizes, transfer speeds, or estimated time remaining (ETA) in the treeview.
- Sub-100% live byte/fragment streaming percentages for individual files (files report `0%` until completion and `100%` when synced/completed).
- Mid-file pause/resume controls.
- Filtering courses by Moodle's `enddate` or `classification="inprogress"` parameter.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `course-synchronization`: Add academic term category and in-progress course detection to `Course`, replace eager all-course loading with lazy per-course section population and explicit course selection, add structured progress callbacks across `IDATSync` and `TaskOrchestrator`, and replace stdout interception with the `ttk.Treeview` course/file sync UI.

## Applicable Standards (`agent-os/standards/index.yml`)

- `backend/client-adapter-acl`
- `backend/content-strategy-dispatch`
- `global/layer-boundaries`
- `testing/composition-and-seams`

## Impact

- `src/idat_sync/domain/course.py`
- `src/idat_sync/infrastructure/moodle_adapter.py`
- `src/idat_sync/application/ports.py`
- `src/idat_sync/application/course_repository.py`
- `src/idat_sync/application/task_orchestrator.py`
- `src/idat_sync/application/sync_service.py`
- `src/idat_sync/ui/app.py`
- `src/idat_sync/ui/sync_frame.py`
- `tests/test_moodle_adapter.py`, `tests/test_course_repository.py`, `tests/test_sync_pipeline.py`, `tests/test_auth.py`, `tests/test_real_data.py`
