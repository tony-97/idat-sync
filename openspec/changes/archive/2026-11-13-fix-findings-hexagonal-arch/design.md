# Design

## Context

See `proposal.md` for motivation and [`docs/adr/0001-hexagonal-ports-and-adapters.md`](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) for the foundational architectural decision. During the two-axis code review of `feat: implement true hexagonal architecture`, two architectural boundary breaches were detected: `DownloadManager` preserved optional `file_system: FileSystemPort | None = None` with direct filesystem fallback logic in `application/`, and `MoodleAdapter` exposed a public method returning raw vendor dicts (`get_assignments_by_course`). In addition, `GLOSSARY.md` lacked definitions for specialized content types.

## Goals / Non-Goals

**Goals:**
- Eliminate all direct disk I/O from `src/idat_sync/application/download_manager.py` by making `FileSystemPort` a mandatory injected collaborator.
- Seal the Anti-Corruption Layer on `MoodleAdapter` by keeping internal vendor data processing private.
- Align `GLOSSARY.md` with the ubiquitous domain language used across content downloaders and adapters.

**Non-Goals:**
- Altering the behavior of `FileSystemPort` or `LocalFileSystemAdapter`.
- Reverting configuration rules in `openspec/config.yaml`.

## Decisions

1. **Mandatory Constructor Injection for `FileSystemPort` in `DownloadManager`**:
   - Governed by [`docs/adr/0001-hexagonal-ports-and-adapters.md`](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) and `agent-os/standards/global/layer-boundaries.md`.
   - Change `DownloadManager.__init__` to `(self, strategies: Sequence[Downloader], file_system: FileSystemPort)`.
   - Remove the fallback branches in `diff_trees` and `should_download`. All file scanning, existence checks, and mtime comparisons must unconditionally invoke `self.file_system`.
   - *Alternative considered*: Retaining `file_system: FileSystemPort | None = None` for backward compatibility with un-migrated test cases. Rejected because it violates hexagonal layer boundaries and introduces code duplication between `application/` and `infrastructure/filesystem.py`.

2. **Privatize `_get_assignments_by_course` on `MoodleAdapter`**:
   - Governed by `agent-os/standards/backend/client-adapter-acl.md`.
   - Rename `get_assignments_by_course` to `_get_assignments_by_course`. Public callers must interact with the domain model through `get_courses_with_contents()`.
   - *Alternative considered*: Preserving `get_assignments_by_course` and wrapping raw dicts into an `Assignment` domain dataclass. Rejected because `Assignment` was intentionally removed from `domain/course.py` to prevent Moodle-specific concepts from leaking into domain entities.

3. **Modules Touched & Confirmed Public Test Seams**:
   - **Modules touched**:
     - `src/idat_sync/application/download_manager.py`
     - `src/idat_sync/infrastructure/moodle_adapter.py`
     - `tests/test_sync_pipeline.py`
     - `GLOSSARY.md`
   - **Confirmed public test seams**:
     - **`DownloadManager` seam**: `tests/test_sync_pipeline.py` tests `DownloadManager` injected with `LocalFileSystemAdapter` against `tempfile.TemporaryDirectory()`.
     - **`MoodleAdapter` seam**: `tests/test_moodle_adapter.py` tests `get_courses_with_contents()` mapping assignments to `inline_contents` without exposing raw vendor dicts.
     - **Domain vocabulary seam**: `GLOSSARY.md` under `### Synchronization & Files`.

## Risks / Trade-offs

- **[Breaking DownloadManager constructor signature in existing tests]** → Update `TestSyncPipeline.setUp` and line 110 in `tests/test_sync_pipeline.py` to supply `LocalFileSystemAdapter()` or mock ports. Note that `bootstrap.py` is already passing `file_system=file_system`.
