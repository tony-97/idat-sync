# authentication Specification Delta

## MODIFIED Requirements

### Requirement: Browser-based SharePoint SSO and MFA capture
`SharepointBrowserAuth` in `src/idat_sync/infrastructure/sharepoint_auth.py` SHALL accept `(sharepoint_host: str, sharepoint_personal_host: str, email_domain: str)` without defaults. `fetch_storage_state(username, password, get_mfa_code)` SHALL drive `async_playwright` through SharePoint, Microsoft SSO, and MFA, launching `await p.webkit.launch(headless=False)` on `"Darwin"`, `await p.chromium.launch(headless=False, channel="msedge")` on `"Windows"`, and `await p.chromium.launch(headless=False, channel="chromium")` on other platforms, fill `#i0116` with `username` verbatim when `"@"` is present or `f"{username}@{email_domain}"` when `"@"` is absent, return `None` immediately without filling `#idTxtBx_SAOTCC_OTC` when `(await get_mfa_code()).strip()` is empty, wrap `async_playwright()` startup, `launch()`, `new_context()`, and page actions inside `try ... except Exception:` returning `None` while cleaning up any created `context` and `browser` in nested `try ... finally:` (so `browser.close()` runs even if `context.close()` raises) without swallowing `asyncio.CancelledError`, and return `StorageStateData | None`. Verified at the `SharepointBrowserAuth` (`src/idat_sync/infrastructure/sharepoint_auth.py`) seam.

#### Scenario: Completing Playwright SharePoint SSO and MFA flow
- **WHEN** `SharepointBrowserAuth(...).fetch_storage_state("jdoe", "pw", get_mfa_code)` runs vs `fetch_storage_state("jdoe@other.edu", "pw", get_mfa_code)`
- **THEN** it visits `https://{sharepoint_host}/_layouts/15/sharepoint.aspx`, fills `"jdoe@{email_domain}"` when `"@"` is absent or `"jdoe@other.edu"` verbatim when `"@"` is present, fills password, submits the stripped code from `await get_mfa_code()`, visits `https://{sharepoint_personal_host}/shared`, and returns `await context.storage_state()`

#### Scenario: Launching WebKit on macOS and Chromium channels on Windows and Linux
- **WHEN** `SharepointBrowserAuth(...).fetch_storage_state("jdoe", "pw", get_mfa_code)` runs with `platform.system()` returning `"Darwin"`, `"Windows"`, or `"Linux"`
- **THEN** it calls `await p.webkit.launch(headless=False)` on `"Darwin"`, `await p.chromium.launch(headless=False, channel="msedge")` on `"Windows"`, and `await p.chromium.launch(headless=False, channel="chromium")` on `"Linux"`, and returns `None` while closing `context` and `browser` if `await get_mfa_code()` returns `""` or whitespace

#### Scenario: Swallowing browser startup and runtime exceptions while preserving cleanup and cancellation
- **WHEN** `async_playwright()`, `launch()`, `new_context()`, `context.close()`, or page interactions raise a runtime `Exception` vs `asyncio.CancelledError`
- **THEN** `fetch_storage_state` closes any created `context` and still closes any created `browser` even if `context.close()` raises, returns `None` on `Exception`, and propagates `asyncio.CancelledError`

### Requirement: JSON credential persistence and corrupt-file safety
`JsonCredentialStore` in `src/idat_sync/infrastructure/credential_store.py` SHALL accept `credentials_file: str | Path` (storing `self.credentials_file`) and log `"credentials file"` in status/error messages. `save(credentials)` SHALL ensure `Path(credentials_file).parent` exists, write JSON, call `os.chmod(self.credentials_file, 0o600)` to restrict permissions to the owner, and return `bool`; `load()` SHALL open `credentials_file` directly inside `try:` (without a pre-`try` `.exists()` check) and return `None` when the file is missing, unreadable, invalid JSON, or missing valid non-empty `token` (`str`) and `sharepoint_storage_state` (`dict`); `clear()` SHALL remove the file if present. Verified at the `JsonCredentialStore` (`src/idat_sync/infrastructure/credential_store.py`) seam.

#### Scenario: Saving, loading, and clearing valid Credentials on disk
- **WHEN** `JsonCredentialStore(path)` saves valid `Credentials`, calls `load()`, and calls `clear()`
- **THEN** `save` creates parent directories, calls `os.chmod(path, 0o600)`, and returns `True`, `load()` returns the matching `Credentials`, and `clear()` deletes the file so subsequent `load()` returns `None`

#### Scenario: Returning None for corrupt, unreadable, or incomplete credential files
- **WHEN** `credentials_file` is unreadable (`OSError`/`PermissionError`), contains invalid JSON, `"{}"`, or an empty/non-string `"token"` or non-dict `"sharepoint_storage_state"`
- **THEN** `JsonCredentialStore.load()` catches `(OSError, json.JSONDecodeError)` and returns `None` without raising

### Requirement: Composition root wiring and UI decoupling for authentication
`create_auth_service(config: SyncConfig) -> AuthService` in `src/idat_sync/bootstrap.py` SHALL wire `MoodleTokenAdapter(MoodleTokenClient(config.moodle_base_url))`, `SharepointBrowserAuth`, and `JsonCredentialStore` using `config` fields. `src/idat_sync/ui/` MUST NOT import `idat_sync.infrastructure`, `src/idat_sync/ui/dialogs.py` MUST NOT exist, `LoadingDialog` MUST NOT be exported in `src/idat_sync/ui/__init__.py`, `AppFrame` in `src/idat_sync/ui/app.py` MUST NOT define `exit`, `MainApp` SHALL call `self.__frame.destroy()` when switching frames, and `MainApp`, `LoginFrame`, and `SyncFrame` SHALL consume `AuthService` and `Credentials` via `bootstrap` and `application`. `LoginFrame` SHALL keep the Login button disabled whenever trimmed username is blank, password is empty, or authentication is in progress (`is_login`), define `get_mfa_code` directly without `get_mfa_code_ui` (binding `<Return>` on the MFA entry to submit and `WM_DELETE_WINDOW` on the MFA popup to resolve the future with `""` and destroy the popup), pass stripped username with verbatim password to `AuthService.login`, ignore blank or concurrent submissions, unpack `ExceptionGroup.exceptions` on failure, and reset `is_login` in `finally:`. Verified at the `bootstrap.create_auth_service`, `MainApp`, `LoginFrame`, and AST (`src/idat_sync/ui/`) seams.

#### Scenario: Wiring AuthService from SyncConfig in bootstrap
- **WHEN** `create_auth_service(custom_config)` is called
- **THEN** it returns an `AuthService` wired with `MoodleTokenAdapter(MoodleTokenClient(custom_config.moodle_base_url))`, `SharepointBrowserAuth(custom_config.sharepoint_host, custom_config.sharepoint_personal_host, custom_config.email_domain)`, and `JsonCredentialStore(custom_config.session_file)`

#### Scenario: Disabling Login on blank inputs or active login and guarding submissions
- **WHEN** `LoginFrame` initializes, when trimmed username or raw password is empty vs. non-empty, or while `do_login` is running vs. completes/raises
- **THEN** the Login button starts `"disabled"`, switches to `"normal"` only when trimmed username, non-empty password, and `not is_login` all hold, `do_login` ignores blank or concurrent submissions and passes stripped `username` with unstripped `password` to `AuthService.login`, and `finally:` resets `is_login` while unpacking `ExceptionGroup.exceptions`

#### Scenario: Submitting MFA via Return or dismissing MFA popup cleanly
- **WHEN** `await LoginFrame.get_mfa_code()` opens the MFA popup and the user either presses `<Return>` in the entry field or closes the window via `WM_DELETE_WINDOW`
- **THEN** `hasattr(LoginFrame, "get_mfa_code_ui")` is `False`, pressing `<Return>` resolves `get_mfa_code()` with the entered code and destroys the popup, and triggering `WM_DELETE_WINDOW` resolves `get_mfa_code()` with `""` and destroys the popup so `do_login` finishes and re-enables the Login button

#### Scenario: Enforcing UI isolation from infrastructure
- **WHEN** all Python modules under `src/idat_sync/ui/` are inspected
- **THEN** none import `idat_sync.infrastructure`, `src/idat_sync/ui/dialogs.py` does not exist, `"LoadingDialog"` is not in `idat_sync.ui.__all__`, and `hasattr(AppFrame, "exit")` is `False`
