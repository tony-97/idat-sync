# Spec Delta

## Purpose

Authenticates users against Moodle and SharePoint/Microsoft SSO, orchestrates concurrent token and browser-cookie acquisition behind hexagonal ports, and persists validated Credentials locally.

## ADDED Requirements

### Requirement: Application-owned authentication orchestration and pure-Python ports
`AuthService` in `src/idat_sync/application/auth_service.py` SHALL accept `(moodle_auth: MoodleTokenPort, sharepoint_auth: SharepointAuthPort, credential_store: CredentialStorePort)` without `SyncConfig`, URL/host strings, or `session_file`. `application/ports.py` SHALL define `CookieData`, `StorageStateData`, `Credentials`, `MoodleTokenPort`, `SharepointAuthPort`, and `CredentialStorePort` with zero `playwright` imports. Verified at the `AuthService` and AST seams.

#### Scenario: Normalizing username and running login providers concurrently
- **WHEN** `AuthService.login(username, password, get_mfa_code)` is called with `"jdoe@custom.edu"`
- **THEN** it strips `"@..."` to `"jdoe"` only for `asyncio.to_thread(moodle_auth.fetch_token, "jdoe", password)`, passes the unsplit `"jdoe@custom.edu"` to `sharepoint_auth.fetch_storage_state("jdoe@custom.edu", password, get_mfa_code)` as named tasks inside `asyncio.TaskGroup()` (cancelling any incomplete sibling task and propagating `ExceptionGroup` if either raises), saves `Credentials` via `asyncio.to_thread(credential_store.save, credentials)` only when both succeed, and returns `True` (`False` if either returns falsy or `save` returns `False`)

#### Scenario: Delegating logout, authentication check, and credential retrieval
- **WHEN** `logout()`, `is_authenticated()`, and `get_credentials()` are called on `AuthService`
- **THEN** `logout()` calls `credential_store.clear()`, `get_credentials()` returns `credential_store.load()`, and `is_authenticated()` returns `credential_store.load() is not None`

#### Scenario: Omitting unused login_flow_ended event parameter
- **WHEN** `AuthService.login` and `SharepointAuthPort.fetch_storage_state` signatures are inspected
- **THEN** neither accepts `login_flow_ended`

### Requirement: Moodle token retrieval in MoodleTokenClient and MoodleTokenAdapter
`MoodleTokenClient(base_url: str)` in `src/idat_sync/infrastructure/moodle_client.py` SHALL normalize trailing slashes, own `REQUEST_HEADER`, and expose `request_token(username, password) -> dict[str, Any]` without `request_delay`, POSTing to `{base_url}/login/token.php` with `service="moodle_mobile_app"`, `REQUEST_HEADER`, and `timeout=120`, calling `raise_for_status()`, and returning the raw vendor JSON payload. `MoodleTokenAdapter(client: MoodleTokenClient)` in `src/idat_sync/infrastructure/moodle_adapter.py` SHALL implement `MoodleTokenPort` without `base_url`, delegate to `client.request_token(username, password)`, print `[!]` and return `None` immediately when `"error"` is present in a `dict` payload (even if `"token"` is also present), and require a `dict` payload with a non-empty `str` `"token"` (printing `[!]` and returning `None` when non-`dict`, missing, empty, or non-`str`). Verified at the `MoodleTokenClient` and `MoodleTokenAdapter` seams.

#### Scenario: Fetching a Moodle mobile token with injected base URL
- **WHEN** `MoodleTokenClient("https://lms.example.edu/").request_token("jdoe", "pw")` receives `{"token": "tok123"}` and is wrapped by `MoodleTokenAdapter`
- **THEN** `MoodleTokenClient` posts `{"username": "jdoe", "password": "pw", "service": "moodle_mobile_app"}` to `"https://lms.example.edu/login/token.php"` with `headers=REQUEST_HEADER` and `timeout=120`, calls `raise_for_status()`, returns the raw dict, and `MoodleTokenAdapter.fetch_token("jdoe", "pw")` returns `"tok123"`

#### Scenario: Returning None on Moodle error or missing/invalid payload and propagating HTTP errors
- **WHEN** `MoodleTokenClient.request_token` returns `{"error": "Invalid login"}`, `{"error": "Invalid login", "token": "ignored"}`, `{}`, `{"token": ""}`, `{"token": 123}`, `None`, `[]`, or `"unexpected"`, or `/login/token.php` returns HTTP 500
- **THEN** `MoodleTokenAdapter.fetch_token` prints `[!]` and returns `None` for any error or non-`dict`/missing/empty/non-`str` token payload, and `requests.HTTPError` from `raise_for_status()` propagates on HTTP failure

### Requirement: Browser-based SharePoint SSO and MFA capture
`SharepointBrowserAuth` in `src/idat_sync/infrastructure/sharepoint_auth.py` SHALL accept `(sharepoint_host: str, sharepoint_personal_host: str, email_domain: str)` without defaults. `fetch_storage_state(username, password, get_mfa_code)` SHALL drive `async_playwright` through SharePoint, Microsoft SSO, and MFA, fill `#i0116` with `username` verbatim when `"@"` is present or `f"{username}@{email_domain}"` when `"@"` is absent, wrap `async_playwright()` startup, `launch()`, `new_context()`, and page actions inside `try ... except Exception:` returning `None` while cleaning up any created `context` and `browser` in nested `try ... finally:` (so `browser.close()` runs even if `context.close()` raises) without swallowing `asyncio.CancelledError`, and return `StorageStateData | None`. Verified at the `SharepointBrowserAuth` seam.

#### Scenario: Completing Playwright SharePoint SSO and MFA flow
- **WHEN** `SharepointBrowserAuth(...).fetch_storage_state("jdoe", "pw", get_mfa_code)` runs vs `fetch_storage_state("jdoe@other.edu", "pw", get_mfa_code)`
- **THEN** it visits `https://{sharepoint_host}/_layouts/15/sharepoint.aspx`, fills `"jdoe@{email_domain}"` when `"@"` is absent or `"jdoe@other.edu"` verbatim when `"@"` is present, fills password, submits the code from `await get_mfa_code()`, visits `https://{sharepoint_personal_host}/shared`, and returns `await context.storage_state()`

#### Scenario: Swallowing browser startup and runtime exceptions while preserving cleanup and cancellation
- **WHEN** `async_playwright()`, `launch()`, `new_context()`, `context.close()`, or page interactions raise a runtime `Exception` vs `asyncio.CancelledError`
- **THEN** `fetch_storage_state` closes any created `context` and still closes any created `browser` even if `context.close()` raises, returns `None` on `Exception`, and propagates `asyncio.CancelledError`

### Requirement: JSON credential persistence and corrupt-file safety
`JsonCredentialStore` in `src/idat_sync/infrastructure/credential_store.py` SHALL accept `credentials_file: str | Path` (storing `self.credentials_file`) and log `"credentials file"` in status/error messages. `save(credentials)` SHALL ensure `Path(credentials_file).parent` exists and write JSON returning `bool`; `load()` SHALL open `credentials_file` directly inside `try:` (without a pre-`try` `.exists()` check) and return `None` when the file is missing, unreadable, invalid JSON, or missing valid non-empty `token` (`str`) and `sharepoint_storage_state` (`dict`); `clear()` SHALL remove the file if present. Verified at the `JsonCredentialStore` seam.

#### Scenario: Saving, loading, and clearing valid Credentials on disk
- **WHEN** `JsonCredentialStore(path)` saves valid `Credentials`, calls `load()`, and calls `clear()`
- **THEN** `save` creates parent directories and returns `True`, `load()` returns the matching `Credentials`, and `clear()` deletes the file so subsequent `load()` returns `None`

#### Scenario: Returning None for corrupt, unreadable, or incomplete credential files
- **WHEN** `credentials_file` is unreadable (`OSError`/`PermissionError`), contains invalid JSON, `"{}"`, or an empty/non-string `"token"` or non-dict `"sharepoint_storage_state"`
- **THEN** `JsonCredentialStore.load()` catches `(OSError, json.JSONDecodeError)` and returns `None` without raising

### Requirement: Composition root wiring and UI decoupling for authentication
`create_auth_service(config: SyncConfig) -> AuthService` in `src/idat_sync/bootstrap.py` SHALL wire `MoodleTokenAdapter(MoodleTokenClient(config.moodle_base_url))`, `SharepointBrowserAuth`, and `JsonCredentialStore` using `config` fields. `src/idat_sync/ui/` MUST NOT import `idat_sync.infrastructure`, and `MainApp`, `LoginFrame`, and `SyncFrame` SHALL consume `AuthService` and `Credentials` via `bootstrap` and `application`. `LoginFrame` SHALL keep the Login button disabled whenever trimmed username is blank, password is empty, or authentication is in progress (`is_login`), pass stripped username with verbatim password to `AuthService.login`, ignore blank or concurrent submissions, unpack `ExceptionGroup.exceptions` on failure, and reset `is_login` in `finally:`. Verified at the `bootstrap.create_auth_service`, `LoginFrame`, and AST seams.

#### Scenario: Wiring AuthService from SyncConfig in bootstrap
- **WHEN** `create_auth_service(custom_config)` is called
- **THEN** it returns an `AuthService` wired with `MoodleTokenAdapter(MoodleTokenClient(custom_config.moodle_base_url))`, `SharepointBrowserAuth(custom_config.sharepoint_host, custom_config.sharepoint_personal_host, custom_config.email_domain)`, and `JsonCredentialStore(custom_config.session_file)`

#### Scenario: Disabling Login on blank inputs or active login and guarding submissions
- **WHEN** `LoginFrame` initializes, when trimmed username or raw password is empty vs. non-empty, or while `do_login` is running vs. completes/raises
- **THEN** the Login button starts `"disabled"`, switches to `"normal"` only when trimmed username, non-empty password, and `not is_login` all hold, `do_login` ignores blank or concurrent submissions and passes stripped `username` with unstripped `password` to `AuthService.login`, and `finally:` resets `is_login` while unpacking `ExceptionGroup.exceptions`

#### Scenario: Enforcing UI isolation from infrastructure
- **WHEN** all Python modules under `src/idat_sync/ui/` are inspected via AST
- **THEN** none import `idat_sync.infrastructure`

### Requirement: Ubiquitous language for Credentials
`GLOSSARY.md` SHALL define **Credentials** (the Moodle token and SharePoint storage state obtained at login) with forbidden synonyms (`Session`, `auth state`, `login data`), and `AuthService` and store classes SHALL use this vocabulary. Verified at the domain vocabulary seam.

#### Scenario: Consulting GLOSSARY.md for authentication terminology
- **WHEN** `GLOSSARY.md` is inspected
- **THEN** `Credentials` is defined with its domain meaning and `_Avoid_: Session, auth state, login data`
