# Proposal

## Why

`application/` currently imports concrete adapters and downloaders from `infrastructure/`, mixes vendor-specific Moodle HTML rules and SharePoint `office365` SDK handling with use-case orchestration, and embeds composition-root wiring inside `IDATSync`. Inverting dependencies into True Hexagonal Architecture (Ports & Adapters) decouples `domain/` and `application/` from external SDKs, network transport, and disk I/O while fixing a recording week-alignment bug when creation timestamps are missing.

## What Changes

- **BREAKING**: Invert dependencies so `domain/` and `application/` have zero imports from `infrastructure/` or `ui/`.
- **BREAKING**: Define Driven Ports (`CourseGateway`, `RecordingGateway`, `UrlContentStrategy`, `FileSystemPort`, `Downloader[C]`, `DownloadTask[C]`) in `src/idat_sync/application/ports.py`.
- **BREAKING**: Simplify `Course`, `CourseSection`, and `CourseModule` in `src/idat_sync/domain/course.py` to hold `inline_contents: list[StringContent]` and `contents: list[ModuleContent]`, removing Moodle-specific `Assignment`, `modname`, `description`, and `summary` fields from the domain model.
- Move Moodle summary/label/assignment HTML generation and `introattachments` merging into `MoodleAdapter`.
- **BREAKING**: Require a pure `SharepointClient` constructor in `SharepointApiAdapter` and return `list[RecordingContent]` from `fetch_recordings`, computing `Semana {week}` per file only when both `time_created` and `serverRelativeUrl` exist.
- Add pure `SyncDiff.from_trees(remote_contents, local_mtimes)` in `src/idat_sync/domain/sync.py` and `LocalFileSystemAdapter` in `src/idat_sync/infrastructure/filesystem.py`.
- Move `safe_filename` to `src/idat_sync/domain/path_utils.py` (re-exported in `src/idat_sync/utils.py`).
- **BREAKING**: Extract object-graph wiring to `create_sync_service(credentials)` in `src/idat_sync/bootstrap.py` and inject ports into `CourseRepository`, `DownloadManager`, and `IDATSync`.
- Update `agent-os/standards/` (`global/layer-boundaries`, `backend/client-adapter-acl`, `backend/content-strategy-dispatch`, `testing/composition-and-seams`).

## Non-Goals

- Refactoring `AuthProvider` (`src/idat_sync/infrastructure/auth.py`) behind an authentication port.
- Replacing `ProgressInterceptor` (`sys.stdout` redirection in `src/idat_sync/ui/`).

## Capabilities

### New Capabilities
- `course-synchronization`: Hexagonal ports, anti-corruption adapter transformations for `Course` and `RecordingContent`, pure `SyncDiff` computation, and composition-root wiring.

### Modified Capabilities

## Impact

- **Code**: `src/idat_sync/domain/`, `src/idat_sync/application/`, `src/idat_sync/infrastructure/`, `src/idat_sync/bootstrap.py`, `src/idat_sync/utils.py`, `src/idat_sync/ui/sync_frame.py`, `tests/`.
- **Applicable `agent-os` standards (`agent-os/standards/index.yml`)**:
  - `global/layer-boundaries`
  - `backend/client-adapter-acl`
  - `backend/content-strategy-dispatch`
  - `testing/composition-and-seams`
