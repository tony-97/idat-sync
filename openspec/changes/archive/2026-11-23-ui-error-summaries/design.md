# Design

## Context

See `proposal.md` for motivation. Per [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) and [ADR-0008](../../../docs/adr/0008-vite-solidjs-single-file-ui.md), `src/idat_sync/ui/` is a pure-Python presentation layer consuming `IDATSyncError` subclasses from `application/` and serializing state dicts to the bundled SolidJS UI (`src/idat_sync/ui/index.html`). Currently `LoginFrame` and `SyncFrame` pass raw `str(exc)` straight into UI state, while `SyncFrame._ensure_sync_service()` is the only UI error path that does not log raw `IDATSyncError` to `log.err`.

## Goals / Non-Goals

**Goals:**
- Attach lightweight, typed domain/port fact attributes to `IDATSyncError` subclasses in `application/exceptions.py` so `ui/` never parses unstructured log strings.
- Translate every `IDATSyncError` subclass into a concise, jargon-free English summary inside `src/idat_sync/ui/error_format.py` strictly from exception class and typed attributes.
- Keep all raw diagnostic detail in `log.err` with zero duplicate log entries.
- Reuse the existing serialized UI payload schema (`error: str`, `status: str`) with zero frontend or `index.html` rebuilds.

**Non-Goals:**
- Storing English UI phrasing or user-facing sentences inside `application/` or `infrastructure/`.
- Expandable error details, modals, or tooltips in `frontend/`.
- New ADRs or `GLOSSARY.md` edits.

## Decisions

### 1. Typed fact attributes on `IDATSyncError` + declarative UI formatter
- **Choice**: Add optional keyword-only metadata fields with backwards-compatible defaults to the 4 concrete subclasses that need disambiguation in `src/idat_sync/application/exceptions.py` (re-exported via `src/idat_sync/application/ports.py`), using stdlib `typing.Literal` type aliases (`ServiceName = Literal["Moodle", "SharePoint"]`, `AuthFailureReason = Literal["credentials", "mfa", "timeout", "sharepoint"]`, `FileSystemOperation = Literal["access", "write"]`):
  - `AuthenticationError(message: str, *, reason: AuthFailureReason = "credentials")`
  - `CredentialsExpiredError(message: str, *, service: ServiceName | None = None)`
  - `FileSystemError(message: str, *, operation: FileSystemOperation = "access")`
  - `RemoteServiceError(message: str, *, service: ServiceName | None = None, status_code: int | None = None, is_network: bool = False)`
  - `CredentialStoreError` and base `IDATSyncError` remain single-argument exceptions with no extra fields.
- **Alternative considered**: Regex and substring parsing over `str(exc)` inside `ui/error_format.py`. Rejected because parsing raw English log strings creates an invisible string contract across layers and causes real false positives (e.g., a folder named `write_notes` matching `"write"` or a filename containing `500` matching `\b([45]\d{2})\b`). Putting UI sentences directly on `IDATSyncError` was also rejected to preserve strict separation of presentation copy in `ui/` per `global/layer-boundaries`.
- **Raise-site metadata mapping**:
  - `src/idat_sync/infrastructure/sharepoint_auth.py`: passes `reason="mfa"` on empty MFA code, `reason="timeout"` on `TimeoutError`, and `reason="sharepoint"` on generic browser auth failures (`auth_service.py` and `moodle_adapter.py` keep default `reason="credentials"`).
  - `src/idat_sync/infrastructure/moodle_client.py`: passes `service="Moodle"` on all `CredentialsExpiredError` and `RemoteServiceError` raises (including `invalidtoken`, download 401/403, and WS JSON errors), plus `status_code=exc.response.status_code` / `response.status_code` on HTTP status errors and `is_network=True` on `httpx.RequestError`.
  - `src/idat_sync/infrastructure/sharepoint_client.py`: passes `service="SharePoint"` on all `CredentialsExpiredError` and `RemoteServiceError` raises, plus `status_code=status_code` on `ClientRequestException` (when `isinstance(status_code, int)`) / `httpx.HTTPStatusError`, and `is_network=True` on non-HTTP `httpx.HTTPError`.
  - `src/idat_sync/infrastructure/downloaders.py`: passes `operation="write"` on `_atomic_download` and recording write `OSError` (`mkdir` and `filesystem.py` keep default `operation="access"`); `RecordingDownloader._download_sync` passes `service="SharePoint"` on `CredentialsExpiredError` and `RemoteServiceError`, plus `status_code=cause.code` when `cause` is `urllib.error.HTTPError` and `is_network=True` when `cause` is `urllib.error.URLError | TimeoutError`.
- **Formatter dispatch (`src/idat_sync/ui/error_format.py`)**:
  - `format_ui_error(exc: IDATSyncError) -> str` inspects ONLY `isinstance(exc, ...)` and the structured attributes above (never `str(exc)`):
    - `AuthenticationError`: lookup `exc.reason` in `{"mfa": "Verification code is required", "timeout": "Sign-in timed out — try again", "sharepoint": "SharePoint sign-in failed — try again", "credentials": "Sign-in failed — check your username and password"}`.
    - `CredentialsExpiredError`: `f"{exc.service} credentials expired — sign in again"` if `exc.service` else `"Credentials expired — sign in again"` (always using `GLOSSARY.md` term `Credentials`).
    - `CredentialStoreError`: `"Could not update stored credentials"`.
    - `FileSystemError`: `"Could not save file to disk"` when `exc.operation == "write"`, else `"Could not access local folder"`.
    - `RemoteServiceError`: if `exc.status_code is not None` → `f"{exc.service or 'Server'} error (HTTP {exc.status_code})"`; elif `exc.is_network` → `f"{exc.service or 'Network'} connection error"`; else `f"{exc.service} error"` if `exc.service` else `"Remote service error"`.
    - Fallback `IDATSyncError`: `"Unexpected sync error"`.

### 2. Single-source raw logging in `SyncFrame._ensure_sync_service()`
- **Choice**: Log raw `IDATSyncError` via `logging.getLogger(__name__).error("Sync setup failed: %s", exc)` inside `SyncFrame._ensure_sync_service()` before closing any newly created `IDATSync` and re-raising.
- **Rationale**: `TaskDownloadFailed` and `CourseSyncCompleted` are already logged by `log_sync_event`, `JsonCredentialStore.save()`/`.clear()` already log via `_logger.exception`, and `LoginFrame.do_login()` already logs via `_logger.error`. Logging inside `_set_error_status()` would double-log mid-sync `CredentialsExpiredError` (which `IDATSync.sync_course` logs via `CourseSyncCompleted` before re-raising); logging only in `_ensure_sync_service()` guarantees every error is written to `log.err` exactly once.

### 3. Per-file vs. SyncBar summary formatting
- **Choice**:
  - `SyncFrame._with_log_hint(message: str) -> str` formats `f"{message}. Check {self.config.log_file.name} for details."` in one place.
  - `SyncFrame._on_download_failed` passes `format_ui_error(error)` directly to `CourseTree.set_file_status` (short, scannable, no `"Check log.err"` repetition on every row).
  - `SyncFrame._run_sync` computes `failed_count = self.course_tree.activity_to_dict()["failed_count"]` after `sync_courses`: when `first_error is not None`, sets `self.error` to `self._with_log_hint(f"{failed_count} {'file' if failed_count == 1 else 'files'} failed to download")` if `failed_count > 0`, else `self._with_log_hint(format_ui_error(first_error))`.
  - `SyncFrame._set_error_status` sets `self.error = summary` on `CredentialsExpiredError` (no log-file hint) and `self._with_log_hint(summary)` on non-auth `IDATSyncError`, and sets `self.status` to `"Credentials expired. Sign in again."` or `f"Sync error: {summary}"`.
  - `LoginFrame` stores `format_ui_error(exc)` directly in `self.error` (it does not hold `SyncConfig`).

### 4. Modules Touched & Confirmed Test Seams
- **Touched modules**:
  - `src/idat_sync/application/exceptions.py`
  - `src/idat_sync/application/ports.py` (re-export type aliases alongside exception classes)
  - `src/idat_sync/infrastructure/moodle_client.py`
  - `src/idat_sync/infrastructure/sharepoint_client.py`
  - `src/idat_sync/infrastructure/sharepoint_auth.py`
  - `src/idat_sync/infrastructure/downloaders.py`
  - `src/idat_sync/ui/error_format.py` (new)
  - `src/idat_sync/ui/login_frame.py`
  - `src/idat_sync/ui/sync_frame.py`
- **Confirmed test seams**:
  - `idat_sync.application.exceptions`: unit tests verifying default and explicit structured attributes (`reason`, `service`, `operation`, `status_code`, `is_network`) with zero forbidden imports.
  - `MoodleTokenClient` / `MoodleClient` (`tests/test_moodle_client.py`): verifies `service="Moodle"`, `status_code`, and `is_network` across HTTP, network, invalid JSON, `invalidtoken`, and file download errors.
  - `SharepointClient` (`tests/test_sharepoint_adapter.py`): verifies `service="SharePoint"`, `status_code`, and `is_network` across `ClientRequestException`, `httpx.HTTPStatusError`, `httpx.RequestError`, and expired personal-site checks.
  - `SharepointBrowserAuth` (`tests/test_sharepoint_auth.py`): verifies `AuthenticationError.reason` for `"mfa"`, `"timeout"`, and `"sharepoint"`.
  - `MoodleDownloader` / `RecordingDownloader` (`tests/test_sync_pipeline.py`): verifies `FileSystemError.operation == "write"` vs. `"access"` and `RecordingDownloader` `service="SharePoint"`, `status_code`, and `is_network`.
  - `idat_sync.ui.error_format.format_ui_error` (`tests/test_auth.py`): pure unit tests covering every exception subclass, structured attribute combination, and regression cases proving raw `str(exc)` paths/URLs/numbers do not cause false matches.
  - `LoginFrame` (`tests/test_auth.py`): verifies summarized `frame.error` on `__init__` `CredentialStoreError` and `do_login` `AuthenticationError` / `RemoteServiceError` / `CredentialStoreError` while raw errors stay in logs.
  - `SyncFrame` (`tests/test_auth.py`): verifies per-file summarized errors, post-sync `SyncBar` failed-file count vs. zero-file fallback with `log_file.name` hint, `_set_error_status` auth vs. non-auth summary/status, and single raw error log in `_ensure_sync_service()`, using spec-constrained `MagicMock(spec=IDATSync)`.

## Risks / Trade-offs

- **Opaque third-party `yt-dlp` errors**: When `yt_dlp.utils.DownloadError` does not carry a `urllib.error.HTTPError` or `URLError`/`TimeoutError` in `exc_info`, `RecordingDownloader` still sets `service="SharePoint"`, so `format_ui_error` cleanly reports `"SharePoint error"` while full yt-dlp output remains in `log.err`.
- **No in-UI technical details**: Power users must open `log.err` to inspect exact HTTP URLs or stack traces. → Mitigated by appending `Check {config.log_file.name} for details.` on non-auth `SyncBar`/onboarding errors.
- **No new ADR**: Lightweight optional attributes on existing port-boundary exceptions and a pure UI formatting helper; does not meet the ADR threshold.
