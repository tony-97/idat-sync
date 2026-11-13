# Proposal

## Why

Authentication currently breaks the hexagonal boundary: `src/idat_sync/infrastructure/auth.py` mixes Moodle HTTP token retrieval, JSON credential persistence, and concurrent orchestration in `AuthProvider`, while `src/idat_sync/utils.py` holds `get_sharepoint_cookies` (Playwright SSO/MFA) and `RQ_HEADER`, and `ui/` reaches directly into `infrastructure/auth.py` and private `playwright._impl._api_structures` types. Moving orchestration into an application-layer `AuthService` behind driven ports lets `MoodleTokenClient` join `moodle_client.py` and `MoodleTokenAdapter` join `moodle_adapter.py`, isolates Playwright SSO into `infrastructure/sharepoint_auth.py` and credential persistence into `infrastructure/credential_store.py`, wires auth via `bootstrap.py`, and eliminates `utils.py` and `infrastructure/auth.py`.

## What Changes

- **BREAKING**: Define pure-Python `CookieData`, `StorageStateData`, and `Credentials` TypedDicts plus `MoodleTokenPort`, `SharepointAuthPort`, and `CredentialStorePort` in `src/idat_sync/application/ports.py`.
- **BREAKING**: Add `AuthService` in `src/idat_sync/application/auth_service.py` (exported in `application/__init__.py`) to strip `@...` only for `MoodleTokenPort`, pass the unsplit `username` to `SharepointAuthPort`, run Moodle token retrieval and SharePoint SSO concurrently, and manage `Credentials` via `CredentialStorePort`. Drop the unused `login_flow_ended` parameter from `login` and `SharepointAuthPort.fetch_storage_state`.
- **BREAKING**: Rename `RQ_HEADER` to `REQUEST_HEADER` in `src/idat_sync/infrastructure/moodle_client.py`, add `MoodleTokenClient(base_url)` for raw token HTTP transport, and add `MoodleTokenAdapter(client)` in `src/idat_sync/infrastructure/moodle_adapter.py` implementing `MoodleTokenPort`.
- **BREAKING**: Delete `src/idat_sync/infrastructure/auth.py` (`AuthProvider`) and split its two cohesive responsibilities into `SharepointBrowserAuth` in `src/idat_sync/infrastructure/sharepoint_auth.py` (Playwright SSO/MFA, filling `username` verbatim when `@` is present or falling back to `@{email_domain}`) and `JsonCredentialStore(credentials_file)` in `src/idat_sync/infrastructure/credential_store.py` (`load`, `save`, `clear`, returning `None` on missing/unreadable/corrupt/incomplete JSON and ensuring parent directories on `save`), and update `infrastructure/__init__.py` exports.
- Replace `playwright._impl._api_structures.StorageStateCookie` imports in `sharepoint_client.py` and `downloaders.py` with `CookieData`.
- Add `bootstrap.create_auth_service(config: SyncConfig) -> AuthService`, update `ui/app.py`, `ui/login_frame.py`, and `ui/sync_frame.py` to depend only on `bootstrap` and `application`, and delete `src/idat_sync/utils.py`.
- Document **Credentials** in `GLOSSARY.md` and refresh auth references in `agent-os/standards/global/layer-boundaries.md`, `agent-os/standards/backend/client-adapter-acl.md`, and `agent-os/standards/testing/composition-and-seams.md`.

## Non-Goals

- Changing login return values (`bool`) or introducing typed authentication exceptions (`TODO: raise exceptions` stays deferred).
- Changing `SyncConfig.session_file`, `IDAT_SYNC_SESSION_FILE`, or the `Credentials` JSON keys (`token`, `sharepoint_storage_state`).
- Adding an interactive loading dialog or new ADRs.

## Capabilities

### New Capabilities
- `authentication`: Application `AuthService` orchestration, driven auth/credential ports (`MoodleTokenPort`, `SharepointAuthPort`, `CredentialStorePort`), `MoodleTokenClient`, `MoodleTokenAdapter`, `SharepointBrowserAuth`, `JsonCredentialStore`, and `bootstrap.create_auth_service`.

### Modified Capabilities
- `course-synchronization`: Remove the `idat_sync.utils` re-export, `AuthProvider`, `get_sharepoint_cookies`, `utils.py`, and `infrastructure/auth.py` symbol checks, rename `RQ_HEADER` to `REQUEST_HEADER`, and enforce `ui/` and `playwright` import boundaries.

## Applicable Standards

- `backend/client-adapter-acl`
- `global/layer-boundaries`
- `testing/composition-and-seams`

## Impact

- **Touched modules**: `src/idat_sync/application/ports.py`, `src/idat_sync/application/auth_service.py`, `src/idat_sync/application/__init__.py`, `src/idat_sync/infrastructure/moodle_client.py`, `src/idat_sync/infrastructure/moodle_adapter.py`, `src/idat_sync/infrastructure/sharepoint_auth.py`, `src/idat_sync/infrastructure/credential_store.py`, `src/idat_sync/infrastructure/sharepoint_client.py`, `src/idat_sync/infrastructure/downloaders.py`, `src/idat_sync/infrastructure/__init__.py`, `src/idat_sync/bootstrap.py`, `src/idat_sync/ui/app.py`, `src/idat_sync/ui/login_frame.py`, `src/idat_sync/ui/sync_frame.py`, delete `src/idat_sync/infrastructure/auth.py` and `src/idat_sync/utils.py`, plus `GLOSSARY.md` and `agent-os/standards/`.
- **Public test seams**: `tests/test_auth.py`, `tests/test_moodle_client.py`, `tests/test_moodle_adapter.py`, `tests/test_sync_pipeline.py`.
