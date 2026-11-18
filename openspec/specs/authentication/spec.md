# authentication Specification

## Purpose

Authenticates users against Moodle and SharePoint/Microsoft SSO, orchestrates concurrent token and browser-cookie acquisition behind hexagonal ports, and persists validated Credentials locally.

## Requirements

### Requirement: Application-owned authentication orchestration and pure-Python ports
`AuthService` in `src/idat_sync/application/auth_service.py` SHALL accept `(moodle_auth: MoodleTokenPort, sharepoint_auth: SharepointAuthPort, credential_store: CredentialStorePort)` without `SyncConfig`, URL/host strings, or `session_file`. `MoodleTokenPort.fetch_token(username, password) -> str`, `AuthService.authenticate(username, password) -> str`, `SharepointAuthPort.fetch_storage_state(username, password, get_mfa_code) -> StorageStateData`, and `AuthService.login(username, password, get_mfa_code) -> None` SHALL be `async def` coroutines, and `CredentialStorePort.save(credentials: Credentials) -> None` SHALL return `None` and raise `CredentialStoreError` on failure. `application/ports.py` SHALL define `CookieData`, `StorageStateData`, `Credentials`, `MoodleTokenPort`, `SharepointAuthPort`, and `CredentialStorePort` and re-export `IDATSyncError`, `AuthenticationError`, `CredentialsExpiredError`, `CredentialStoreError`, `RemoteServiceError`, and `FileSystemError` from `application/exceptions.py` with zero `playwright`, `requests`, or `httpx` imports. `AuthService.login` SHALL execute `authenticate` and `fetch_storage_state` concurrently inside `asyncio.TaskGroup()`, catch `except* IDATSyncError as eg: raise eg.exceptions[0] from eg` (cancelling the incomplete sibling task immediately when either fails), raise `AuthenticationError` if either returns an empty token or storage state, and persist `Credentials` via `await asyncio.to_thread(credential_store.save, credentials)`. Verified at the `AuthService` (`src/idat_sync/application/auth_service.py`) and AST (`src/idat_sync/application/ports.py`, `src/idat_sync/application/exceptions.py`) seams.

#### Scenario: Normalizing username and running login providers concurrently
- **WHEN** `await AuthService.login(username, password, get_mfa_code)` is called with `"jdoe@custom.edu"`
- **THEN** it strips `"@..."` to `"jdoe"` only for `moodle_auth.fetch_token("jdoe", password)`, passes the unsplit `"jdoe@custom.edu"` to `sharepoint_auth.fetch_storage_state("jdoe@custom.edu", password, get_mfa_code)` as named tasks inside `asyncio.TaskGroup()` without wrapping `moodle_auth.fetch_token` in `asyncio.to_thread`, saves `Credentials` via `asyncio.to_thread(credential_store.save, credentials)` only when both return non-empty results, and returns `None`

#### Scenario: Cancelling sibling login provider immediately and unwrapping TaskGroup errors
- **WHEN** either `moodle_auth.fetch_token` or `sharepoint_auth.fetch_storage_state` raises an `IDATSyncError` subclass (such as `AuthenticationError` or `RemoteServiceError`) while the sibling task is still pending, or returns an empty token or empty storage state, or `credential_store.save` raises `CredentialStoreError`
- **THEN** `asyncio.TaskGroup()` cancels the incomplete sibling task immediately, `AuthService.login` unwraps `except* IDATSyncError as eg: raise eg.exceptions[0] from eg` (raising `AuthenticationError` on empty provider output or `CredentialStoreError` on save failure) without saving invalid `Credentials`, and non-`IDATSyncError` failures still cancel the sibling and propagate

#### Scenario: Delegating logout, authentication check, and credential retrieval
- **WHEN** `await authenticate("jdoe@custom.edu", "pw")`, `logout()`, `is_authenticated()`, and `get_credentials()` are called on `AuthService`
- **THEN** `await authenticate(...)` awaits `moodle_auth.fetch_token("jdoe", "pw")`, `logout()` calls `credential_store.clear()`, `get_credentials()` returns `credential_store.load()`, and `is_authenticated()` returns `credential_store.load() is not None`

#### Scenario: Omitting unused login_flow_ended event parameter
- **WHEN** `AuthService.login` and `SharepointAuthPort.fetch_storage_state` signatures are inspected
- **THEN** neither accepts `login_flow_ended`

### Requirement: Moodle token retrieval in MoodleTokenClient and MoodleTokenAdapter
`MoodleTokenClient(base_url: str)` in `src/idat_sync/infrastructure/moodle_client.py` SHALL normalize trailing slashes, own `REQUEST_HEADER`, and expose `async def request_token(username, password) -> dict[str, Any]` without `request_delay`, POSTing via `httpx.AsyncClient` to `{base_url}/login/token.php` with `service="moodle_mobile_app"`, `REQUEST_HEADER`, and `timeout=120`, calling `raise_for_status()`, returning the raw vendor JSON payload, and translating `httpx.HTTPStatusError` (`f"HTTP {exc.response.status_code} from Moodle login endpoint."`), `httpx.RequestError` (`f"Could not connect to Moodle: {exc}"`), and JSON `ValueError` (`"Invalid JSON response from Moodle login endpoint."`) into `RemoteServiceError(...) from exc`. `MoodleTokenAdapter(client: MoodleTokenClient)` in `src/idat_sync/infrastructure/moodle_adapter.py` SHALL implement `MoodleTokenPort` without `base_url`, expose `async def fetch_token(username, password) -> str`, await `client.request_token(username, password)`, log a warning with `[!]` via `logging.getLogger(__name__)` without calling `print(...)` and raise `AuthenticationError` immediately when `"error"` is present in a `dict` payload (even if `"token"` is also present), require a `dict` payload with a non-empty `str` `"token"` (logging a warning with `[!]` and raising `AuthenticationError` when non-`dict`, missing, empty, or non-`str`), and let `RemoteServiceError` from `MoodleTokenClient` propagate unchanged. Verified at the `MoodleTokenClient` (`src/idat_sync/infrastructure/moodle_client.py`) and `MoodleTokenAdapter` (`src/idat_sync/infrastructure/moodle_adapter.py`) seams.

#### Scenario: Fetching a Moodle mobile token with injected base URL
- **WHEN** `await MoodleTokenClient("https://lms.example.edu/").request_token("jdoe", "pw")` receives `{"token": "tok123"}` and is wrapped by `MoodleTokenAdapter`
- **THEN** `MoodleTokenClient` posts `{"username": "jdoe", "password": "pw", "service": "moodle_mobile_app"}` via `httpx.AsyncClient` to `"https://lms.example.edu/login/token.php"` with `headers=REQUEST_HEADER` and `timeout=120`, calls `raise_for_status()`, returns the raw dict, and `await MoodleTokenAdapter.fetch_token("jdoe", "pw")` returns `"tok123"`

#### Scenario: Returning None on Moodle error or missing/invalid payload and propagating HTTP errors
- **WHEN** `await MoodleTokenClient.request_token` returns `{"error": "Invalid login"}`, `{"error": "Invalid login", "token": "ignored"}`, `{}`, `{"token": ""}`, `{"token": 123}`, `None`, `[]`, or `"unexpected"`, or `/login/token.php` fails with `httpx.HTTPStatusError`, `httpx.RequestError`, or invalid JSON (`ValueError`)
- **THEN** `await MoodleTokenAdapter.fetch_token` logs a warning containing `[!]` via `logging.getLogger("idat_sync.infrastructure.moodle_adapter")` and raises `AuthenticationError` for any error or non-`dict`/missing/empty/non-`str` token payload, while `MoodleTokenClient.request_token` raises `RemoteServiceError(...) from exc` (which `MoodleTokenAdapter.fetch_token` propagates unchanged)

### Requirement: Browser-based SharePoint SSO and MFA capture
`SharepointBrowserAuth` in `src/idat_sync/infrastructure/sharepoint_auth.py` SHALL accept `(sharepoint_host: str, sharepoint_personal_host: str, email_domain: str)` without defaults. `fetch_storage_state(username, password, get_mfa_code)` SHALL drive `async_playwright` through SharePoint, Microsoft SSO, and MFA, launching `await p.webkit.launch(headless=False)` on `"Darwin"`, `await p.chromium.launch(headless=False, channel="msedge")` on `"Windows"`, and `await p.chromium.launch(headless=False, channel="chromium")` on other platforms, fill `#i0116` with `username` verbatim when `"@"` is present or `f"{username}@{email_domain}"` when `"@"` is absent, raise `AuthenticationError("MFA code is required")` without filling `#idTxtBx_SAOTCC_OTC` when `(await get_mfa_code()).strip()` is empty, wrap `async_playwright()` startup, `launch()`, `new_context()`, and page actions inside `try ... except` logging via `logging.getLogger(__name__).exception(...)` and translating `playwright.async_api.TimeoutError` into `AuthenticationError("SharePoint sign-in timed out.") from exc` and any other runtime `Exception` into `AuthenticationError("SharePoint browser authentication failed") from exc` while cleaning up any created `context` and `browser` in nested `try ... finally:` (so `browser.close()` runs even if `context.close()` raises) without swallowing `asyncio.CancelledError`, and return `StorageStateData`. Verified at the `SharepointBrowserAuth` (`src/idat_sync/infrastructure/sharepoint_auth.py`) seam.

#### Scenario: Completing Playwright SharePoint SSO and MFA flow
- **WHEN** `SharepointBrowserAuth(...).fetch_storage_state("jdoe", "pw", get_mfa_code)` runs vs `fetch_storage_state("jdoe@other.edu", "pw", get_mfa_code)`
- **THEN** it visits `https://{sharepoint_host}/_layouts/15/sharepoint.aspx`, fills `"jdoe@{email_domain}"` when `"@"` is absent or `"jdoe@other.edu"` verbatim when `"@"` is present, fills password, submits the stripped code from `await get_mfa_code()`, visits `https://{sharepoint_personal_host}/shared`, and returns `await context.storage_state()`

#### Scenario: Launching WebKit on macOS and Chromium channels on Windows and Linux
- **WHEN** `SharepointBrowserAuth(...).fetch_storage_state("jdoe", "pw", get_mfa_code)` runs with `platform.system()` returning `"Darwin"`, `"Windows"`, or `"Linux"`
- **THEN** it calls `await p.webkit.launch(headless=False)` on `"Darwin"`, `await p.chromium.launch(headless=False, channel="msedge")` on `"Windows"`, and `await p.chromium.launch(headless=False, channel="chromium")` on `"Linux"`, and raises `AuthenticationError("MFA code is required")` while closing `context` and `browser` if `await get_mfa_code()` returns `""` or whitespace

#### Scenario: Swallowing browser startup and runtime exceptions while preserving cleanup and cancellation
- **WHEN** `async_playwright()`, `launch()`, `new_context()`, `context.close()`, or page interactions raise `playwright.async_api.TimeoutError`, another runtime `Exception`, or `asyncio.CancelledError`
- **THEN** `fetch_storage_state` closes any created `context` and still closes any created `browser` even if `context.close()` raises, logs the exception with traceback via `logging.getLogger("idat_sync.infrastructure.sharepoint_auth")` at `ERROR` level and raises `AuthenticationError("SharePoint sign-in timed out.") from exc` on `TimeoutError` or `AuthenticationError("SharePoint browser authentication failed") from exc` on other `Exception`s, and propagates `asyncio.CancelledError`

### Requirement: JSON credential persistence and corrupt-file safety
`JsonCredentialStore` in `src/idat_sync/infrastructure/credential_store.py` SHALL accept `credentials_file: str | Path` (storing `self.credentials_file`) and log `"credentials file"` in status/error messages. `save(credentials) -> None` SHALL ensure `Path(credentials_file).parent` exists, atomically write JSON via a sibling `.part` file opened with `0o600` (`os.chmod(..., 0o600)`), replace `self.credentials_file`, and on `OSError` unlink `.part` (`missing_ok=True`), log via `_logger.exception(...)`, and raise `CredentialStoreError(...) from exc`; `load()` SHALL open `credentials_file` directly inside `try:` (without a pre-`try` `.exists()` check) and return `None` when the file is missing, unreadable, invalid JSON, or missing valid non-empty `token` (`str`) and `sharepoint_storage_state` (`dict`); `clear() -> None` SHALL remove the file if present and on `OSError` log via `_logger.exception(...)` and raise `CredentialStoreError(...) from exc`. Verified at the `JsonCredentialStore` (`src/idat_sync/infrastructure/credential_store.py`) seam.

#### Scenario: Saving, loading, and clearing valid Credentials on disk
- **WHEN** `JsonCredentialStore(path)` saves valid `Credentials`, calls `load()`, and calls `clear()`
- **THEN** `save` creates parent directories, calls `os.chmod(part_path, 0o600)`, atomically replaces `path` returning `None`, `load()` returns the matching `Credentials`, and `clear()` deletes the file so subsequent `load()` returns `None`

#### Scenario: Returning None for corrupt, unreadable, or incomplete credential files
- **WHEN** `credentials_file` is unreadable (`OSError`/`PermissionError`), contains invalid JSON, `"{}"`, or an empty/non-string `"token"` or non-dict `"sharepoint_storage_state"`
- **THEN** `JsonCredentialStore.load()` catches `(OSError, json.JSONDecodeError)` and returns `None` without raising

#### Scenario: Raising CredentialStoreError when saving or clearing fails on OSError
- **WHEN** `JsonCredentialStore.save(credentials)` or `JsonCredentialStore.clear()` encounters an `OSError`
- **THEN** `save` unlinks any sibling `.part` file (`missing_ok=True`), both log with traceback via `logging.getLogger("idat_sync.infrastructure.credential_store")` containing `"credentials file"`, and both raise `CredentialStoreError(...) from exc`

### Requirement: Composition root wiring and UI decoupling for authentication
`create_auth_service(config: SyncConfig) -> AuthService` in `src/idat_sync/bootstrap.py` SHALL wire `MoodleTokenAdapter(MoodleTokenClient(config.moodle_base_url))`, `SharepointBrowserAuth`, and `JsonCredentialStore` using `config` fields. `src/idat_sync/ui/` MUST NOT import `idat_sync.infrastructure`, `src/idat_sync/ui/dialogs.py` MUST NOT exist, `LoadingDialog` MUST NOT be exported in `src/idat_sync/ui/__init__.py`, `AppFrame` in `src/idat_sync/ui/base.py` (re-exported from `src/idat_sync/ui/app.py`) MUST NOT define `exit` and SHALL define `async def aclose(self) -> None` and `@contextlib.asynccontextmanager async def _busy(self, flag: tk.BooleanVar)`, `MainApp` SHALL call `self._frame.destroy()` when switching frames and bind `"WM_DELETE_WINDOW"` to `async_handler(self.on_close)` (guarding against duplicate calls, awaiting `await self._frame.aclose()` when `self._frame` is non-`None`, and destroying the window in `finally:`), and `MainApp`, `LoginFrame`, and `SyncFrame` SHALL consume `AuthService`, `Credentials`, and `IDATSyncError` via `bootstrap` and `application`. `LoginFrame` SHALL keep the Login button disabled whenever trimmed username is blank, password is empty, or authentication is in progress (`is_login`), bind `<Return>` on `pass_entry` with `async_handler(self.do_login)`, track its active login task via `_busy(self.is_login)` and cancel/await it in `async def aclose(self) -> None`, guard `validate_buttons` and `finally:` `is_login` resets with `self.winfo_exists()` so destroying `LoginFrame` on `switch_to_sync()` or `on_close()` never raises `TclError`, delegate `get_mfa_code` to `ask_mfa_code(self.master)` in `src/idat_sync/ui/mfa_dialog.py` without `get_mfa_code_ui` (binding `<Return>` on the MFA entry to submit, `WM_DELETE_WINDOW` on the MFA popup to resolve the future with `""`, and destroying `popup` in a `finally:` block even when cancelled), initialize `error_var = tk.StringVar(value="")` bound to a visible error `tk.Label` before calling `self.auth.logout()` wrapped in `try ... except CredentialStoreError as exc:` (recording `str(exc)` in `error_var`), pass stripped username with verbatim password to `AuthService.login`, ignore blank or concurrent submissions, clear `error_var` when starting `do_login`, catch `IDATSyncError as exc` directly (logging `_logger.error("Login error: %s", exc)` and setting `self.error_var.set(str(exc))` without `bool` checks or `ExceptionGroup` unpacking), and reset `is_login` in `finally:` when the frame still exists. Verified at the `bootstrap.create_auth_service`, `MainApp`, `LoginFrame`, `mfa_dialog.ask_mfa_code`, and AST (`src/idat_sync/ui/`) seams.

#### Scenario: Wiring AuthService from SyncConfig in bootstrap
- **WHEN** `create_auth_service(custom_config)` is called
- **THEN** it returns an `AuthService` wired with `MoodleTokenAdapter(MoodleTokenClient(custom_config.moodle_base_url))`, `SharepointBrowserAuth(custom_config.sharepoint_host, custom_config.sharepoint_personal_host, custom_config.email_domain)`, and `JsonCredentialStore(custom_config.session_file)`

#### Scenario: Disabling Login on blank inputs or active login and guarding submissions
- **WHEN** `LoginFrame` initializes (including when `auth.logout()` raises `CredentialStoreError`), when trimmed username or raw password is empty vs. non-empty, when `<Return>` is pressed in `pass_entry`, or while `do_login` is running vs. completes/raises/switches frames via `MainApp.switch_to_sync()`
- **THEN** `LoginFrame.__init__` catches `CredentialStoreError` into `error_var` without crashing startup, the Login button starts `"disabled"` and switches to `"normal"` only when trimmed username, non-empty password, and `not is_login` all hold, pressing `<Return>` in `pass_entry` schedules `do_login` via `async_handler`, `do_login` ignores blank or concurrent submissions and passes stripped `username` with unstripped `password` to `AuthService.login`, `finally:` resets `is_login` without raising `TclError` even when `MainApp.switch_to_sync()` already destroyed `LoginFrame`, and `IDATSyncError` is caught directly without `ExceptionGroup` unpacking

#### Scenario: Displaying LoginFrame errors on False return and exceptions
- **WHEN** `do_login` starts with a pre-existing `error_var` message and `AuthService.login` raises `AuthenticationError`, `RemoteServiceError`, or `CredentialStoreError`
- **THEN** `do_login` clears `error_var` on start, logs `exc` via `logging.getLogger("idat_sync.ui.login_frame").error(...)`, and displays `str(exc)` in `error_var`

#### Scenario: Submitting MFA via Return or dismissing MFA popup cleanly
- **WHEN** `await LoginFrame.get_mfa_code()` (delegating to `ask_mfa_code(self.master)`) opens the MFA popup and the user presses `<Return>` in the entry field, closes the window via `WM_DELETE_WINDOW`, or the awaiting task is cancelled
- **THEN** `hasattr(LoginFrame, "get_mfa_code_ui")` is `False`, pressing `<Return>` resolves `get_mfa_code()` with the entered code and destroys the popup, triggering `WM_DELETE_WINDOW` resolves `get_mfa_code()` with `""` and destroys the popup, and cancelling `get_mfa_code()` destroys the popup in `finally:` before propagating `asyncio.CancelledError`

#### Scenario: Enforcing UI isolation from infrastructure
- **WHEN** all Python modules under `src/idat_sync/ui/` are inspected
- **THEN** none import `idat_sync.infrastructure`, `src/idat_sync/ui/dialogs.py` does not exist, `"LoadingDialog"` is not in `idat_sync.ui.__all__`, and `hasattr(AppFrame, "exit")` is `False`

#### Scenario: Closing active frame on WM_DELETE_WINDOW before destroying MainApp
- **WHEN** `MainApp.on_close()` (registered on `WM_DELETE_WINDOW`) is triggered on a `MainApp` holding an active `LoginFrame` or `SyncFrame` (including while `do_login`, `load_idat_sync`, or `do_sync` is running)
- **THEN** `MainApp.on_close()` awaits `self._frame.aclose()` once (cancelling active frame tasks, closing open MFA popups and transports, and ignoring duplicate close triggers) before calling `destroy()`, even if `aclose()` raises, without triggering `TclError` on subsequent widget updates

### Requirement: Ubiquitous language for Credentials
`GLOSSARY.md` SHALL define **Credentials** (the Moodle token and SharePoint storage state obtained at login) with forbidden synonyms (`Session`, `auth state`, `login data`), and `AuthService` and store classes SHALL use this vocabulary. Verified at the domain vocabulary seam.

#### Scenario: Consulting GLOSSARY.md for authentication terminology
- **WHEN** `GLOSSARY.md` is inspected
- **THEN** `Credentials` is defined with its domain meaning and `_Avoid_: Session, auth state, login data`

### Requirement: Pure-Python CredentialsExpiredError exception in application ports
`src/idat_sync/application/exceptions.py` SHALL define `IDATSyncError(Exception)` inheriting directly from `Exception` (`not issubclass(IDATSyncError, RuntimeError)`) and five subclasses inheriting from `IDATSyncError`: `AuthenticationError`, `CredentialsExpiredError`, `CredentialStoreError`, `RemoteServiceError`, and `FileSystemError`. `src/idat_sync/application/ports.py` SHALL re-export all six exception classes in `__all__`, and both `src/idat_sync/application/exceptions.py` and `src/idat_sync/application/ports.py` SHALL have zero imports from `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `httpx`, `office365`, `yt_dlp`, `playwright`, or `tkinter`. Verified at the `application.exceptions` (`src/idat_sync/application/exceptions.py`), `application.ports` (`src/idat_sync/application/ports.py`), and AST architecture seams.

#### Scenario: Subclassing RuntimeError without vendor imports in ports
- **WHEN** `IDATSyncError`, `AuthenticationError`, `CredentialsExpiredError`, `CredentialStoreError`, `RemoteServiceError`, and `FileSystemError` in `src/idat_sync/application/exceptions.py` and `src/idat_sync/application/ports.py` are inspected
- **THEN** `issubclass(IDATSyncError, Exception)` is `True`, `issubclass(IDATSyncError, RuntimeError)` is `False`, all five subclasses inherit from `IDATSyncError`, all six are exported in `application/exceptions.py` and `application/ports.py` `__all__`, and neither module imports `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `httpx`, `office365`, `yt_dlp`, `playwright`, or `tkinter`

### Requirement: Resilient SyncFrame recovery without silently wiping Credentials
`SyncFrame` in `src/idat_sync/ui/sync_frame.py` SHALL store injected `credentials`, track active startup (`load_idat_sync`) and sync (`do_sync`) tasks via `_busy(self.is_syncing)`, expose `async def aclose(self) -> None` cancelling and awaiting any active task before closing and clearing `self.idat_sync` when present, guard `_update_buttons` and `finally:` widget updates with `self.winfo_exists()`, raise `IDATSyncError("Sync service failed to initialize")` in `_ensure_sync_service` if `self.idat_sync is None` after creation and catch `IDATSyncError` to close any newly constructed `self.idat_sync` (`await self.aclose()`) on startup failure before clearing `self.idat_sync` to `None`, catch `IDATSyncError as exc` in `load_idat_sync` and `do_sync` to display `exc` in `status_var` via `_set_error_status`, reset `is_syncing` in `finally:` when the frame exists, retry `await create_sync_service(self.credentials, self.config)` in `do_sync` whenever `self.idat_sync is None`, bind `logout_button` to `async_handler(self.do_logout)` so logging out awaits `await self.aclose()` before switching to login, and MUST NOT silently delete `Credentials` or auto-switch to `LoginFrame` on startup or sync errors. Verified at the `SyncFrame` (`src/idat_sync/ui/sync_frame.py`) seam.

#### Scenario: Recovering from a transient startup failure during Sync
- **WHEN** `await SyncFrame.load_idat_sync(credentials)` fails because `await create_sync_service(credentials, self.config)` raises an `IDATSyncError` (such as `RemoteServiceError`), leaving `self.idat_sync` as `None`, and the user later selects a folder and invokes `await do_sync()`
- **THEN** `load_idat_sync` writes the error into `status_var`, leaves `Credentials` intact on disk without switching frames, resets `is_syncing` to `False` in `finally:` (re-enabling `Browse...`, `Logout`, and `Sync` once a folder is selected), and `do_sync()` retries `await create_sync_service(self.credentials, self.config)` when `self.idat_sync is None` before running `async for event in self.idat_sync.sync_courses(path, selected, refresh=True):`

#### Scenario: Closing partial IDATSync transports when SyncFrame startup fails after service creation
- **WHEN** `create_sync_service(...)` succeeds in `load_idat_sync` or `_ensure_sync_service` and a subsequent step (`await idat_sync.list_courses()`) raises an `IDATSyncError`
- **THEN** `SyncFrame` awaits `idat_sync.aclose()` before setting `self.idat_sync = None` and keeps `Credentials` intact on disk

#### Scenario: Closing transports cleanly before switching to LoginFrame on Logout
- **WHEN** the user invokes `await SyncFrame.do_logout()` (or clicks `Logout`)
- **THEN** `do_logout()` ignores the action if `self.is_syncing.get()` is `True`, otherwise awaits `self.aclose()` (closing `self.idat_sync` and setting it to `None`) and invokes `self.app.switch_to_login()` in `finally:` even if `aclose()` raises

#### Scenario: Surfacing CredentialsExpiredError in SyncFrame without deleting stored Credentials
- **WHEN** `load_idat_sync` or `do_sync` catches `CredentialsExpiredError` or another `IDATSyncError`
- **THEN** `SyncFrame` stays on screen, writes an error message into `status_var` (instructing the user to click `Logout` when `CredentialsExpiredError` occurs), MUST NOT call `auth.logout()` or delete the credentials file, and resets `is_syncing` in `finally:`

#### Scenario: Cancelling active SyncFrame tasks cleanly on aclose
- **WHEN** `await SyncFrame.aclose()` runs while `load_idat_sync` or `do_sync` is in flight and `MainApp.on_close()` subsequently calls `destroy()`
- **THEN** `SyncFrame.aclose()` cancels and awaits the active task and closes `self.idat_sync` before `destroy()` runs, without triggering `TclError` in `_update_buttons()`
