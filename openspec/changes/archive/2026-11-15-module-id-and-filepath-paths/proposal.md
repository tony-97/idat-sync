# Proposal

## Why

`MoodleAdapter._to_module_content` discards Moodle's `filepath` field and `CourseRepository._get_module_content` names folders from `module.name` alone, so files inside subfolders of a Moodle `folder` module (such as `/Parte 1/guia.pdf` vs `/Parte 2/guia.pdf`) and items inside equal-named `CourseModule` entries collapse to the same local path. Then `SyncDiff.from_trees` builds `{c.path: c for c in remote_contents}` and silently drops earlier items.

## What Changes

- **BREAKING**: Update `CourseRepository._get_module_content` to append `-[<module.id>]` to every `CourseModule` folder name (`<bounded_name>-[<module.id>]`, truncating `module.name` first so the segment stays `<= 100` characters).
- Add `filepath: str = "/"` to `ModuleContent` (`domain/course.py`), populate it from `raw_content.get("filepath", "/")` in `MoodleAdapter._to_module_content`, and apply sanitized non-root subfolder segments in `CourseRepository.list_url_content` without changing `UrlContentStrategy`.
- Update `IDATSync.get_course_remote_tree` to disambiguate any remaining case-insensitive path collisions (`<stem> (2)<ext>` within 100 chars, keeping the first item unchanged), and update `SyncDiff.from_trees` to raise `ValueError` on duplicate paths instead of silently overwriting entries.

## Non-Goals

- Creating a retroactive disk migration script for older module folders (deferred until needed; no users have synced legacy trees yet).
- Appending numeric IDs to `Course` or `CourseSection` directory names.
- Converting Windows paths to `\\?\` long-path prefixes or changing `max_len=100` segment bounds.
- Fixing unrelated `/ponytail-audit` findings (SharePoint `$select`/`datetime.min`, `StringDownloader` update skipping, UI shutdown cleanup, or `requires-python`).

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `course-synchronization`: Preserve `ModuleContent.filepath` subfolders during `UrlContentStrategy` resolution, qualify every `CourseModule` folder with `-[<module.id>]`, deduplicate remaining case-insensitive collisions in `IDATSync.get_course_remote_tree`, and fail fast on duplicate `Content.path` keys in `SyncDiff.from_trees`.

## Impact

- **Modules touched**: `src/idat_sync/domain/course.py`, `src/idat_sync/domain/path_utils.py`, `src/idat_sync/domain/sync.py`, `src/idat_sync/application/course_repository.py`, `src/idat_sync/application/sync_service.py`, `src/idat_sync/infrastructure/moodle_adapter.py`.
- **Tests touched**: `tests/test_moodle_adapter.py`, `tests/test_course_repository.py`, `tests/test_sync_pipeline.py`, `tests/test_real_data.py` (check fixture expectations).
- **Docs already updated**: `GLOSSARY.md`, `docs/adr/0004-module-id-folders-and-filepath-preservation.md`.
- **Applicable `agent-os` standards (`agent-os/standards/index.yml`)**:
  - `backend/client-adapter-acl`
  - `backend/content-strategy-dispatch`
  - `global/layer-boundaries`
  - `testing/composition-and-seams`
