# Design

## Context

See [proposal.md](./proposal.md) for motivation and affected files, and [ADR-0004](../../../docs/adr/0004-module-id-folders-and-filepath-preservation.md) for the decision to qualify every `CourseModule` directory with `-[<module.id>]` as an explicit exception to [ADR-0002](../../../docs/adr/0002-path-sanitization-compatibility.md). `GLOSSARY.md` has already been updated for `ModuleContent` subfolder semantics.

## Goals / Non-Goals

**Goals:**
- Preserve Moodle `ModuleContent.filepath` subfolders without modifying the `UrlContentStrategy` port signature.
- Unconditionally isolate equal-named `CourseModule` directories using `-[<module.id>]` bounded to 100 characters.
- Disambiguate any remaining leaf collisions case-insensitively at course tree assembly and fail fast in `SyncDiff.from_trees` if duplicates bypass assembly.

**Non-Goals:**
- Retroactive local-folder migration scripts, ID suffixes on `Course`/`CourseSection`, or Windows `\\?\` long-path prefixes.

## Decisions

1. **Apply `ModuleContent.filepath` in `CourseRepository.list_url_content` rather than extending `UrlContentStrategy`**: Adding `filepath: str = "/"` to `ModuleContent` (`domain/course.py`) and composing non-root sanitized segments via `Content.in_folder(...)` inside `CourseRepository.list_url_content` leaves `UrlContentStrategy.list_url_content(url, filename, last_modified)` untouched for `SharepointApiAdapter`, mocks, and existing spec tests, and also preserves subfolders if a `ModuleContent` points to an external file link inside a Moodle subfolder.
   - *Alternative considered*: Passing `filepath` as a 4th parameter on `UrlContentStrategy.list_url_content`. Rejected because it forces breaking changes on `application/ports.py` and `SharepointApiAdapter` (`infrastructure/sharepoint_adapter.py`) for a Moodle-specific folder attribute.
   - *Traversal check*: Splitting `filepath` on `"/"` checks `part == ".."` before calling `safe_path_segment(part)` because `safe_path_segment("..")` normalizes `".."` to `"untitled"` rather than failing closed like `Content.in_folder`. Empty segments and `"."` are skipped.

2. **Extract `safe_suffixed_segment` into `domain/path_utils.py`**: Both `CourseRepository._get_module_content` (`<module_name>-[<module.id>]`), `MoodleAdapter` inline `.html` naming (`_safe_suffixed_html`), and leaf collision suffixing (`<stem> (n)<ext>`) need to truncate the prefix within a `100 - len(suffix)` budget while preserving the suffix and OS safety rules (`WINDOWS_RESERVED`, trailing dots/spaces). Extracting `safe_suffixed_segment(prefix, suffix, max_len=100)` into `domain/path_utils.py` keeps the logic pure (`domain/` has zero outward imports) and avoids duplicating truncation arithmetic between `application/` and `infrastructure/`.

3. **Deduplicate case-insensitively in `IDATSync.get_course_remote_tree`, enforce strictly in `SyncDiff.from_trees`**:
   - `IDATSync.get_course_remote_tree` is the single choke point where course materials and recordings merge before diffing and UI reporting (`CourseFilesDiscovered`). Keying seen paths by `content.path.as_posix().casefold()` handles Windows/macOS case collisions consistently across all CI OSes while leaving non-colliding and first-occurrence paths untouched.
   - Colliding leaf filenames split into `(path.stem, path.suffix)` and probe `n = 2, 3, ...` with `safe_suffixed_segment(stem, f" ({n}){ext}")` via `dataclasses.replace(item, path=item.path.with_name(...))` until unused case-insensitively (handling pre-existing `"file (2).pdf"` files and 100-char stems cleanly).
   - `SyncDiff.from_trees` stays a pure domain check: iterating `remote_contents` and raising `ValueError` on duplicate exact `Path` keys guarantees direct domain callers never silently lose items in `{c.path: c for c in remote_contents}`.

## Risks / Trade-offs

- **Folder structure change (`<module_name>-[<module.id>]`)**: Documented in [ADR-0004](../../../docs/adr/0004-module-id-folders-and-filepath-preservation.md); `tests/test_course_repository.py` hardcoded module paths (`Lecture Notes` -> `Lecture Notes-[201]`, etc.) will be updated to match the new contract. → Acceptable because no production user trees exist yet.
- **Unarchived sibling change `consolidate-labels-and-bibliografia-html`**: Both changes modify `Requirement: Moodle adapter translates vendor curriculum into domain models`. → The delta spec carries forward the full `info.html` / `bibliografia.html` contract alongside `ModuleContent.filepath` so specs stay additive and consistent.
