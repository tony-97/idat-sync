# authentication Specification

## MODIFIED Requirements

### Requirement: Application-owned authentication orchestration and pure-Python ports
`AuthService` in `src/idat_sync/application/auth_service.py` SHALL accept `(moodle_auth: MoodleTokenPort, sharepoint_auth: SharepointAuthPort, credential_store: CredentialStorePort)` without `SyncConfig`, URL/host strings, or `session_file`. `MoodleTokenPort.fetch_token(username, password) -> str | None` and `AuthService.authenticate(username, password) -> str | None` SHALL be `async def` coroutines. `application/ports.py` SHALL define `CookieData`, `StorageStateData`, `Credentials`, `MoodleTokenPort`, `SharepointAuthPort`, and `CredentialStorePort` with zero `playwright`, `requests`, or `httpx` imports. Verified at the `AuthService` (`src/idat_sync/application/auth_service.py`) and AST (`src/idat_sync/application/ports.py`) seams.

#### Scenario: Normalizing username and running login providers concurrently
- **WHEN** `await AuthService.login(username, password, get_mfa_code)` is called with `"jdoe@custom.edu"`
- **THEN** it strips `"@..."` to `"jdoe"` only for `moodle_auth.fetch_token("jdoe", password)`, passes the unsplit `"jdoe@custom.edu"` to `sharepoint_auth.fetch_storage_state("jdoe@custom.edu", password, get_mfa_code)` as named tasks inside `asyncio.TaskGroup()` without wrapping `moodle_auth.fetch_token` in `asyncio.to_thread` (cancelling any incomplete sibling task and propagating `ExceptionGroup` if either raises), saves `Credentials` via `asyncio.to_thread(credential_store.save, credentials)` only when both succeed, and returns `True` (`False` if either returns falsy or `save` returns `False`)

#### Scenario: Delegating logout, authentication check, and credential retrieval
- **WHEN** `await authenticate("jdoe@custom.edu", "pw")`, `logout()`, `is_authenticated()`, and `get_credentials()` are called on `AuthService`
- **THEN** `await authenticate(...)` awaits `moodle_auth.fetch_token("jdoe", "pw")`, `logout()` calls `credential_store.clear()`, `get_credentials()` returns `credential_store.load()`, and `is_authenticated()` returns `credential_store.load() is not None`

#### Scenario: Omitting unused login_flow_ended event parameter
- **WHEN** `AuthService.login` and `SharepointAuthPort.fetch_storage_state` signatures are inspected
- **THEN** neither accepts `login_flow_ended`

### Requirement: Moodle token retrieval in MoodleTokenClient and MoodleTokenAdapter
`MoodleTokenClient(base_url: str)` in `src/idat_sync/infrastructure/moodle_client.py` SHALL normalize trailing slashes, own `REQUEST_HEADER`, and expose `async def request_token(username, password) -> dict[str, Any]` without `request_delay`, POSTing via `httpx.AsyncClient` to `{base_url}/login/token.php` with `service="moodle_mobile_app"`, `REQUEST_HEADER`, and `timeout=120`, calling `raise_for_status()`, and returning the raw vendor JSON payload. `MoodleTokenAdapter(client: MoodleTokenClient)` in `src/idat_sync/infrastructure/moodle_adapter.py` SHALL implement `MoodleTokenPort` without `base_url`, expose `async def fetch_token(username, password) -> str | None`, await `client.request_token(username, password)`, log a warning with `[!]` via `logging.getLogger(__name__)` without calling `print(...)` and return `None` immediately when `"error"` is present in a `dict` payload (even if `"token"` is also present), and require a `dict` payload with a non-empty `str` `"token"` (logging a warning with `[!]` and returning `None` when non-`dict`, missing, empty, or non-`str`). Verified at the `MoodleTokenClient` (`src/idat_sync/infrastructure/moodle_client.py`) and `MoodleTokenAdapter` (`src/idat_sync/infrastructure/moodle_adapter.py`) seams.

#### Scenario: Fetching a Moodle mobile token with injected base URL
- **WHEN** `await MoodleTokenClient("https://lms.example.edu/").request_token("jdoe", "pw")` receives `{"token": "tok123"}` and is wrapped by `MoodleTokenAdapter`
- **THEN** `MoodleTokenClient` posts `{"username": "jdoe", "password": "pw", "service": "moodle_mobile_app"}` via `httpx.AsyncClient` to `"https://lms.example.edu/login/token.php"` with `headers=REQUEST_HEADER` and `timeout=120`, calls `raise_for_status()`, returns the raw dict, and `await MoodleTokenAdapter.fetch_token("jdoe", "pw")` returns `"tok123"`

#### Scenario: Returning None on Moodle error or missing/invalid payload and propagating HTTP errors
- **WHEN** `await MoodleTokenClient.request_token` returns `{"error": "Invalid login"}`, `{"error": "Invalid login", "token": "ignored"}`, `{}`, `{"token": ""}`, `{"token": 123}`, `None`, `[]`, or `"unexpected"`, or `/login/token.php` returns HTTP 500
- **THEN** `await MoodleTokenAdapter.fetch_token` logs a warning containing `[!]` via `logging.getLogger("idat_sync.infrastructure.moodle_adapter")` and returns `None` for any error or non-`dict`/missing/empty/non-`str` token payload, and `httpx.HTTPStatusError` from `raise_for_status()` propagates on HTTP failure

### Requirement: Pure-Python CredentialsExpiredError exception in application ports
`src/idat_sync/application/ports.py` SHALL define and export `CredentialsExpiredError(RuntimeError)` with zero external SDK or UI framework imports so infrastructure clients can signal expired `Credentials` and application/UI layers can distinguish authentication expiry from transient failures. Verified at the `application.ports` (`src/idat_sync/application/ports.py`) and AST architecture seams.

#### Scenario: Subclassing RuntimeError without vendor imports in ports
- **WHEN** `CredentialsExpiredError` in `src/idat_sync/application/ports.py` is inspected and raised
- **THEN** `issubclass(CredentialsExpiredError, RuntimeError)` is `True`, `"CredentialsExpiredError"` is listed in `__all__`, and `application/ports.py` has zero imports from `requests`, `httpx`, `office365`, `yt_dlp`, `playwright`, or `tkinter`

### Requirement: Resilient SyncFrame recovery without silently wiping Credentials
`SyncFrame` in `src/idat_sync/ui/sync_frame.py` SHALL store injected `credentials`, display `load_idat_sync` and `do_sync` exceptions in `status_var`, reset `is_syncing` in `finally:`, retry `await create_sync_service(self.credentials, self.config)` in `do_sync` whenever `self.idat_sync is None`, and MUST NOT silently delete `Credentials` or auto-switch to `LoginFrame`. Verified at the `SyncFrame` (`src/idat_sync/ui/sync_frame.py`) seam.

#### Scenario: Recovering from a transient startup failure during Sync
- **WHEN** `await SyncFrame.load_idat_sync(credentials)` fails because `await create_sync_service(credentials, self.config)` raises a transient error, leaving `self.idat_sync` as `None`, and the user later selects a folder and invokes `await do_sync()`
- **THEN** `load_idat_sync` writes the error into `status_var`, leaves `Credentials` intact on disk without switching frames, resets `is_syncing` to `False` in `finally:` (re-enabling `Browse...`, `Logout`, and `Sync` once a folder is selected), and `do_sync()` retries `await create_sync_service(self.credentials, self.config)` when `self.idat_sync is None` before iterating `async for event in self.idat_sync.sync_courses(path, selected):`

#### Scenario: Surfacing CredentialsExpiredError in SyncFrame without deleting stored Credentials
- **WHEN** `load_idat_sync` or `do_sync` catches `CredentialsExpiredError` or another runtime `Exception`
- **THEN** `SyncFrame` stays on screen, writes an error message into `status_var` (instructing the user to click `Logout` when `CredentialsExpiredError` occurs), MUST NOT call `auth.logout()` or delete the credentials file, and resets `is_syncing` in `finally:`
