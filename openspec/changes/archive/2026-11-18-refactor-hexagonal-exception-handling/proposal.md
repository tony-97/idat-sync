# Proposal

## Why

Current authentication and synchronization flows leak raw vendor exceptions (`httpx.HTTPStatusError` with MDN footers, `ClientRequestException`, `yt_dlp.utils.DownloadError`, raw `OSError`), swallow specific login failure reasons into `bool` returns (`AuthService.login`, `CredentialStorePort.save`), and lack a unified port-boundary exception hierarchy. Refactoring to a single hexagonal exception hierarchy gives users clear, meaningful error messages and keeps `application/` and `ui/` decoupled from infrastructure error types without backward-compatibility baggage.

## What Changes

- **Pure-Python exception hierarchy in `application/exceptions.py`**: Define `IDATSyncError(Exception)` (inheriting directly from `Exception`, not `RuntimeError`) with five subclasses: `AuthenticationError`, `CredentialsExpiredError`, `CredentialStoreError`, `RemoteServiceError`, and `FileSystemError`. Re-export all six from `application/ports.py`.
- **BREAKING — Raising `AuthService.login` and `CredentialStorePort.save`**: Remove `bool` failure returns from `AuthService.login(...) -> None` and `CredentialStorePort.save(...) -> None`. `AuthService.login` unwraps `TaskGroup` failures via `except* IDATSyncError as eg: raise eg.exceptions[0] from eg` and raises `AuthenticationError` if either port returns empty data. `JsonCredentialStore.save` and `clear` raise `CredentialStoreError` on `OSError` (`load()` continues returning `Credentials | None`).
- **ACL translation across `infrastructure/`**:
  - `MoodleTokenClient` maps HTTP/request/JSON errors to `RemoteServiceError`; `MoodleTokenAdapter` raises `AuthenticationError` on bad payloads and lets `RemoteServiceError` propagate.
  - `SharepointBrowserAuth` maps `TimeoutError` and other runtime/Playwright errors to specific `AuthenticationError` messages while propagating `asyncio.CancelledError`.
  - `MoodleClient`, `SharepointClient`, and `RecordingDownloader` map auth failures (`401`/`403`, `invalidtoken`, empty `personal_site.url`, uninitialized `search_client`) to `CredentialsExpiredError` and all non-auth HTTP/WS/SDK/JSON/`yt-dlp` errors to `RemoteServiceError` (redacting `wstoken`/`token` with `from None` in `MoodleClient.download_file`).
  - `LocalFileSystemAdapter` (`scan_mtimes`, `ensure_dir`), `_atomic_download`, and `RecordingDownloader` map `OSError` to `FileSystemError` (`read_bytes` continues returning `None`).
- **Narrowed application, event, UI, CLI, and notebook error handling**: `TaskDownloadFailed.error` and `CourseSyncCompleted.error` accept `IDATSyncError`; `CourseRepository`, `IDATSync`, `LoginFrame`, `SyncFrame`, `scripts/login_cli.py`, `scripts/moodle_json.py`, and `colab.ipynb` catch `IDATSyncError` subclasses directly without `ExceptionGroup` unpacking.

## Non-Goals

- Wrapping domain invariant `ValueError` checks (`Content.in_folder`, `SyncDiff.from_trees`, `CourseRepository._safe_subfolder`) or startup `SyncConfig` `ValueError` / `FileNotFoundError` validation in `IDATSyncError`.
- Modifying `GLOSSARY.md` or adding a separate new ADR beyond [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md).

## Applicable Standards

- `global/layer-boundaries`
- `backend/client-adapter-acl`
- `backend/content-strategy-dispatch`
- `testing/composition-and-seams`

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `authentication`: Define the `IDATSyncError` hierarchy in `application/exceptions.py`, change `AuthService.login` and `CredentialStorePort.save` to return `None` and raise `IDATSyncError` subclasses, translate login/store faults cleanly, and catch `IDATSyncError` in `LoginFrame` and `SyncFrame`.
- `course-synchronization`: Translate all client/downloader/filesystem faults into `CredentialsExpiredError`, `RemoteServiceError`, and `FileSystemError`, narrow `SyncEvent.error`, `CourseRepository`, `IDATSync`, CLI scripts, and `colab.ipynb` to `IDATSyncError`, and keep domain `ValueError` invariants unchanged.

## Impact

- `src/idat_sync/application/exceptions.py` (new), `ports.py`, `auth_service.py`, `course_repository.py`, `events.py`, `sync_service.py`
- `src/idat_sync/infrastructure/credential_store.py`, `filesystem.py`, `moodle_client.py`, `moodle_adapter.py`, `sharepoint_auth.py`, `sharepoint_client.py`, `downloaders.py`
- `src/idat_sync/ui/login_frame.py`, `sync_frame.py`, `scripts/login_cli.py`, `scripts/moodle_json.py`, `colab.ipynb`
- Confirmed public test seams: `idat_sync.application.exceptions`, `ports`, `AuthService`, `JsonCredentialStore`, `SharepointBrowserAuth`, `LoginFrame`, `SyncFrame`, `MoodleClient`, `MoodleTokenClient`, `MoodleAdapter`, `MoodleTokenAdapter`, `SharepointClient`, `SharepointApiAdapter`, `CourseRepository`, `IDATSync`, `LocalFileSystemAdapter`, `*Downloader`, `log_sync_event`, CLI scripts (`tests/test_architecture.py`, `test_auth.py`, `test_moodle_client.py`, `test_moodle_adapter.py`, `test_sharepoint_adapter.py`, `test_course_repository.py`, `test_sync_pipeline.py`, `test_real_data.py`).
