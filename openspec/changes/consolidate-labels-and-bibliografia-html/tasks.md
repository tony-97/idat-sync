# Tasks

## 1. Consolidate Section Labels into info.html

- [x] 1.1 At the `MoodleAdapter` seam in `tests/test_moodle_adapter.py` and `src/idat_sync/infrastructure/moodle_adapter.py`, write failing tests verifying that non-`Bibliografía` sections append non-empty `modname == "label"` `description`s in module order into a single `StringContent(Path("info.html"), 0, ...)` on `CourseSection.inline_contents` (ordered after `-[<sec_id>]-resumen.html` when present), ignore empty-description labels, omit `info.html` when no non-empty labels exist, and omit per-label `CourseModule` `.html` items (updating the filename-sanitization test accordingly); implement the minimal changes in `MoodleAdapter` and verify `python -m unittest tests.test_moodle_adapter` passes.

## 2. Dump Bibliografía Labels and URLs into bibliografia.html

- [x] 2.1 At the `MoodleAdapter` seam in `tests/test_moodle_adapter.py` and `src/idat_sync/infrastructure/moodle_adapter.py`, write failing tests verifying that sections whose trimmed, lowercase, accent-stripped name equals `"bibliografia"` collect non-empty `label` `description` HTML and HTML-escaped `url` entries (`name` + `fileurl`) in module order into a single `StringContent(Path("bibliografia.html"), 0, ...)` on `CourseSection.inline_contents`, omit `info.html`, emit no `ModuleContent` items for those `url` modules, and preserve any non-`label`/non-`url` modules in `CourseSection.modules`; implement the minimal code in `MoodleAdapter` and verify `python -m unittest tests.test_moodle_adapter` passes.
- [x] 2.2 At the `CourseRepository.get_course_content` seam in `tests/test_course_repository.py`, write tests verifying section-level `info.html` and `bibliografia.html` project directly under `<course_dir>/<safe_section_name>/` without invoking `UrlContentStrategy` for `Bibliografía` URLs, and verify `python -m unittest tests.test_course_repository tests.test_real_data` passes.

## 3. Verification

- [x] 3.1 Run the full test suite (`python -m unittest discover -s tests`), typecheck, and lint across the repository and verify all checks pass.
