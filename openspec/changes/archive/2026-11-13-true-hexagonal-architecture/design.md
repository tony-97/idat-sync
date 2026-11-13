# Design

## Context

See `proposal.md` for motivation and [`docs/adr/0001-hexagonal-ports-and-adapters.md`](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) for the foundational architectural decision. Currently, `application/` imports concrete adapters and downloaders from `infrastructure/`, `application/sync_service.py` imports `safe_filename` from `idat_sync.utils` (which transitively imports `playwright.async_api`), and `CourseRepository` / `DownloadManager` interleave domain transformations with vendor SDK handling and local filesystem reads.

## Goals / Non-Goals

**Goals:**
- Enforce strict inward dependencies: `ui/` and `infrastructure/` depend on `application/`, and `application/` depends only on `domain/` (with `bootstrap.py` as the outer Composition Root).
- Isolate all Moodle HTML artifact rules and SharePoint recording projection rules inside their respective Driven Adapters.
- Make `SyncDiff` computation pure in `domain/sync.py` and isolate local filesystem reads/mkdir calls behind `FileSystemPort`.

**Non-Goals:**
- Introducing an authentication port for `AuthProvider` or replacing `ProgressInterceptor` `sys.stdout` capture in `ui/`.

## Decisions

1. **Hexagonal Dependency Inversion & Composition Root**:
   - Governed by [`docs/adr/0001-hexagonal-ports-and-adapters.md`](../../../docs/adr/0001-hexagonal-ports-and-adapters.md).
   - Driven Ports (`DownloadTask[C]`, `Downloader[C]`, `UrlContentStrategy`, `CourseGateway`, `RecordingGateway`, `FileSystemPort`) live in `src/idat_sync/application/ports.py`.
   - `src/idat_sync/bootstrap.py` defines `create_sync_service(credentials: Credentials) -> IDATSync` to wire `MoodleClient`, `MoodleAdapter`, `SharepointClient`, `SharepointApiAdapter`, `LocalFileSystemAdapter`, and the rate-limit `Downloader` groups.

2. **Vendor-Free `Course` Domain Model**:
   - `MoodleAdapter` converts section summaries (`{name}-[{id}]-resumen.html`), `"label"` modules (`{name}.html` when `description` is non-empty), and `"assign"` intros (`{name}-[{id}]-intro.html`) into `inline_contents: list[StringContent]` and merges `introattachments` into `CourseModule.contents: list[ModuleContent]`.
   - *Alternative considered*: Keeping `modname` and `Assignment` in `domain/course.py` and passing a URL resolver callback into `domain/`. Rejected because `"label"`, `"assign"`, and `cmid` are Moodle-specific schema artifacts rather than domain concepts.

3. **Per-File SharePoint Recording Projection**:
   - `SharepointApiAdapter(client: SharepointClient, semester_start: date = date(2025, 9, 22))` filters `fetch_recordings` for files with both `file.time_created` and `file.serverRelativeUrl` before computing `week = 1 + (file.time_created.date() - self.semester_start).days // 7`, preventing missing timestamps from misaligning subsequent recordings.

4. **Modules Touched**:
   - `src/idat_sync/domain/course.py`, `src/idat_sync/domain/sync.py`, `src/idat_sync/domain/path_utils.py`, `src/idat_sync/domain/__init__.py`
   - `src/idat_sync/application/ports.py`, `src/idat_sync/application/course_repository.py`, `src/idat_sync/application/download_manager.py`, `src/idat_sync/application/task_orchestrator.py`, `src/idat_sync/application/sync_service.py`, `src/idat_sync/application/__init__.py`
   - `src/idat_sync/infrastructure/moodle_adapter.py`, `src/idat_sync/infrastructure/sharepoint_adapter.py`, `src/idat_sync/infrastructure/downloaders.py`, `src/idat_sync/infrastructure/filesystem.py`, `src/idat_sync/infrastructure/__init__.py`
   - `src/idat_sync/bootstrap.py`, `src/idat_sync/utils.py`, `src/idat_sync/ui/sync_frame.py`
   - `tests/test_course_repository.py`, `tests/test_moodle_adapter.py`, `tests/test_sharepoint_adapter.py`, `tests/test_sync_pipeline.py`
   - `agent-os/standards/index.yml`, `agent-os/standards/global/layer-boundaries.md`, `agent-os/standards/backend/client-adapter-acl.md`, `agent-os/standards/backend/content-strategy-dispatch.md`, `agent-os/standards/testing/composition-and-seams.md`

5. **Confirmed Public Test Seams**:
   - **Domain seam (`SyncDiff.from_trees`, `Content.in_folder`, `safe_filename`)**: Pure in-memory unit tests with real domain objects and `dict[Path, int]`.
   - **Adapter seam (`MoodleAdapter`, `SharepointApiAdapter`)**: Inject `MagicMock(spec=MoodleClient)` and `MagicMock(spec=SharepointClient)` and assert domain outputs (`Course` with `inline_contents` and merged attachments, `list[RecordingContent]` with `Semana {week}` paths).
   - **Application orchestration seam (`CourseRepository`, `DownloadManager`, `TaskOrchestrator`, `IDATSync`)**:
     - `CourseRepository`: inject `CourseGateway`, `RecordingGateway`, and `UrlContentStrategy` mocks/stubs with real `Course` trees.
     - `DownloadManager`, `TaskOrchestrator`, and `LocalFileSystemAdapter`: test with real instances, real `Content` objects, custom `Downloader` subclasses, and `LocalFileSystemAdapter` against `tempfile.TemporaryDirectory()` (or a `FileSystemPort` stub).
   - **Architecture boundary seam**: AST/import inspection verifying zero imports from `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `office365`, `yt_dlp`, or `playwright` across `src/idat_sync/domain/` and `src/idat_sync/application/`.

## Risks / Trade-offs

- **[Breaking internal constructor signatures on `CourseRepository`, `DownloadManager`, `IDATSync`, and `SharepointApiAdapter`]** → Update all callers (`bootstrap.py`, `ui/sync_frame.py`, and test suites) in lockstep and re-export `Downloader`, `DownloadTask`, and `safe_filename` from their previous modules for compatibility.
