# Design

## Context

See [proposal.md](./proposal.md) for motivation and [spec.md](./specs/course-synchronization/spec.md) for behavioral contracts.

All changes preserve the inward dependency rule in [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md), path sanitization rules in [ADR-0002](../../../docs/adr/0002-path-sanitization-compatibility.md), and academic-term in-progress detection in [ADR-0003](../../../docs/adr/0003-in-progress-course-detection-by-category-term.md).

## Goals / Non-Goals

**Goals:**
- Load the enrolled course list in a single Moodle request (`list_courses`) on `SyncFrame` startup and order in-progress courses first (`[x]` pre-selected).
- Lazily populate Moodle sections per course (`populate_course_sections`) as each selected course begins syncing, caching `_get_assignments_by_course()` once per `MoodleAdapter` instance.
- Report course and file sync lifecycle transitions through `SyncProgressPort` and `TaskOrchestrator` callbacks into `SyncFrame`'s `ttk.Treeview` (`Name`, `Status`, `Progress`) without `stdout` interception.

**Non-Goals:**
- Backwards-compatibility shims for `get_courses_with_contents()`, `get_courses()`, `get_all_courses_contents()`, or optional `courses=None` in `sync_courses`.
- Byte-size, transfer speed, or ETA columns.

## Decisions

### 1. In-Progress Course Detection (`src/idat_sync/domain/course.py`)
Per [ADR-0003](../../../docs/adr/0003-in-progress-course-detection-by-category-term.md), `Course` adds `category: str = ""` and `get_in_progress_course_ids(courses: Sequence[Course]) -> set[int]` parses `(int(year), roman_value, suffix)` from `course.category` (or leading token of `course.name`) and selects all courses matching the maximum term key.
- *Alternative considered*: Passing `classification="inprogress"` to Moodle or comparing `enddate`. Rejected because IDAT sets `enddate = 0` on all courses and copies stale `startdate` values across cloned templates.

### 2. Single-Path Lazy Per-Course Section Loading (`MoodleAdapter`, `CourseGateway`, `CourseRepository`, `IDATSync`)
- `MoodleAdapter` stores `self._assignments_cache: dict[int, dict[int, dict]] | None = None`. `populate_course_sections(course: Course) -> Course` populates `self._assignments_cache` on first call via `self._get_assignments_by_course()`, fetches `self.get_course_sections(course.id, assignments=...)`, assigns `course.sections`, and returns `course`.
- `IDATSync.sync_course(course, course_dir, progress_listener=None)` calls `self.course_repository.populate_course_sections(course)` when `not course.sections`, ensuring each selected course immediately transitions from Moodle section fetch to file diffing and downloading without waiting for subsequent selected courses' Moodle requests.
- *Alternative considered*: Populating sections for all selected courses before syncing the first course. Rejected because with `request_delay=30.0`, selecting 7 courses would stall the first download by 3.5 minutes.

### 3. Actionable File Filtering & Thread-Safe UI Progress Updates (`SyncProgressPort`, `TaskOrchestrator`, `SyncFrame`)
- `IDATSync.sync_course` computes `diff = self.check_course_sync(course, course_path)` and `tasks = self.download_manager.prepare_download_tasks(diff.pending_contents)`. Because `StringDownloader.can_handle` rejects empty/whitespace `StringContent` items (such as empty Moodle section summaries), `IDATSync` passes only `sorted(diff.synced_paths)` and `[t.path for t in tasks]` to `progress_listener.on_course_files(course, course_path, synced_paths, pending_paths)`.
- Because `IDATSync.sync_courses` and `TaskOrchestrator._run_group` execute on worker threads while `tkinter` widgets must only be mutated on the main event loop thread, `SyncFrame`'s `SyncProgressPort` implementation marshals all treeview and `status_var` updates onto `master.loop` via `master.loop.call_soon_threadsafe` and an `asyncio.Queue`.

## Modules Touched & Public Test Seams

| Module | Public Seam |
|---|---|
| `src/idat_sync/domain/course.py` | `Course`, `get_in_progress_course_ids` |
| `src/idat_sync/infrastructure/moodle_adapter.py` | `MoodleAdapter.list_courses`, `MoodleAdapter.populate_course_sections` |
| `src/idat_sync/application/ports.py` | `CourseGateway`, `SyncProgressPort` |
| `src/idat_sync/application/course_repository.py` | `CourseRepository.list_courses`, `CourseRepository.populate_course_sections` |
| `src/idat_sync/application/task_orchestrator.py` | `TaskOrchestrator.run`, `TaskOrchestrator.run_async` |
| `src/idat_sync/application/sync_service.py` | `IDATSync.list_courses`, `IDATSync.sync_course`, `IDATSync.sync_courses` |
| `src/idat_sync/ui/app.py` | `MainApp` (`ProgressInterceptor` removed) |
| `src/idat_sync/ui/sync_frame.py` | `SyncFrame` (`ttk.Treeview`, selection controls, `status_var`) |

## Risks / Trade-offs

- **[Risk] Concurrent downloader groups invoking task callbacks simultaneously from worker threads** → Mitigation: Route UI listener callbacks through `loop.call_soon_threadsafe(queue.put_nowait, event)` so treeview updates and course `done_count / total` progress calculations serialize on the main event loop.
- **[Risk] Offline or expired credentials during startup `list_courses()` leaves treeview empty** → Mitigation: Surface the error in `status_var` and allow clicking `Sync` once a folder is selected to retry initializing `IDATSync` and loading courses before syncing.
