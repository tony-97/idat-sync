# authentication Specification

## MODIFIED Requirements

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
