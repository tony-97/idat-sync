# Spec Delta

## Purpose

Authenticates users against Moodle and SharePoint/Microsoft SSO, orchestrates concurrent token and browser-cookie acquisition behind hexagonal ports, and persists validated Credentials locally.

## ADDED Requirements

### Requirement: Application-owned authentication orchestration and pure-Python ports
`AuthService` in `src/idat_sync/application/auth_service.py` SHALL accept `(moodle_auth: MoodleTokenPort, sharepoint_auth: SharepointAuthPort, credential_store: CredentialStorePort)` without `SyncConfig`, URL/host strings, or `session_file`. `application/ports.py` SHALL define `CookieData`, `StorageStateData`, `Credentials`, `MoodleTokenPort`, `SharepointAuthPort`, and `CredentialStorePort` with zero `playwright` imports. Verified at the `AuthService` and AST seams.

#### Scenario: Normalizing username and running login providers concurrently
- **WHEN** `AuthService.login(username, password, get_mfa_code)` is called with `"jdoe@idat.pe"`
- **THEN** it strips `"@..."` to `"jdoe"`, runs `asyncio.to_thread(moodle_auth.fetch_token, "jdoe", password)` and `sharepoint_auth.fetch_storage_state("jdoe", password, get_mfa_code)` via `asyncio.gather`, saves `Credentials` via `asyncio.to_thread(credential_store.save, credentials)` only when both succeed, and returns `True` (`False` if either fails or `save` returns `False`)

#### Scenario: Delegating logout, authentication check, and credential retrieval
- **WHEN** `logout()`, `is_authenticated()`, and `get_credentials()` are called on `AuthService`
- **THEN** `logout()` calls `credential_store.clear()`, `get_credentials()` returns `credential_store.load()`, and `is_authenticated()` returns `credential_store.load() is not None`

#### Scenario: Omitting unused login_flow_ended event parameter
- **WHEN** `AuthService.login` and `SharepointAuthPort.fetch_storage_state` signatures are inspected
- **THEN** neither accepts `login_flow_ended`

### Requirement: Moodle token retrieval in MoodleTokenAdapter
`MoodleTokenAdapter` in `src/idat_sync/infrastructure/moodle_client.py` SHALL accept `base_url: str`, normalize trailing slashes, and own `RQ_HEADER`. `fetch_token(username, password)` SHALL POST to `{base_url}/login/token.php` with `service="moodle_mobile_app"` and `RQ_HEADER`, propagate HTTP errors via `raise_for_status()`, print `[!]` diagnostics when `"error"` is present or `"token"` is absent, and return `str | None`. Verified at the `MoodleTokenAdapter` seam.

#### Scenario: Fetching a Moodle mobile token with injected base URL
- **WHEN** `MoodleTokenAdapter("https://lms.example.edu/").fetch_token("jdoe", "pw")` receives `{"token": "tok123"}`
- **THEN** it posts `{"username": "jdoe", "password": "pw", "service": "moodle_mobile_app"}` to `"https://lms.example.edu/login/token.php"` with `headers=RQ_HEADER` and `timeout=120`, calls `raise_for_status()`, and returns `"tok123"`

#### Scenario: Returning None on Moodle error payload and propagating HTTP errors
- **WHEN** `/login/token.php` returns `{"error": "Invalid login"}` or HTTP 500
- **THEN** `fetch_token` prints `[!]` and returns `None` for the JSON error payload, and raises `requests.HTTPError` from `raise_for_status()` on HTTP failure

### Requirement: Browser-based SharePoint SSO and MFA capture
`SharepointBrowserAuth` in `src/idat_sync/infrastructure/auth.py` SHALL accept `(sharepoint_host: str, sharepoint_personal_host: str, email_domain: str)` without defaults. `fetch_storage_state(username, password, get_mfa_code)` SHALL drive `async_playwright` through SharePoint, Microsoft SSO, and MFA, catch `Exception` returning `None` without swallowing `asyncio.CancelledError`, and return `StorageStateData | None`. Verified at the `SharepointBrowserAuth` seam.

#### Scenario: Completing Playwright SharePoint SSO and MFA flow
- **WHEN** `SharepointBrowserAuth(...).fetch_storage_state("jdoe", "pw", get_mfa_code)` runs
- **THEN** it visits `https://{sharepoint_host}/_layouts/15/sharepoint.aspx`, fills `"{username}@{email_domain}"` and password, submits the code from `await get_mfa_code()`, visits `https://{sharepoint_personal_host}/shared`, and returns `await context.storage_state()`

#### Scenario: Swallowing browser runtime exceptions while preserving cancellation
- **WHEN** `async_playwright` raises a runtime `Exception` vs `asyncio.CancelledError`
- **THEN** `fetch_storage_state` closes the context and browser, returns `None` on `Exception`, and propagates `asyncio.CancelledError`

### Requirement: JSON credential persistence and corrupt-file safety
`JsonCredentialStore` in `src/idat_sync/infrastructure/auth.py` SHALL accept `session_file: str | Path`. `save(credentials)` SHALL ensure `Path(session_file).parent` exists and write JSON returning `bool`; `load()` SHALL return `None` when the file is missing, unreadable, invalid JSON, or missing valid non-empty `token` (`str`) and `sharepoint_storage_state` (`dict`); `clear()` SHALL remove the file if present. Verified at the `JsonCredentialStore` seam.

#### Scenario: Saving, loading, and clearing valid Credentials on disk
- **WHEN** `JsonCredentialStore(path)` saves valid `Credentials`, calls `load()`, and calls `clear()`
- **THEN** `save` creates parent directories and returns `True`, `load()` returns the matching `Credentials`, and `clear()` deletes the file so subsequent `load()` returns `None`

#### Scenario: Returning None for corrupt or incomplete credential files
- **WHEN** `session_file` contains invalid JSON, `"{}"`, or an empty/non-string `"token"` or non-dict `"sharepoint_storage_state"`
- **THEN** `JsonCredentialStore.load()` catches `(OSError, json.JSONDecodeError)` and returns `None` without raising

### Requirement: Composition root wiring and UI decoupling for authentication
`create_auth_service(config: SyncConfig) -> AuthService` in `src/idat_sync/bootstrap.py` SHALL wire `MoodleTokenAdapter`, `SharepointBrowserAuth`, and `JsonCredentialStore` using `config` fields. `src/idat_sync/ui/` MUST NOT import `idat_sync.infrastructure`, and `MainApp`, `LoginFrame`, and `SyncFrame` SHALL consume `AuthService` and `Credentials` via `bootstrap` and `application`. Verified at the `bootstrap.create_auth_service` and AST seams.

#### Scenario: Wiring AuthService from SyncConfig in bootstrap
- **WHEN** `create_auth_service(custom_config)` is called
- **THEN** it returns an `AuthService` wired with `MoodleTokenAdapter(custom_config.moodle_base_url)`, `SharepointBrowserAuth(custom_config.sharepoint_host, custom_config.sharepoint_personal_host, custom_config.email_domain)`, and `JsonCredentialStore(custom_config.session_file)`

#### Scenario: Enforcing UI isolation from infrastructure
- **WHEN** all Python modules under `src/idat_sync/ui/` are inspected via AST
- **THEN** none import `idat_sync.infrastructure`

### Requirement: Ubiquitous language for Credentials
`GLOSSARY.md` SHALL define **Credentials** (the Moodle token and SharePoint storage state obtained at login) with forbidden synonyms (`Session`, `auth state`, `login data`), and `AuthService` and store classes SHALL use this vocabulary. Verified at the domain vocabulary seam.

#### Scenario: Consulting GLOSSARY.md for authentication terminology
- **WHEN** `GLOSSARY.md` is inspected
- **THEN** `Credentials` is defined with its domain meaning and `_Avoid_: Session, auth state, login data`
