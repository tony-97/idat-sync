## ADDED Requirements

### Requirement: Pure-Python CredentialsExpiredError exception in application ports
`src/idat_sync/application/ports.py` SHALL define and export `CredentialsExpiredError(RuntimeError)` with zero external SDK or UI framework imports so infrastructure clients can signal expired `Credentials` and application/UI layers can distinguish authentication expiry from transient failures. Verified at the `application.ports` and AST architecture seams.

#### Scenario: Subclassing RuntimeError without vendor imports in ports
- **WHEN** `CredentialsExpiredError` in `src/idat_sync/application/ports.py` is inspected and raised
- **THEN** `issubclass(CredentialsExpiredError, RuntimeError)` is `True`, `"CredentialsExpiredError"` is listed in `__all__`, and `application/ports.py` has zero imports from `requests`, `office365`, `yt_dlp`, `playwright`, or `tkinter`

### Requirement: Resilient SyncFrame recovery without silently wiping Credentials
`SyncFrame` SHALL store injected `credentials`, display `load_idat_sync` and `do_sync` exceptions in `Progress:`, reset `is_syncing` in `finally:`, retry `create_sync_service(credentials, config)` in `do_sync` whenever `self.idat_sync is None`, and MUST NOT silently delete `Credentials` or auto-switch to `LoginFrame`. Verified at the `SyncFrame` seam.

#### Scenario: Recovering from a transient startup failure during Sync
- **WHEN** `SyncFrame.load_idat_sync(credentials)` fails because `create_sync_service` raises a transient error, leaving `self.idat_sync` as `None`, and the user later selects a folder and invokes `do_sync()`
- **THEN** `load_idat_sync` logs the error into `Progress:`, leaves `Credentials` intact on disk without switching frames, resets `is_syncing` to `False` in `finally:` (re-enabling `Browse...`, `Logout`, and `Sync` once a folder is selected), and `do_sync()` retries `create_sync_service(credentials, self.config)` when `self.idat_sync is None` before running `sync_courses`

#### Scenario: Surfacing CredentialsExpiredError in SyncFrame without deleting stored Credentials
- **WHEN** `load_idat_sync` or `do_sync` catches `CredentialsExpiredError` or another runtime `Exception`
- **THEN** `SyncFrame` stays on screen, writes an error message into `Progress:` (instructing the user to click `Logout` when `CredentialsExpiredError` occurs), MUST NOT call `auth.logout()` or delete the credentials file, and resets `is_syncing` in `finally:`
