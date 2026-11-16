# authentication Specification Delta

## MODIFIED Requirements

### Requirement: Composition root wiring and UI decoupling for authentication
`create_auth_service(config: SyncConfig) -> AuthService` in `src/idat_sync/bootstrap.py` SHALL wire `MoodleTokenAdapter(MoodleTokenClient(config.moodle_base_url))`, `SharepointBrowserAuth`, and `JsonCredentialStore` using `config` fields. `src/idat_sync/ui/` MUST NOT import `idat_sync.infrastructure`, `src/idat_sync/ui/dialogs.py` MUST NOT exist, `LoadingDialog` MUST NOT be exported in `src/idat_sync/ui/__init__.py`, `AppFrame` in `src/idat_sync/ui/app.py` MUST NOT define `exit` and SHALL define `async def aclose(self) -> None`, `MainApp` SHALL call `self.__frame.destroy()` when switching frames and bind `"WM_DELETE_WINDOW"` to `async_handler(self.on_close)` (guarding against duplicate calls, awaiting `await self.__frame.aclose()` when `self.__frame` is non-`None`, and destroying the window in `finally:`), and `MainApp`, `LoginFrame`, and `SyncFrame` SHALL consume `AuthService` and `Credentials` via `bootstrap` and `application`. `LoginFrame` SHALL keep the Login button disabled whenever trimmed username is blank, password is empty, or authentication is in progress (`is_login`), bind `<Return>` on `pass_entry` with `async_handler(self.do_login)`, track its active login task and cancel/await it in `async def aclose(self) -> None`, guard `validate_buttons` and `finally:` `is_login` resets with `self.winfo_exists()` so destroying `LoginFrame` on `switch_to_sync()` or `on_close()` never raises `TclError`, define `get_mfa_code` directly without `get_mfa_code_ui` (binding `<Return>` on the MFA entry to submit, `WM_DELETE_WINDOW` on the MFA popup to resolve the future with `""`, and destroying `popup` in a `finally:` block even when cancelled), define `error_var = tk.StringVar(value="")` bound to a visible error `tk.Label`, pass stripped username with verbatim password to `AuthService.login`, ignore blank or concurrent submissions, clear `error_var` when starting `do_login`, display an error message in `error_var` both when `AuthService.login` returns `False` and when it raises (unpacking `ExceptionGroup.exceptions` and logging each via `logging.getLogger(__name__).error(...)`), and reset `is_login` in `finally:` when the frame still exists. Verified at the `bootstrap.create_auth_service`, `MainApp`, `LoginFrame`, and AST (`src/idat_sync/ui/`) seams.

#### Scenario: Wiring AuthService from SyncConfig in bootstrap
- **WHEN** `create_auth_service(custom_config)` is called
- **THEN** it returns an `AuthService` wired with `MoodleTokenAdapter(MoodleTokenClient(custom_config.moodle_base_url))`, `SharepointBrowserAuth(custom_config.sharepoint_host, custom_config.sharepoint_personal_host, custom_config.email_domain)`, and `JsonCredentialStore(custom_config.session_file)`

#### Scenario: Disabling Login on blank inputs or active login and guarding submissions
- **WHEN** `LoginFrame` initializes, when trimmed username or raw password is empty vs. non-empty, when `<Return>` is pressed in `pass_entry`, or while `do_login` is running vs. completes/raises/switches frames via `MainApp.switch_to_sync()`
- **THEN** the Login button starts `"disabled"`, switches to `"normal"` only when trimmed username, non-empty password, and `not is_login` all hold, pressing `<Return>` in `pass_entry` schedules `do_login` via `async_handler`, `do_login` ignores blank or concurrent submissions and passes stripped `username` with unstripped `password` to `AuthService.login`, `finally:` resets `is_login` without raising `TclError` even when `MainApp.switch_to_sync()` already destroyed `LoginFrame`, and exceptions unpack `ExceptionGroup.exceptions`

#### Scenario: Displaying LoginFrame errors on False return and exceptions
- **WHEN** `do_login` starts with a pre-existing `error_var` message and `AuthService.login` returns `False` vs raises a single exception or `ExceptionGroup`
- **THEN** `do_login` clears `error_var` on start, sets a user-visible failure message in `error_var` when `AuthService.login` returns `False`, and sets an unpacked error summary in `error_var` (also logging each unpacked exception) when `AuthService.login` raises

#### Scenario: Submitting MFA via Return or dismissing MFA popup cleanly
- **WHEN** `await LoginFrame.get_mfa_code()` opens the MFA popup and the user presses `<Return>` in the entry field, closes the window via `WM_DELETE_WINDOW`, or the awaiting task is cancelled
- **THEN** `hasattr(LoginFrame, "get_mfa_code_ui")` is `False`, pressing `<Return>` resolves `get_mfa_code()` with the entered code and destroys the popup, triggering `WM_DELETE_WINDOW` resolves `get_mfa_code()` with `""` and destroys the popup, and cancelling `get_mfa_code()` destroys the popup in `finally:` before propagating `asyncio.CancelledError`

#### Scenario: Enforcing UI isolation from infrastructure
- **WHEN** all Python modules under `src/idat_sync/ui/` are inspected
- **THEN** none import `idat_sync.infrastructure`, `src/idat_sync/ui/dialogs.py` does not exist, `"LoadingDialog"` is not in `idat_sync.ui.__all__`, and `hasattr(AppFrame, "exit")` is `False`

#### Scenario: Closing active frame on WM_DELETE_WINDOW before destroying MainApp
- **WHEN** `MainApp.on_close()` (registered on `WM_DELETE_WINDOW`) is triggered on a `MainApp` holding an active `LoginFrame` or `SyncFrame` (including while `do_login`, `load_idat_sync`, or `do_sync` is running)
- **THEN** `MainApp.on_close()` awaits `self.__frame.aclose()` once (cancelling active frame tasks, closing open MFA popups and transports, and ignoring duplicate close triggers) before calling `destroy()`, even if `aclose()` raises, without triggering `TclError` on subsequent widget updates

### Requirement: Resilient SyncFrame recovery without silently wiping Credentials
`SyncFrame` in `src/idat_sync/ui/sync_frame.py` SHALL store injected `credentials`, track active startup (`load_idat_sync`) and sync (`do_sync`) tasks, expose `async def aclose(self) -> None` cancelling and awaiting any active task before closing and clearing `self.idat_sync` when present, guard `validate_buttons` and `finally:` widget updates with `self.winfo_exists()`, display `load_idat_sync` and `do_sync` exceptions in `status_var`, close any newly constructed `self.idat_sync` (`await self.aclose()`) on startup failure before clearing `self.idat_sync` to `None`, reset `is_syncing` in `finally:` when the frame exists, retry `await create_sync_service(self.credentials, self.config)` in `do_sync` whenever `self.idat_sync is None`, bind `logout_button` to `async_handler(self.do_logout)` so logging out awaits `await self.aclose()` before switching to login, and MUST NOT silently delete `Credentials` or auto-switch to `LoginFrame` on startup or sync errors. Verified at the `SyncFrame` (`src/idat_sync/ui/sync_frame.py`) seam.

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

#### Scenario: Cancelling active SyncFrame tasks cleanly on aclose
- **WHEN** `await SyncFrame.aclose()` runs while `load_idat_sync` or `do_sync` is in flight and `MainApp.on_close()` subsequently calls `destroy()`
- **THEN** `SyncFrame.aclose()` cancels and awaits the active task and closes `self.idat_sync` before `destroy()` runs, without triggering `TclError` in `validate_buttons()`
