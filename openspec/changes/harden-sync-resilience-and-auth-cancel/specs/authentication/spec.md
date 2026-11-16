# authentication Specification Delta

## MODIFIED Requirements

### Requirement: Application-owned authentication orchestration and pure-Python ports
`AuthService` in `src/idat_sync/application/auth_service.py` SHALL accept `(moodle_auth: MoodleTokenPort, sharepoint_auth: SharepointAuthPort, credential_store: CredentialStorePort)` without `SyncConfig`, URL/host strings, or `session_file`. `MoodleTokenPort.fetch_token(username, password) -> str` and `AuthService.authenticate(username, password) -> str` SHALL be `async def` coroutines, and `SharepointAuthPort.fetch_storage_state(username, password, get_mfa_code) -> StorageStateData` SHALL be an `async def` coroutine. `application/ports.py` SHALL define and export `AuthenticationError(RuntimeError)` alongside `CredentialsExpiredError`, `CookieData`, `StorageStateData`, `Credentials`, `MoodleTokenPort`, `SharepointAuthPort`, and `CredentialStorePort` with zero `playwright`, `requests`, or `httpx` imports. `AuthService.login` SHALL execute `authenticate` and `fetch_storage_state` concurrently inside `asyncio.TaskGroup()` and catch `except* AuthenticationError` to return `False` immediately (cancelling the incomplete sibling task whenever either provider raises `AuthenticationError`) while letting non-`AuthenticationError` exceptions propagate as `ExceptionGroup`. Verified at the `AuthService` (`src/idat_sync/application/auth_service.py`) and AST (`src/idat_sync/application/ports.py`) seams.

#### Scenario: Normalizing username and running login providers concurrently
- **WHEN** `await AuthService.login(username, password, get_mfa_code)` is called with `"jdoe@custom.edu"`
- **THEN** it strips `"@..."` to `"jdoe"` only for `moodle_auth.fetch_token("jdoe", password)`, passes the unsplit `"jdoe@custom.edu"` to `sharepoint_auth.fetch_storage_state("jdoe@custom.edu", password, get_mfa_code)` as named tasks inside `asyncio.TaskGroup()` without wrapping `moodle_auth.fetch_token` in `asyncio.to_thread`, saves `Credentials` via `asyncio.to_thread(credential_store.save, credentials)` only when both succeed, and returns `True` (`False` if `credential_store.save` returns `False`)

#### Scenario: Cancelling sibling login provider immediately on AuthenticationError and propagating unexpected errors
- **WHEN** either `moodle_auth.fetch_token` or `sharepoint_auth.fetch_storage_state` raises `AuthenticationError` vs a non-`AuthenticationError` exception (such as `RuntimeError`) while the sibling task is still pending
- **THEN** `asyncio.TaskGroup()` cancels the incomplete sibling task immediately in both cases, `AuthService.login` catches `except* AuthenticationError` and returns `False` without saving `Credentials` when only `AuthenticationError` occurred, and `AuthService.login` propagates `ExceptionGroup` (even alongside `AuthenticationError`) when any non-`AuthenticationError` exception occurred

#### Scenario: Delegating logout, authentication check, and credential retrieval
- **WHEN** `await authenticate("jdoe@custom.edu", "pw")`, `logout()`, `is_authenticated()`, and `get_credentials()` are called on `AuthService`
- **THEN** `await authenticate(...)` awaits `moodle_auth.fetch_token("jdoe", "pw")`, `logout()` calls `credential_store.clear()`, `get_credentials()` returns `credential_store.load()`, and `is_authenticated()` returns `credential_store.load() is not None`

#### Scenario: Omitting unused login_flow_ended event parameter
- **WHEN** `AuthService.login` and `SharepointAuthPort.fetch_storage_state` signatures are inspected
- **THEN** neither accepts `login_flow_ended`

### Requirement: Moodle token retrieval in MoodleTokenClient and MoodleTokenAdapter
`MoodleTokenClient(base_url: str)` in `src/idat_sync/infrastructure/moodle_client.py` SHALL normalize trailing slashes, own `REQUEST_HEADER`, and expose `async def request_token(username, password) -> dict[str, Any]` without `request_delay`, POSTing via `httpx.AsyncClient` to `{base_url}/login/token.php` with `service="moodle_mobile_app"`, `REQUEST_HEADER`, and `timeout=120`, calling `raise_for_status()`, and returning the raw vendor JSON payload. `MoodleTokenAdapter(client: MoodleTokenClient)` in `src/idat_sync/infrastructure/moodle_adapter.py` SHALL implement `MoodleTokenPort` without `base_url`, expose `async def fetch_token(username, password) -> str`, await `client.request_token(username, password)`, log a warning with `[!]` via `logging.getLogger(__name__)` without calling `print(...)` and raise `AuthenticationError` immediately when `"error"` is present in a `dict` payload (even if `"token"` is also present), and require a `dict` payload with a non-empty `str` `"token"` (logging a warning with `[!]` and raising `AuthenticationError` when non-`dict`, missing, empty, or non-`str`). Verified at the `MoodleTokenClient` (`src/idat_sync/infrastructure/moodle_client.py`) and `MoodleTokenAdapter` (`src/idat_sync/infrastructure/moodle_adapter.py`) seams.

#### Scenario: Fetching a Moodle mobile token with injected base URL
- **WHEN** `await MoodleTokenClient("https://lms.example.edu/").request_token("jdoe", "pw")` receives `{"token": "tok123"}` and is wrapped by `MoodleTokenAdapter`
- **THEN** `MoodleTokenClient` posts `{"username": "jdoe", "password": "pw", "service": "moodle_mobile_app"}` via `httpx.AsyncClient` to `"https://lms.example.edu/login/token.php"` with `headers=REQUEST_HEADER` and `timeout=120`, calls `raise_for_status()`, returns the raw dict, and `await MoodleTokenAdapter.fetch_token("jdoe", "pw")` returns `"tok123"`

#### Scenario: Returning None on Moodle error or missing/invalid payload and propagating HTTP errors
- **WHEN** `await MoodleTokenClient.request_token` returns `{"error": "Invalid login"}`, `{"error": "Invalid login", "token": "ignored"}`, `{}`, `{"token": ""}`, `{"token": 123}`, `None`, `[]`, or `"unexpected"`, or `/login/token.php` returns HTTP 500
- **THEN** `await MoodleTokenAdapter.fetch_token` logs a warning containing `[!]` via `logging.getLogger("idat_sync.infrastructure.moodle_adapter")` and raises `AuthenticationError` (replacing legacy `None` return) for any error or non-`dict`/missing/empty/non-`str` token payload, and `httpx.HTTPStatusError` from `raise_for_status()` propagates on HTTP failure

### Requirement: Browser-based SharePoint SSO and MFA capture
`SharepointBrowserAuth` in `src/idat_sync/infrastructure/sharepoint_auth.py` SHALL accept `(sharepoint_host: str, sharepoint_personal_host: str, email_domain: str)` without defaults. `fetch_storage_state(username, password, get_mfa_code)` SHALL drive `async_playwright` through SharePoint, Microsoft SSO, and MFA, launching `await p.webkit.launch(headless=False)` on `"Darwin"`, `await p.chromium.launch(headless=False, channel="msedge")` on `"Windows"`, and `await p.chromium.launch(headless=False, channel="chromium")` on other platforms, fill `#i0116` with `username` verbatim when `"@"` is present or `f"{username}@{email_domain}"` when `"@"` is absent, raise `AuthenticationError` without submitting `#idTxtBx_SAOTCC_OTC` when `(await get_mfa_code()).strip()` is empty, wrap `async_playwright()` startup, `launch()`, `new_context()`, and page actions inside `try ... except Exception:` logging the exception via `logging.getLogger(__name__).exception(...)` and raising `AuthenticationError` while cleaning up any created `context` and `browser` in nested `try ... finally:` (so `browser.close()` runs even if `context.close()` raises) without swallowing `asyncio.CancelledError`, and return `StorageStateData`. Verified at the `SharepointBrowserAuth` (`src/idat_sync/infrastructure/sharepoint_auth.py`) seam.

#### Scenario: Completing Playwright SharePoint SSO and MFA flow
- **WHEN** `SharepointBrowserAuth(...).fetch_storage_state("jdoe", "pw", get_mfa_code)` runs vs `fetch_storage_state("jdoe@other.edu", "pw", get_mfa_code)`
- **THEN** it visits `https://{sharepoint_host}/_layouts/15/sharepoint.aspx`, fills `"jdoe@{email_domain}"` when `"@"` is absent or `"jdoe@other.edu"` verbatim when `"@"` is present, fills password, submits the stripped code from `await get_mfa_code()`, visits `https://{sharepoint_personal_host}/shared`, and returns `await context.storage_state()`

#### Scenario: Launching WebKit on macOS and Chromium channels on Windows and Linux
- **WHEN** `SharepointBrowserAuth(...).fetch_storage_state("jdoe", "pw", get_mfa_code)` runs with `platform.system()` returning `"Darwin"`, `"Windows"`, or `"Linux"`
- **THEN** it calls `await p.webkit.launch(headless=False)` on `"Darwin"`, `await p.chromium.launch(headless=False, channel="msedge")` on `"Windows"`, and `await p.chromium.launch(headless=False, channel="chromium")` on `"Linux"`, and raises `AuthenticationError` while closing `context` and `browser` if `await get_mfa_code()` returns `""` or whitespace

#### Scenario: Swallowing browser startup and runtime exceptions while preserving cleanup and cancellation
- **WHEN** `async_playwright()`, `launch()`, `new_context()`, `context.close()`, or page interactions raise a runtime `Exception` vs `asyncio.CancelledError`
- **THEN** `fetch_storage_state` closes any created `context` and still closes any created `browser` even if `context.close()` raises, logs the exception with traceback via `logging.getLogger("idat_sync.infrastructure.sharepoint_auth")` at `ERROR` level and raises `AuthenticationError` (replacing legacy `None` return) on `Exception`, and propagates `asyncio.CancelledError`
