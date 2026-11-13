# Design

## Context

See `proposal.md` for motivation and scope, and [docs/adr/0001-hexagonal-ports-and-adapters.md](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) for the four-layer hexagonal architecture. Today `ui/app.py` and `ui/login_frame.py` instantiate and invoke `infrastructure.auth.AuthProvider` directly, `src/idat_sync/utils.py` holds `RQ_HEADER` and `get_sharepoint_cookies`, and `sharepoint_client.py`, `downloaders.py`, and `auth.py` import private `playwright._impl._api_structures` types.

## Goals / Non-Goals

**Goals:**
- Decouple `ui/` completely from `infrastructure/` by introducing `AuthService` in `application/` wired through `bootstrap.create_auth_service(config)`.
- Consolidate Moodle HTTP (`REQUEST_HEADER`, `/login/token.php`) into `MoodleTokenClient` in `moodle_client.py` wrapped by `MoodleTokenAdapter` in `moodle_adapter.py`, while isolating Playwright browser SSO in `SharepointBrowserAuth` and JSON persistence in `JsonCredentialStore` inside `infrastructure/auth.py`.
- Replace all `playwright._impl._api_structures` imports with pure-Python TypedDicts in `application/ports.py` and delete `src/idat_sync/utils.py`.

**Non-Goals:**
- Introducing typed authentication exception classes or changing `login() -> bool` semantics.
- Changing `SyncConfig.session_file`, `IDAT_SYNC_SESSION_FILE`, or `Credentials` JSON field names (`token`, `sharepoint_storage_state`).
- Creating a new ADR (this change applies ADR-0001 without altering long-lived architecture).

## Decisions

1. **Application-layer `AuthService` behind three driven ports (`C1`)**
   - *Decision*: Place `AuthService(moodle_auth: MoodleTokenPort, sharepoint_auth: SharepointAuthPort, credential_store: CredentialStorePort)` in `src/idat_sync/application/auth_service.py` and wire concrete adapters in `bootstrap.create_auth_service(config)`. `AuthService.login(username, password, get_mfa_code)` strips `username.split("@", 1)[0]` only for `asyncio.to_thread(moodle_auth.fetch_token, ...)`, passes the unsplit `username` directly to `sharepoint_auth.fetch_storage_state(...)` via `asyncio.gather`, and saves `Credentials` via `asyncio.to_thread(credential_store.save, ...)` only when both succeed. `SharepointBrowserAuth` uses `username` verbatim when `"@"` is present and appends `@{email_domain}` only when `"@"` is absent.
   - *Rationale*: Mirrors `IDATSync` and `create_sync_service`, removes `infrastructure` imports from `ui/`, preserves user-supplied SharePoint email domains while keeping bare Moodle handles, and makes login orchestration testable in memory with fake ports.
   - *Alternatives considered*: Keeping `AuthProvider` inside `infrastructure/auth.py` (rejected because `ui/` would continue importing `infrastructure/` directly) or splitting `@...` unconditionally for both providers (rejected because it overwrites custom SharePoint domains).

2. **Two-tier `MoodleTokenClient` + `MoodleTokenAdapter` vs. keeping SharePoint SSO out of `SharepointClient`**
   - *Decision*: Rename `RQ_HEADER` to `REQUEST_HEADER` in `src/idat_sync/infrastructure/moodle_client.py`, add stateless single-call `MoodleTokenClient(base_url)` (without `request_delay`) exposing `request_token(username, password) -> dict`, and place `MoodleTokenAdapter(client: MoodleTokenClient)` in `src/idat_sync/infrastructure/moodle_adapter.py` implementing `MoodleTokenPort`. Keep `SharepointBrowserAuth(sharepoint_host, sharepoint_personal_host, email_domain)` in `src/idat_sync/infrastructure/auth.py` instead of `sharepoint_client.py`.
   - *Rationale*: Follows `client-adapter-acl.md` cleanly (`*Client` owns HTTP transport returning raw vendor dicts; `*Adapter` wraps `*Client` via constructor injection without `base_url` and translates `"error"`/`"token"` payloads into `str | None`), avoids adding an invalid `token=None` state to `MoodleClient(token, base_url, request_delay)`, and avoids stalling login with `SyncConfig.request_delay`. By contrast, SharePoint login is an interactive `async_playwright` browser flow with an outer `try ... except Exception:` guard (`asyncio.CancelledError`-safe) and `finally` cleanup, whereas `SharepointClient` is a synchronous `office365` REST SDK client.
   - *Alternatives considered*: Adding a `@staticmethod` or nullable `token` on `MoodleClient` (rejected to avoid half-initialized `MoodleClient` instances) or moving Playwright into `SharepointClient` (rejected to keep `sharepoint_client.py` free of Playwright).

3. **Pure-Python `CookieData`, `StorageStateData`, and `Credentials` in `application/ports.py`**
   - *Decision*: Define `CookieData(TypedDict, total=False)` (covering `name`, `value`, `domain`, `path`, `expires`, `httpOnly`, `secure`, `sameSite`), `StorageStateData(TypedDict, total=False)` (`cookies: list[CookieData]`, `origins: list[dict[str, Any]]`), and `Credentials(TypedDict)` (`token: str`, `sharepoint_storage_state: StorageStateData`) in `application/ports.py`. Replace `playwright._impl._api_structures` imports in `sharepoint_client.py`, `downloaders.py`, and `auth.py`.
   - *Rationale*: Eliminates private Playwright type leakage across layers so `infrastructure/auth.py` becomes the sole module importing `playwright`. `total=False` matches Playwright's `StorageState` / `StorageStateCookie` definitions and supports partial cookie dicts in tests.

4. **Minimal `CredentialStorePort` (`load`, `save`, `clear`), corrupt-file safety, and removing dead `login_flow_ended`**
   - *Decision*: `CredentialStorePort` exposes `load() -> Credentials | None`, `save(credentials: Credentials) -> bool`, and `clear() -> None` (dropping `exists()`). `JsonCredentialStore.load()` opens the file directly inside `try:` (no pre-`try` `.exists()` check), catches `(OSError, json.JSONDecodeError)`, validates a non-empty `str` `"token"` and `dict` `"sharepoint_storage_state"`, and `save()` ensures `Path(session_file).parent` exists; log messages refer to `"credentials file"`. `AuthService.is_authenticated()` delegates to `get_credentials() is not None`. Drop the unused `login_flow_ended` parameter from `AuthService.login`, `SharepointAuthPort`, `SharepointBrowserAuth`, and `LoginFrame`.
   - *Rationale*: Prevents `MainApp` from treating a missing, unreadable, corrupt, or incomplete `session.json` as authenticated (which previously left a blank window) and keeps a dead UI event out of the application port.

5. **Touched Modules & Confirmed Public Test Seams**
   - `src/idat_sync/application/ports.py`, `src/idat_sync/application/auth_service.py`, `src/idat_sync/application/__init__.py` -> tested in `tests/test_auth.py` (`AuthService` seam with fake ports).
   - `src/idat_sync/infrastructure/moodle_client.py` (`REQUEST_HEADER`, `MoodleTokenClient`) -> tested in `tests/test_moodle_client.py` (patching `requests.post`).
   - `src/idat_sync/infrastructure/moodle_adapter.py` (`MoodleTokenAdapter`) -> tested in `tests/test_moodle_adapter.py` (injecting `MagicMock(spec=MoodleTokenClient)`).
   - `src/idat_sync/infrastructure/auth.py` (`SharepointBrowserAuth`, `JsonCredentialStore`), `sharepoint_client.py`, `downloaders.py`, `infrastructure/__init__.py` -> tested in `tests/test_auth.py` (patching `idat_sync.infrastructure.auth.async_playwright`; real `tempfile` + `PermissionError` for `JsonCredentialStore`).
   - `src/idat_sync/bootstrap.py`, `ui/app.py`, `ui/login_frame.py`, `ui/sync_frame.py`, delete `src/idat_sync/utils.py` -> tested in `tests/test_sync_pipeline.py` (`create_auth_service` wiring seam and expanded AST import/removal guards).
   - `GLOSSARY.md` (`Credentials` entry) and `agent-os/standards/` (`global/layer-boundaries.md`, `backend/client-adapter-acl.md`, `testing/composition-and-seams.md`).

## Risks / Trade-offs

- **[Risk] Uncommitted `delegate-transport-to-clients` modifications currently sit in the working tree** → Mitigation: Keep changes strictly additive/scoped to the auth files above and note the pre-existing working tree state before `/opsx-apply` and `/code-review`.
- **[Risk] Calling `AuthService.is_authenticated()` followed by `get_credentials()` parses `session.json` twice during `MainApp.__init__`** → Mitigation: `session.json` is tiny local JSON and only read on startup; keeping `is_authenticated() -> bool` preserves existing `MainApp` call semantics without storing stale state.
- **[Risk] Existing test `test_auth_provider_and_ui_accept_configured_paths_and_hosts` imports `AuthProvider` and patches `idat_sync.utils.async_playwright`** → Mitigation: Replace it cleanly across the six confirmed seams (`test_auth.py`, `test_moodle_client.py`, and `test_sync_pipeline.py`).
