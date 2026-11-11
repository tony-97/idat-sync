# Tasks

## 1. Sync Diff, Ports & Filesystem Vertical Slice

- [x] 1.1 Move `safe_filename` and `WINDOWS_RESERVED` into `src/idat_sync/domain/path_utils.py` (re-exporting in `src/idat_sync/utils.py`) and verify with a unit test at the `domain.path_utils` seam in `tests/test_sync_pipeline.py`.
- [x] 1.2 Implement `SyncDiff.from_trees(remote_contents, local_mtimes)` in `src/idat_sync/domain/sync.py`, define `DownloadTask[C]`, `Downloader[C]`, and `FileSystemPort` in `src/idat_sync/application/ports.py` (re-exporting `DownloadTask` and `Downloader` in `src/idat_sync/infrastructure/downloaders.py`), implement `LocalFileSystemAdapter` in `src/idat_sync/infrastructure/filesystem.py`, update `DownloadManager` and `TaskOrchestrator` to depend only on `application/ports.py` and `domain/`, and verify `tests/test_sync_pipeline.py` passes.

## 2. Curriculum & Recording Adapters Vertical Slice

- [x] 2.1 Update `Course`, `CourseSection`, and `CourseModule` in `src/idat_sync/domain/course.py` (and `src/idat_sync/domain/__init__.py`) to hold `inline_contents: list[StringContent]` and `contents: list[ModuleContent]` without `Assignment`, `modname`, `description`, or `summary`, move summary/label/assign HTML generation and `introattachments` merging into `src/idat_sync/infrastructure/moodle_adapter.py`, and verify `tests/test_moodle_adapter.py` passes at the `MoodleAdapter` seam.
- [x] 2.2 Update `SharepointApiAdapter` in `src/idat_sync/infrastructure/sharepoint_adapter.py` to accept `(client: SharepointClient, semester_start: date = date(2025, 9, 22))` and return `list[RecordingContent]` from `fetch_recordings` (filtering for files with both `time_created` and `serverRelativeUrl` before computing `Semana {week}` per file), and verify `tests/test_sharepoint_adapter.py` passes at the `SharepointApiAdapter` seam.
- [x] 2.3 Add `CourseGateway`, `RecordingGateway`, and `UrlContentStrategy` protocols to `src/idat_sync/application/ports.py`, update `CourseRepository` in `src/idat_sync/application/course_repository.py` to accept `(course_gateway, recording_gateway, url_strategies)` and compose `inline_contents` and resolved URLs, and verify `tests/test_course_repository.py` passes at the `CourseRepository` seam.

## 3. Composition Root, Boundary Verification & Standards

- [x] 3.1 Update `IDATSync` in `src/idat_sync/application/sync_service.py` to accept `(course_repository, download_manager, task_orchestrator, file_system)` and import `safe_filename` from `idat_sync.domain.path_utils`, create `create_sync_service(credentials)` in `src/idat_sync/bootstrap.py`, update `src/idat_sync/ui/sync_frame.py` to call `create_sync_service`, and verify with an `IDATSync` orchestration test and AST import-boundary test in `tests/test_sync_pipeline.py` asserting zero imports from `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `office365`, `yt_dlp`, or `playwright` in `domain/` and `application/`.
- [x] 3.2 Update `agent-os/standards/global/layer-boundaries.md`, `agent-os/standards/backend/client-adapter-acl.md`, `agent-os/standards/backend/content-strategy-dispatch.md`, `agent-os/standards/testing/composition-and-seams.md`, and `agent-os/standards/index.yml` to codify the hexagonal boundaries and verify all four standard files match the updated architecture.
- [x] 3.3 Run the full test suite, typecheck, and lint to verify all checks pass with zero errors.
