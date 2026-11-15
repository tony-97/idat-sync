# Tasks

## 1. Preserve Moodle `ModuleContent.filepath` Subfolders

- [x] 1.1 At the `MoodleAdapter` seam (`tests/test_moodle_adapter.py`, `src/idat_sync/domain/course.py`, `src/idat_sync/infrastructure/moodle_adapter.py`), write failing tests verifying `ModuleContent.filepath` defaults to `"/"` and populates from `raw_content.get("filepath") or "/"` on both module `contents` and `assign` `introattachments`; add `filepath: str = "/"` to `ModuleContent` and populate it in `MoodleAdapter._to_module_content`, then verify `python -m unittest tests.test_moodle_adapter` passes.
- [x] 1.2 At the `CourseRepository.list_url_content` seam (`tests/test_course_repository.py`, `src/idat_sync/application/course_repository.py`), write failing tests verifying non-root `filepath` values (`"/Parte 1/"`, `"/Tema: 1/./CON/"`) compose sanitized subfolders via `Content.in_folder(...)` while keeping `UrlContentStrategy.list_url_content(url, filename, last_modified)` unchanged, `"/"` remains flat, and `".."` traversal raises `ValueError`; implement subfolder wrapping in `CourseRepository.list_url_content` and verify `python -m unittest tests.test_course_repository` passes.

## 2. Qualify `CourseModule` Directories with `-[<module.id>]`

- [x] 2.1 At `domain.path_utils`, `MoodleAdapter`, and `CourseRepository.get_course_content` seams (`tests/test_course_repository.py`, `tests/test_moodle_adapter.py`, `src/idat_sync/domain/path_utils.py`, `src/idat_sync/infrastructure/moodle_adapter.py`, `src/idat_sync/application/course_repository.py`), write failing tests verifying `<bounded_name>-[<module.id>]` on `CourseModule` folders (`<= 100` chars, truncating `module.name` first so long equal-prefix modules keep distinct `-[<module.id>]` suffixes, leaving `Course` and `CourseSection` names without IDs, and updating existing module-path assertions in `TestCourseRepository`); extract `safe_suffixed_segment` into `domain/path_utils.py`, reuse it in `MoodleAdapter._safe_suffixed_html` and `CourseRepository._get_module_content`, and verify `python -m unittest tests.test_course_repository tests.test_moodle_adapter tests.test_real_data` pass.

## 3. Deduplicate Remaining Leaf Collisions and Guard `SyncDiff`

- [x] 3.1 At the `SyncDiff.from_trees` seam (`tests/test_sync_pipeline.py`, `src/idat_sync/domain/sync.py`), write a failing test verifying duplicate `Content.path` items passed to `SyncDiff.from_trees` raise `ValueError` naming the colliding path; add the duplicate check in `SyncDiff.from_trees` and verify `python -m unittest tests.test_sync_pipeline` passes.
- [x] 3.2 At the `IDATSync.get_course_remote_tree` seam (`tests/test_sync_pipeline.py`, `src/idat_sync/application/sync_service.py`), write failing tests verifying case-insensitive path collisions (across subfolders like `Parte 1/guia.pdf` vs `parte 1/guia.pdf`, 3+ collisions, pre-existing `(2)` filenames, 100-char filename collisions, and recording collisions) keep the 1st occurrence unchanged, rename subsequent items to `<stem> (n)<ext>` bounded to `<= 100` chars via `safe_suffixed_segment`, and succeed in `check_course_sync` / `SyncDiff.from_trees`; implement deduplication in `IDATSync.get_course_remote_tree` and verify `python -m unittest tests.test_sync_pipeline` passes.

## 4. Verification

- [x] 4.1 Run the full test suite (`python -m unittest discover -s tests`), check architecture boundary tests (`tests/test_architecture.py`), and run configured typecheck and lint checks (or confirm none are configured/installed) to verify everything passes.

## Workflow follow-up

- Commit the completed changes before running `/code-review`.
- Run `/opsx:archive` after review passes to merge the delta spec into `openspec/specs/course-synchronization/spec.md`.
