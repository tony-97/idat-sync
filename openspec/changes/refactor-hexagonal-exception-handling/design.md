# Design

## Context

See `proposal.md` for motivation and scope, [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) for hexagonal boundaries and exception translation rules, `agent-os/standards/global/layer-boundaries.md` and `agent-os/standards/backend/client-adapter-acl.md` for package and ACL contracts, and `.scratch/exceptions-in-hexagonal-architecture/research.md` for primary-source analysis and the pre-refactor audit.

## Goals / Non-Goals

**Goals:**
- Route all external, authentication, credential-storage, and filesystem failures across driven ports through a single pure-Python `IDATSyncError(Exception)` hierarchy in `src/idat_sync/application/exceptions.py`.
- Give UI, CLI, and Colab callers exact, readable user-facing error messages without `httpx` MDN documentation footers, `ExceptionGroup` wrappers, or silent `bool` failure collapse.
- Keep `asyncio.CancelledError` (`BaseException`) unblocked across fault-isolation blocks while cleaning up `.part` files, browser contexts, and HTTP transports on abort.
- Preserve cause chains (`raise ... from exc`) for debug logging while suppressing secret-bearing tracebacks (`raise ... from None`) when redacting Moodle tokens.

**Non-Goals:**
- Wrapping domain/path invariant `ValueError` checks (`Content.in_folder`, `SyncDiff.from_trees`, `CourseRepository._safe_subfolder`) or `SyncConfig` validation errors in `IDATSyncError`.
- Maintaining backward-compatible `bool` returns on `AuthService.login` or `CredentialStorePort.save`.

## Decisions

### 1. Dedicated `application/exceptions.py` with `IDATSyncError(Exception)`
- **Choice**: Create `src/idat_sync/application/exceptions.py` defining `IDATSyncError(Exception)` (inheriting directly from `Exception`, not `RuntimeError`) and five subclasses: `AuthenticationError`, `CredentialsExpiredError`, `CredentialStoreError`, `RemoteServiceError`, and `FileSystemError`. Re-export all six in `src/idat_sync/application/ports.py` (`__all__`).
- **Rationale**: Keeps `ports.py` focused on port interfaces and credential shapes while giving future contributors a clean home for new port exceptions. Using `Exception` rather than `RuntimeError` avoids misclassifying expected external/user faults as internal runtime errors.
- **Alternatives considered**: Keeping exceptions inside `ports.py` or subclassing `RuntimeError` — rejected to prevent cluttering `ports.py` and conflating domain/port contracts with `RuntimeError`.

### 2. Raising `AuthService.login` and `CredentialStorePort.save` with `TaskGroup` unwrapping
- **Choice**: Refactor `CredentialStorePort.save` / `JsonCredentialStore.save` and `AuthService.login` to return `None` and raise `CredentialStoreError` or `AuthenticationError` / `RemoteServiceError` on failure. Inside `AuthService.login`, catch `except* IDATSyncError as eg: raise eg.exceptions[0] from eg` so the caller gets the first specific port error instead of an `ExceptionGroup`, while non-`IDATSyncError` crashes still cancel the sibling task and propagate.
- **Rationale**: Eliminates the lossy `bool` return that hid whether Moodle, SharePoint, MFA, or disk writes failed, and removes duplicate `ExceptionGroup` unpacking from `LoginFrame`, `scripts/login_cli.py`, and `colab.ipynb`.
- **Alternatives considered**: Returning an error enum/result wrapper or keeping `bool` alongside logging — rejected as unnecessary boilerplate when exceptions are the native Python port failure channel.

### 3. Translating vendor and OS errors at the `*Client` / `*Adapter` / `*Downloader` / Store boundaries
- **Choice**:
  - `MoodleTokenClient.request_token`: map `httpx.HTTPStatusError`, `httpx.RequestError`, and JSON `ValueError` to `RemoteServiceError(...) from exc`; `MoodleTokenAdapter.fetch_token` maps invalid/error JSON payloads to `AuthenticationError` and lets `RemoteServiceError` propagate.
  - `SharepointBrowserAuth.fetch_storage_state`: map `playwright.async_api.TimeoutError` to `AuthenticationError("SharePoint sign-in timed out.") from exc` and other runtime `Exception`s to `AuthenticationError("SharePoint browser authentication failed") from exc` while keeping `asyncio.CancelledError` intact.
  - `MoodleClient` (`call_ws`, `download_file`) and `SharepointClient` (`create`, `_await_query`, `search_for_recordings`): map auth failures (`401`/`403`, `invalidtoken`, empty `personal_site.url`, uninitialized `search_client`) to `CredentialsExpiredError`, and non-auth HTTP/WS/SDK/JSON failures to `RemoteServiceError` (in `MoodleClient.download_file`, redact `wstoken`/`token` params and raise `from None`; in `call_ws`, format HTTP status without `httpx`'s MDN footer).
  - `RecordingDownloader`: map `401`/`403` `DownloadError` to `CredentialsExpiredError`, other `DownloadError` to `RemoteServiceError`, and `OSError` to `FileSystemError`.
  - `JsonCredentialStore`: `save` and `clear` raise `CredentialStoreError(...) from exc` on `OSError` (`save` unlinks `.part`); `load` keeps returning `None`.
  - `LocalFileSystemAdapter` (`scan_mtimes`, `ensure_dir`) and `_atomic_download`: map `OSError` to `FileSystemError(...) from exc` (`read_bytes` keeps returning `None`).
- **Rationale**: `MoodleDownloader`, `SharepointDownloader`, and `bootstrap` invoke `*Client` directly without going through `*Adapter`, so translating HTTP/SDK exceptions once in `*Client` avoids duplicating `401`/`403` and transport-error mapping across five callers.
- **Alternatives considered**: Duplicating HTTP/SDK exception translation in every `*Adapter` and `*Downloader` — rejected for code duplication.

### 4. Narrowing application, event, UI, CLI, and notebook handlers to `IDATSyncError`
- **Choice**: Narrow `TaskDownloadFailed.error` and `CourseSyncCompleted.error` to `IDATSyncError` (`IDATSyncError | None`). Catch `RemoteServiceError` in `CourseRepository.list_url_content` (leaving `CredentialsExpiredError` and `ValueError` to propagate). Catch `CredentialsExpiredError` (abort) and `(RemoteServiceError, FileSystemError)` / `IDATSyncError` (isolate) in `IDATSync`. Update `LoginFrame` (`__init__` around `logout()` and `do_login`), `SyncFrame` (`_ensure_sync_service` raising `IDATSyncError`, `load_idat_sync`, `do_sync`), `scripts/login_cli.py`, `scripts/moodle_json.py`, and `colab.ipynb` to catch `IDATSyncError` and present `str(exc)`.
- **Rationale**: Ensures expected operational failures are handled uniformly while programming bugs (`TypeError`, `KeyError`) or invariant violations (`ValueError`) fail loudly instead of being silently swallowed or masked as sync errors.
- **Alternatives considered**: Continuing to catch broad `Exception` across all application and UI boundaries — rejected because it hides bugs and leaks raw vendor strings into the UI.

### 5. Confirmed Modules Touched and Public Test Seams
- **Modules touched**: `src/idat_sync/application/exceptions.py` (new), `ports.py`, `auth_service.py`, `course_repository.py`, `events.py`, `sync_service.py`; `src/idat_sync/infrastructure/credential_store.py`, `filesystem.py`, `moodle_client.py`, `moodle_adapter.py`, `sharepoint_auth.py`, `sharepoint_client.py`, `downloaders.py`; `src/idat_sync/ui/login_frame.py`, `sync_frame.py`; `scripts/login_cli.py`, `scripts/moodle_json.py`, `colab.ipynb`.
- **Public test seams**:
  - `idat_sync.application.exceptions` & `idat_sync.application.ports` (`tests/test_auth.py`, `tests/test_architecture.py`)
  - `AuthService`, `JsonCredentialStore`, `SharepointBrowserAuth`, `LoginFrame`, `SyncFrame` (`tests/test_auth.py`)
  - `MoodleClient`, `MoodleTokenClient` (`tests/test_moodle_client.py`)
  - `MoodleAdapter`, `MoodleTokenAdapter` (`tests/test_moodle_adapter.py`)
  - `SharepointClient`, `SharepointApiAdapter` (`tests/test_sharepoint_adapter.py`)
  - `CourseRepository` (`tests/test_course_repository.py`)
  - `IDATSync`, `LocalFileSystemAdapter`, `MoodleDownloader`, `SharepointDownloader`, `StringDownloader`, `RecordingDownloader`, `log_sync_event` (`tests/test_sync_pipeline.py`)
  - `scripts/login_cli.py`, `scripts/moodle_json.py` (`tests/test_real_data.py` / `tests/test_auth.py`)

## Risks / Trade-offs

- **[Risk] Catching `IDATSyncError` instead of broad `Exception` in `LoginFrame`, `SyncFrame`, `IDATSync`, and `CourseRepository` may let an unmapped adapter bug surface as an unhandled exception** → Mitigation: Translate all known vendor/HTTP/SDK/OS failure modes (`httpx`, `office365`, `playwright`, `yt_dlp`, `json`, `OSError`) at the infrastructure boundary and test each client/adapter failure seam directly.
- **[Risk] Changing `IDATSyncError` base from `RuntimeError` to `Exception` breaks any caller catching `RuntimeError`** → Mitigation: No backward compatibility is preserved; update all tests and callers across `src/`, `tests/`, `scripts/`, and `colab.ipynb` in vertical slices.
- **[Risk] `LoginFrame.__init__` calling `self.auth.logout()` on a locked `session.json` raises `CredentialStoreError` before widgets render** → Mitigation: Initialize `self.error_var` before `self.auth.logout()` and catch `CredentialStoreError` into `self.error_var`.
