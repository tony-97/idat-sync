# Tasks

## 1. Domain Vocabulary & Adapter Boundary

- [x] 1.1 Add `StringContent` and `RecordingContent` definitions under `### Synchronization & Files` in `GLOSSARY.md` and verify all canonical domain terms and avoid lists match the domain model.
- [x] 1.2 Rename `get_assignments_by_course` to private `_get_assignments_by_course` in `src/idat_sync/infrastructure/moodle_adapter.py` and verify `tests/test_moodle_adapter.py` passes at the `MoodleAdapter` public seam.

## 2. FileSystemPort Enforcement Vertical Slice

- [x] 2.1 Require `file_system: FileSystemPort` in `DownloadManager.__init__`, remove all fallback direct disk I/O (`root_path.rglob`, `p.stat`, `local_path.exists`, `local_path.stat`) from `src/idat_sync/application/download_manager.py`, update `TestSyncPipeline.setUp` and line 110 in `tests/test_sync_pipeline.py` to inject `LocalFileSystemAdapter`, and verify `tests/test_sync_pipeline.py` passes.

## 3. Integration & Suite Verification

- [x] 3.1 Run the full test suite (`python -m unittest discover tests` with `PYTHONPATH=src`), import boundary AST verification, and lint/type checks to verify zero regressions.
