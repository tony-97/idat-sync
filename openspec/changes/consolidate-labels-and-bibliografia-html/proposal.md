# Proposal

## Why

In Moodle's `core_course_get_contents` payload, `modname == "label"` modules carry only HTML `description` (never `contents` or `url`). Mapping each label to its own `<module.name>/<module.name>.html` creates unnecessary folders and causes 23 leaf-path collisions across the 35-course fixture when labels share generic or truncated names. Separately, `Bibliografía` sections hold only `label` headings and external library `url` modules (`elibro.net`, `ebooks7-24.com`, `ebooks.editorialmacro.com`, `bc.vitalsource.com`), which `UrlContentStrategy` silently drops instead of saving as readable bibliography text.

## What Changes

- In `MoodleAdapter`, for non-`Bibliografía` sections, collect all `modname == "label"` modules with non-empty `description` inside a `CourseSection` and append them in module order into a single section-level `StringContent` named `info.html` on `CourseSection.inline_contents`, instead of emitting per-label `CourseModule` inline files.
- In `MoodleAdapter`, detect `Bibliografía` sections (matching `section.name` case- and accent-insensitively to `"bibliografia"`), collect both non-empty `label` descriptions and `url` module citations (`name` + `contents[0].fileurl`, HTML-escaped) in module order into a single section-level `StringContent` named `bibliografia.html` on `CourseSection.inline_contents`, without emitting `info.html` or `ModuleContent` items for those `url` modules.
- Update `MoodleAdapter` and `CourseRepository` unit tests to match the consolidated section-level `info.html` and `bibliografia.html` behavior.

## Non-Goals

- Converting `.html` artifacts to `.pdf` (e.g., via `xhtml2pdf`, `weasyprint`, or Playwright); outputs remain `.html` written by `StringDownloader`.
- Adding `UrlContentStrategy` implementations for external library hosts or changing `CourseRepository`, `DownloadManager`, `StringDownloader`, `domain/`, or `ui/`.
- Handling non-`Bibliografía` external `url` modules (e.g., YouTube, Canva) or other leaf-path collision vectors (`assign` attachments, recordings).
- Modifying `GLOSSARY.md` or adding new ADRs under `docs/adr/`.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `course-synchronization`: Update `Requirement: Moodle adapter translates vendor curriculum into domain models` so non-`Bibliografía` `label` descriptions aggregate into section-level `info.html` and `Bibliografía` `label` + `url` modules aggregate into section-level `bibliografia.html` without `ModuleContent` / `UrlContentStrategy` resolution.

## Impact

- **Modules touched**: `src/idat_sync/infrastructure/moodle_adapter.py`, `tests/test_moodle_adapter.py`, `tests/test_course_repository.py`.
- **Public test seam**: `MoodleAdapter` (`populate_course_sections` / `get_course_sections`) and `CourseRepository.get_course_content`.
- **Applicable `agent-os` standards (`agent-os/standards/index.yml`)**:
  - `backend/client-adapter-acl`
  - `global/layer-boundaries`
  - `testing/composition-and-seams`
