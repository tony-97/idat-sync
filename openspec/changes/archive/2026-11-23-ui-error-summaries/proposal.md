# Proposal

## Why

The UI currently displays raw `str(exc)` values from `IDATSyncError` exceptions in file rows, the Activity panel, `SyncBar`, onboarding cards, and the login screen—exposing SharePoint/Moodle API URLs, Python tuples, local paths, and internal jargon (`empty token`, `storage state`, `mobile service`). Users need concise, readable error summaries in the UI while full technical details remain recorded in `log.err`, without coupling the UI to fragile regex or substring parsing over raw log messages.

## What Changes

- Add typed, optional keyword-only structured metadata fields to `IDATSyncError` subclasses in `src/idat_sync/application/exceptions.py` (re-exported via `src/idat_sync/application/ports.py`):
  - `AuthenticationError(message, *, reason: AuthFailureReason = "credentials")`
  - `CredentialsExpiredError(message, *, service: ServiceName | None = None)`
  - `FileSystemError(message, *, operation: FileSystemOperation = "access")`
  - `RemoteServiceError(message, *, service: ServiceName | None = None, status_code: int | None = None, is_network: bool = False)`
  - Keep `CredentialStoreError` and base `IDATSyncError` unchanged.
- Populate those structured domain/port facts (never UI phrasing) at infrastructure `raise` sites in `src/idat_sync/infrastructure/moodle_client.py`, `src/idat_sync/infrastructure/sharepoint_client.py`, `src/idat_sync/infrastructure/sharepoint_auth.py`, and `src/idat_sync/infrastructure/downloaders.py` (`auth_service.py`, `moodle_adapter.py`, `credential_store.py`, and `filesystem.py` rely on safe class defaults).
- Add a pure-Python UI error formatter (`src/idat_sync/ui/error_format.py`) exposing `format_ui_error(exc: IDATSyncError) -> str` that maps every `IDATSyncError` subclass strictly from its exception type and structured attributes (zero regex or substring inspection of `str(exc)`) to a concise, jargon-free English summary using `GLOSSARY.md` vocabulary (`Credentials`, never `Session`).
- Update `LoginFrame` (`src/idat_sync/ui/login_frame.py`) to store `format_ui_error(exc)` in `self.error` for both `__init__` (`CredentialStoreError`) and `do_login` (`IDATSyncError`), while keeping raw `exc` logged via `_logger.error("Login error: %s", exc)` and `JsonCredentialStore`.
- Update `SyncFrame` (`src/idat_sync/ui/sync_frame.py`) to:
  - Record `format_ui_error(error)` on failed file rows in `CourseTree` (no URLs, tuples, file paths, or per-row log hint).
  - Display `"1 file failed to download. Check {log_file.name} for details."` / `"{n} files failed to download. Check {log_file.name} for details."` in `self.error` (`SyncBar`) after a sync with file failures, falling back to `"{format_ui_error(first_error)}. Check {log_file.name} for details."` when `failed_count == 0`, sharing a small `_with_log_hint()` helper.
  - Format `self.error` and `self.status` in `_set_error_status` using `format_ui_error(exc)`, appending `"Check {log_file.name} for details."` only for non-auth errors.
  - Log raw `IDATSyncError` once in `_ensure_sync_service()` so startup/load failures are written to `log.err` without double-logging mid-sync `CredentialsExpiredError`.
- Update existing client/auth/downloader/UI tests and add unit tests covering structured exception fields, `format_ui_error`, `SyncFrame`, and `LoginFrame` seams.

## Non-Goals

- No UI phrasing or English user-facing copy inside `application/` or `infrastructure/`.
- No expandable "Show details" UI, modal, tooltip, or changes to `frontend/` or `src/idat_sync/ui/index.html`.
- No changes to `domain/`, `application/events.py`, `application/sync_service.py`, or `log_sync_event` output format.
- No `GLOSSARY.md` edits or new ADRs (pure typed exception metadata + UI presentation formatting; easily reversible).
- No localization/i18n framework beyond matching the existing English UI copy.

## Capabilities

### New Capabilities

*(None)*

### Modified Capabilities

- `authentication`: `AuthenticationError` and `CredentialsExpiredError` carry typed `reason` / `service` facts; `LoginFrame` and `SyncFrame` store concise `format_ui_error(exc)` summaries instead of raw `str(exc)` while logging raw startup errors once in `SyncFrame._ensure_sync_service()`.
- `course-synchronization`: Add structured fact fields on `IDATSyncError` subclasses, populate them at infrastructure `raise` sites, add the attribute-based `format_ui_error` UI summary contract, and update `SyncFrame` completion/file error reporting to show concise per-file summaries and failed-file counts with a `log.err` hint on `SyncBar`.

## Applicable Agent OS Standards

- `global/layer-boundaries` (`agent-os/standards/global/layer-boundaries.md`): Keeps structured port/domain facts in `application/exceptions.py` and English presentation formatting strictly inside `src/idat_sync/ui/` with zero `idat_sync.infrastructure` imports in `ui/` and zero third-party SDK imports in `application/`.
- `testing/composition-and-seams` (`agent-os/standards/testing/composition-and-seams.md`): Verifies behavior at confirmed client/auth/downloader, exception, formatter, and pure UI controller seams with spec-constrained mocks.

## Impact

- **Code**: `src/idat_sync/application/exceptions.py`, `src/idat_sync/application/ports.py`, `src/idat_sync/infrastructure/moodle_client.py`, `src/idat_sync/infrastructure/sharepoint_client.py`, `src/idat_sync/infrastructure/sharepoint_auth.py`, `src/idat_sync/infrastructure/downloaders.py`, `src/idat_sync/ui/error_format.py` (new), `src/idat_sync/ui/login_frame.py`, `src/idat_sync/ui/sync_frame.py`.
- **Tests**: `tests/test_auth.py`, `tests/test_moodle_client.py`, `tests/test_sharepoint_adapter.py`, `tests/test_sharepoint_auth.py`, `tests/test_sync_pipeline.py`.
- **Dependencies / Frontend**: None.
