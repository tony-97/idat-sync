# authentication Specification

## Purpose

Authenticates users against Moodle and SharePoint/Microsoft SSO, orchestrates concurrent token and browser-cookie acquisition behind hexagonal ports, and persists validated Credentials locally.

## Requirements

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

### Requirement: Browser-based SharePoint SSO and MFA capture
`SharepointBrowserAuth` in `src/idat_sync/infrastructure/sharepoint_auth.py` SHALL accept `(sharepoint_host: str, sharepoint_personal_host: str, email_domain: str)` without defaults. `fetch_storage_state(username, password, get_mfa_code)` SHALL drive `async_playwright` through SharePoint, Microsoft SSO, and MFA, launching `await p.webkit.launch(headless=False)` on `"Darwin"`, `await p.chromium.launch(headless=False, channel="msedge")` on `"Windows"`, and `await p.chromium.launch(headless=False, channel="chromium")` on other platforms, fill `#i0116` with `username` verbatim when `"@"` is present or `f"{username}@{email_domain}"` when `"@"` is absent, return `None` immediately without filling `#idTxtBx_SAOTCC_OTC` when `(await get_mfa_code()).strip()` is empty, wrap `async_playwright()` startup, `launch()`, `new_context()`, and page actions inside `try ... except Exception:` logging the exception via `logging.getLogger(__name__).exception(...)` and returning `None` while cleaning up any created `context` and `browser` in nested `try ... finally:` (so `browser.close()` runs even if `context.close()` raises) without swallowing `asyncio.CancelledError`, and return `StorageStateData | None`. Verified at the `SharepointBrowserAuth` (`src/idat_sync/infrastructure/sharepoint_auth.py`) seam.

#### Scenario: Completing Playwright SharePoint SSO and MFA flow
- **WHEN** `SharepointBrowserAuth(...).fetch_storage_state("jdoe", "pw", get_mfa_code)` runs vs `fetch_storage_state("jdoe@other.edu", "pw", get_mfa_code)`
- **THEN** it visits `https://{sharepoint_host}/_layouts/15/sharepoint.aspx`, fills `"jdoe@{email_domain}"` when `"@"` is absent or `"jdoe@other.edu"` verbatim when `"@"` is present, fills password, submits the stripped code from `await get_mfa_code()`, visits `https://{sharepoint_personal_host}/shared`, and returns `await context.storage_state()`

#### Scenario: Launching WebKit on macOS and Chromium channels on Windows and Linux
- **WHEN** `SharepointBrowserAuth(...).fetch_storage_state("jdoe", "pw", get_mfa_code)` runs with `platform.system()` returning `"Darwin"`, `"Windows"`, or `"Linux"`
- **THEN** it calls `await p.webkit.launch(headless=False)` on `"Darwin"`, `await p.chromium.launch(headless=False, channel="msedge")` on `"Windows"`, and `await p.chromium.launch(headless=False, channel="chromium")` on `"Linux"`, and returns `None` while closing `context` and `browser` if `await get_mfa_code()` returns `""` or whitespace

#### Scenario: Swallowing browser startup and runtime exceptions while preserving cleanup and cancellation
- **WHEN** `async_playwright()`, `launch()`, `new_context()`, `context.close()`, or page interactions raise a runtime `Exception` vs `asyncio.CancelledError`
- **THEN** `fetch_storage_state` closes any created `context` and still closes any created `browser` even if `context.close()` raises, logs the exception with traceback via `logging.getLogger("idat_sync.infrastructure.sharepoint_auth")` at `ERROR` level and returns `None` on `Exception`, and propagates `asyncio.CancelledError`

### Requirement: JSON credential persistence and corrupt-file safety
`JsonCredentialStore` in `src/idat_sync/infrastructure/credential_store.py` SHALL accept `credentials_file: str | Path` (storing `self.credentials_file`) and log `"credentials file"` in status/error messages. `save(credentials)` SHALL ensure `Path(credentials_file).parent` exists, write JSON, call `os.chmod(self.credentials_file, 0o600)` to restrict permissions to the owner, and return `bool`; `load()` SHALL open `credentials_file` directly inside `try:` (without a pre-`try` `.exists()` check) and return `None` when the file is missing, unreadable, invalid JSON, or missing valid non-empty `token` (`str`) and `sharepoint_storage_state` (`dict`); `clear()` SHALL remove the file if present. Verified at the `JsonCredentialStore` (`src/idat_sync/infrastructure/credential_store.py`) seam.

#### Scenario: Saving, loading, and clearing valid Credentials on disk
- **WHEN** `JsonCredentialStore(path)` saves valid `Credentials`, calls `load()`, and calls `clear()`
- **THEN** `save` creates parent directories, calls `os.chmod(path, 0o600)`, and returns `True`, `load()` returns the matching `Credentials`, and `clear()` deletes the file so subsequent `load()` returns `None`

#### Scenario: Returning None for corrupt, unreadable, or incomplete credential files
- **WHEN** `credentials_file` is unreadable (`OSError`/`PermissionError`), contains invalid JSON, `"{}"`, or an empty/non-string `"token"` or non-dict `"sharepoint_storage_state"`
- **THEN** `JsonCredentialStore.load()` catches `(OSError, json.JSONDecodeError)` and returns `None` without raising

### Requirement: Composition root wiring and UI decoupling for authentication
`create_auth_service(config: SyncConfig) -> AuthService` in `src/idat_sync/bootstrap.py` SHALL wire `MoodleTokenAdapter(MoodleTokenClient(config.moodle_base_url))`, `SharepointBrowserAuth`, and `JsonCredentialStore` using `config` fields. `src/idat_sync/ui/` MUST NOT import `idat_sync.infrastructure`, `src/idat_sync/ui/dialogs.py` MUST NOT exist, `LoadingDialog` MUST NOT be exported in `src/idat_sync/ui/__init__.py`, `AppFrame` in `src/idat_sync/ui/app.py` MUST NOT define `exit` and SHALL define `async def aclose(self) -> None`, `MainApp` SHALL call `self.__frame.destroy()` when switching frames and bind `"WM_DELETE_WINDOW"` to `async_handler(self.on_close)` (guarding against duplicate calls, awaiting `await self.__frame.aclose()` when `self.__frame` is non-`None`, and destroying the window in `finally:`), and `MainApp`, `LoginFrame`, and `SyncFrame` SHALL consume `AuthService` and `Credentials` via `bootstrap` and `application`. `LoginFrame` SHALL keep the Login button disabled whenever trimmed username is blank, password is empty, or authentication is in progress (`is_login`), define `get_mfa_code` directly without `get_mfa_code_ui` (binding `<Return>` on the MFA entry to submit and `WM_DELETE_WINDOW` on the MFA popup to resolve the future with `""` and destroy the popup), define `error_var = tk.StringVar(value="")` bound to a visible error `tk.Label`, pass stripped username with verbatim password to `AuthService.login`, ignore blank or concurrent submissions, clear `error_var` when starting `do_login`, display an error message in `error_var` both when `AuthService.login` returns `False` and when it raises (unpacking `ExceptionGroup.exceptions` and logging each via `logging.getLogger(__name__).error(...)`), and reset `is_login` in `finally:`. Verified at the `bootstrap.create_auth_service`, `MainApp`, `LoginFrame`, and AST (`src/idat_sync/ui/`) seams.

#### Scenario: Wiring AuthService from SyncConfig in bootstrap
- **WHEN** `create_auth_service(custom_config)` is called
- **THEN** it returns an `AuthService` wired with `MoodleTokenAdapter(MoodleTokenClient(custom_config.moodle_base_url))`, `SharepointBrowserAuth(custom_config.sharepoint_host, custom_config.sharepoint_personal_host, custom_config.email_domain)`, and `JsonCredentialStore(custom_config.session_file)`

#### Scenario: Disabling Login on blank inputs or active login and guarding submissions
- **WHEN** `LoginFrame` initializes, when trimmed username or raw password is empty vs. non-empty, or while `do_login` is running vs. completes/raises
- **THEN** the Login button starts `"disabled"`, switches to `"normal"` only when trimmed username, non-empty password, and `not is_login` all hold, `do_login` ignores blank or concurrent submissions and passes stripped `username` with unstripped `password` to `AuthService.login`, and `finally:` resets `is_login` while unpacking `ExceptionGroup.exceptions`

#### Scenario: Displaying LoginFrame errors on False return and exceptions
- **WHEN** `do_login` starts with a pre-existing `error_var` message and `AuthService.login` returns `False` vs raises a single exception or `ExceptionGroup`
- **THEN** `do_login` clears `error_var` on start, sets a user-visible failure message in `error_var` when `AuthService.login` returns `False`, and sets an unpacked error summary in `error_var` (also logging each unpacked exception) when `AuthService.login` raises

#### Scenario: Submitting MFA via Return or dismissing MFA popup cleanly
- **WHEN** `await LoginFrame.get_mfa_code()` opens the MFA popup and the user either presses `<Return>` in the entry field or closes the window via `WM_DELETE_WINDOW`
- **THEN** `hasattr(LoginFrame, "get_mfa_code_ui")` is `False`, pressing `<Return>` resolves `get_mfa_code()` with the entered code and destroys the popup, and triggering `WM_DELETE_WINDOW` resolves `get_mfa_code()` with `""` and destroys the popup so `do_login` finishes and re-enables the Login button

#### Scenario: Enforcing UI isolation from infrastructure
- **WHEN** all Python modules under `src/idat_sync/ui/` are inspected
- **THEN** none import `idat_sync.infrastructure`, `src/idat_sync/ui/dialogs.py` does not exist, `"LoadingDialog"` is not in `idat_sync.ui.__all__`, and `hasattr(AppFrame, "exit")` is `False`

#### Scenario: Closing active frame on WM_DELETE_WINDOW before destroying MainApp
- **WHEN** `MainApp.on_close()` (registered on `WM_DELETE_WINDOW`) is triggered on a `MainApp` holding an active `AppFrame`
- **THEN** `MainApp.on_close()` awaits `self.__frame.aclose()` once (ignoring duplicate close triggers) before calling `destroy()`, even if `aclose()` raises

### Requirement: Ubiquitous language for Credentials
`GLOSSARY.md` SHALL define **Credentials** (the Moodle token and SharePoint storage state obtained at login) with forbidden synonyms (`Session`, `auth state`, `login data`), and `AuthService` and store classes SHALL use this vocabulary. Verified at the domain vocabulary seam.

#### Scenario: Consulting GLOSSARY.md for authentication terminology
- **WHEN** `GLOSSARY.md` is inspected
- **THEN** `Credentials` is defined with its domain meaning and `_Avoid_: Session, auth state, login data`

### Requirement: Pure-Python CredentialsExpiredError exception in application ports
`src/idat_sync/application/ports.py` SHALL define and export `CredentialsExpiredError(RuntimeError)` with zero external SDK or UI framework imports so infrastructure clients can signal expired `Credentials` and application/UI layers can distinguish authentication expiry from transient failures. Verified at the `application.ports` (`src/idat_sync/application/ports.py`) and AST architecture seams.

#### Scenario: Subclassing RuntimeError without vendor imports in ports
- **WHEN** `CredentialsExpiredError` in `src/idat_sync/application/ports.py` is inspected and raised
- **THEN** `issubclass(CredentialsExpiredError, RuntimeError)` is `True`, `"CredentialsExpiredError"` is listed in `__all__`, and `application/ports.py` has zero imports from `requests`, `httpx`, `office365`, `yt_dlp`, `playwright`, or `tkinter`

### Requirement: Resilient SyncFrame recovery without silently wiping Credentials
`SyncFrame` in `src/idat_sync/ui/sync_frame.py` SHALL store injected `credentials`, expose `async def aclose(self) -> None` closing and clearing `self.idat_sync` when present, display `load_idat_sync` and `do_sync` exceptions in `status_var`, close any newly constructed `self.idat_sync` (`await self.aclose()`) on startup failure before clearing `self.idat_sync` to `None`, reset `is_syncing` in `finally:`, retry `await create_sync_service(self.credentials, self.config)` in `do_sync` whenever `self.idat_sync is None`, bind `logout_button` to `async_handler(self.do_logout)` so logging out awaits `await self.aclose()` before switching to login, and MUST NOT silently delete `Credentials` or auto-switch to `LoginFrame` on startup or sync errors. Verified at the `SyncFrame` (`src/idat_sync/ui/sync_frame.py`) seam.

#### Scenario: Recovering from a transient startup failure during Sync
- **WHEN** `await SyncFrame.load_idat_sync(credentials)` fails because `await create_sync_service(credentials, self.config)` raises a transient error, leaving `self.idat_sync` as `None`, and the user later selects a folder and invokes `await do_sync()`
- **THEN** `load_idat_sync` writes the error into `status_var`, leaves `Credentials` intact on disk without switching frames, resets `is_syncing` to `False` in `finally:` (re-enabling `Browse...`, `Logout`, and `Sync` once a folder is selected), and `do_sync()` retries `await create_sync_service(self.credentials, self.config)` when `self.idat_sync is None` before running `async for event in self.idat_sync.sync_courses(path, selected):`

#### Scenario: Closing partial IDATSync transports when SyncFrame startup fails after service creation
- **WHEN** `create_sync_service(...)` succeeds in `load_idat_sync` or `_ensure_sync_service` and a subsequent step (`await idat_sync.list_courses()`) raises
- **THEN** `SyncFrame` awaits `idat_sync.aclose()` before setting `self.idat_sync = None` and keeps `Credentials` intact on disk

#### Scenario: Closing transports cleanly before switching to LoginFrame on Logout
- **WHEN** the user invokes `await SyncFrame.do_logout()` (or clicks `Logout`)
- **THEN** `do_logout()` ignores the action if `self.is_syncing.get()` is `True`, otherwise awaits `self.aclose()` (closing `self.idat_sync` and setting it to `None`) and invokes the login switch callback in `finally:` even if `aclose()` raises

#### Scenario: Surfacing CredentialsExpiredError in SyncFrame without deleting stored Credentials
- **WHEN** `load_idat_sync` or `do_sync` catches `CredentialsExpiredError` or another runtime `Exception`
- **THEN** `SyncFrame` stays on screen, writes an error message into `status_var` (instructing the user to click `Logout` when `CredentialsExpiredError` occurs), MUST NOT call `auth.logout()` or delete the credentials file, and resets `is_syncing` in `finally:`
