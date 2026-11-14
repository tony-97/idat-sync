# Tasks

## 1. In-Progress Course Detection (`domain.course` seam)

- [x] 1.1 Add failing tests and implement `Course.category: str = ""` and `get_in_progress_course_ids(courses: Sequence[Course]) -> set[int]` in `src/idat_sync/domain/course.py`, verifying `<YEAR>-<ROMAN><SUFFIX>` ranking, `name` prefix fallback, and empty-set handling in `tests/test_course_repository.py`.

## 2. Lazy Per-Course Section Population (`MoodleAdapter` and `CourseRepository` seams)

- [x] 2.1 Add failing tests and implement `MoodleAdapter.list_courses()` (mapping `category`) and `MoodleAdapter.populate_course_sections(course)` (caching `_get_assignments_by_course()` once per adapter instance) while removing `get_courses_with_contents()` on `MoodleAdapter` and `CourseGateway`, verified in `tests/test_moodle_adapter.py`.
- [x] 2.2 Add failing tests and implement `CourseRepository.list_courses()` and `CourseRepository.populate_course_sections(course)` while removing `get_courses()` and `get_all_courses_contents()` in `src/idat_sync/application/course_repository.py`, verified in `tests/test_course_repository.py` and `tests/test_real_data.py`.

## 3. Structured Sync Progress Reporting (`TaskOrchestrator` and `IDATSync` seams)

- [x] 3.1 Add failing tests and implement `SyncProgressPort` in `src/idat_sync/application/ports.py` and `on_task_start`, `on_task_complete`, and `on_task_error` callbacks on `TaskOrchestrator.run` and `run_async` in `src/idat_sync/application/task_orchestrator.py`, verified in `tests/test_sync_pipeline.py`.
- [x] 3.2 Add failing tests and implement `IDATSync.list_courses()`, lazy section population and actionable file filtering (`sorted(diff.synced_paths)` and `[t.path for t in tasks]`, excluding non-downloadable empty `StringContent` items) in `IDATSync.sync_course`, and explicit `courses: Sequence[Course]` execution in `IDATSync.sync_courses` in `src/idat_sync/application/sync_service.py`, verified in `tests/test_sync_pipeline.py`.

## 4. Treeview Course Selection UI (`MainApp` and `SyncFrame` seams)

- [x] 4.1 Add failing tests and implement the `ttk.Treeview` (`#0` `Name`, `status`, `progress`), quick-select buttons (`In Progress`, `Select All`, `Clear`), row-click checkbox toggling, `status_var` status/error reporting, and removal of `output_text` and `ProgressInterceptor` in `src/idat_sync/ui/sync_frame.py` and `src/idat_sync/ui/app.py`, verified in `tests/test_auth.py`.

## 5. Final Verification

- [x] 5.1 Run the full test suite, typecheck, and lint across the repository and verify all checks pass.
