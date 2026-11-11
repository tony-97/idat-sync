# Proposal

## Why

A two-axis code review on the hexagonal architecture change revealed that `DownloadManager` in `application/` preserved fallback direct filesystem I/O (`Path.rglob`, `Path.stat`, `Path.exists`), partially bypassing the `FileSystemPort` abstraction and violating layer boundaries. Additionally, `MoodleAdapter` exposed a public method returning raw vendor dictionaries, and `GLOSSARY.md` lacked definitions for specialized content types (`StringContent` and `RecordingContent`). Fixing these issues completes the hexagonal port isolation and seals the Anti-Corruption Layer.

## What Changes

- **BREAKING**: Require `file_system: FileSystemPort` as a non-nullable parameter in `DownloadManager.__init__` and remove all direct disk I/O fallback logic (`root_path.rglob`, `p.stat`, `local_path.exists`, `local_path.stat`) from `src/idat_sync/application/download_manager.py`.
- Seal the Anti-Corruption Layer by privatizing `MoodleAdapter.get_assignments_by_course` to `_get_assignments_by_course` in `src/idat_sync/infrastructure/moodle_adapter.py`.
- Update `TestSyncPipeline.setUp` and `DownloadManager` instantiations in `tests/test_sync_pipeline.py` to inject `LocalFileSystemAdapter`.
- Enrich `GLOSSARY.md` with canonical definitions for `StringContent` and `RecordingContent`.

## Non-Goals

- Reverting the proposal word-count configuration change in `openspec/config.yaml`.
- Introducing an authentication port for `AuthProvider`.
- Modifying UI components or downloader implementations.

## Capabilities

### New Capabilities

- `course-synchronization`: Enforces mandatory `FileSystemPort` delegation in `DownloadManager`, eliminates raw vendor dict leakage from `MoodleAdapter`, and documents specialized content types in the ubiquitous domain language.

### Modified Capabilities

## Impact

- **Code**: `src/idat_sync/application/download_manager.py`, `src/idat_sync/infrastructure/moodle_adapter.py`, `tests/test_sync_pipeline.py`, `GLOSSARY.md`.
- **Applicable `agent-os` standards (`agent-os/standards/index.yml`)**:
  - `global/layer-boundaries`
  - `backend/client-adapter-acl`
  - `testing/composition-and-seams`
