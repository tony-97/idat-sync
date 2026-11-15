# authentication Specification

## MODIFIED Requirements

### Requirement: Moodle token retrieval in MoodleTokenClient and MoodleTokenAdapter
`MoodleTokenClient(base_url: str)` in `src/idat_sync/infrastructure/moodle_client.py` SHALL normalize trailing slashes, own `REQUEST_HEADER`, and expose `request_token(username, password) -> dict[str, Any]` without `request_delay`, POSTing to `{base_url}/login/token.php` with `service="moodle_mobile_app"`, `REQUEST_HEADER`, and `timeout=120`, calling `raise_for_status()`, and returning the raw vendor JSON payload. `MoodleTokenAdapter(client: MoodleTokenClient)` in `src/idat_sync/infrastructure/moodle_adapter.py` SHALL implement `MoodleTokenPort` without `base_url`, delegate to `client.request_token(username, password)`, log a warning with `[!]` via `logging.getLogger(__name__)` without calling `print(...)` and return `None` immediately when `"error"` is present in a `dict` payload (even if `"token"` is also present), and require a `dict` payload with a non-empty `str` `"token"` (logging a warning with `[!]` and returning `None` when non-`dict`, missing, empty, or non-`str`). Verified at the `MoodleTokenClient` and `MoodleTokenAdapter` seams.

#### Scenario: Fetching a Moodle mobile token with injected base URL
- **WHEN** `MoodleTokenClient("https://lms.example.edu/").request_token("jdoe", "pw")` receives `{"token": "tok123"}` and is wrapped by `MoodleTokenAdapter`
- **THEN** `MoodleTokenClient` posts `{"username": "jdoe", "password": "pw", "service": "moodle_mobile_app"}` to `"https://lms.example.edu/login/token.php"` with `headers=REQUEST_HEADER` and `timeout=120`, calls `raise_for_status()`, returns the raw dict, and `MoodleTokenAdapter.fetch_token("jdoe", "pw")` returns `"tok123"`

#### Scenario: Returning None on Moodle error or missing/invalid payload and propagating HTTP errors
- **WHEN** `MoodleTokenClient.request_token` returns `{"error": "Invalid login"}`, `{"error": "Invalid login", "token": "ignored"}`, `{}`, `{"token": ""}`, `{"token": 123}`, `None`, `[]`, or `"unexpected"`, or `/login/token.php` returns HTTP 500
- **THEN** `MoodleTokenAdapter.fetch_token` logs a warning containing `[!]` via `logging.getLogger("idat_sync.infrastructure.moodle_adapter")` and returns `None` for any error or non-`dict`/missing/empty/non-`str` token payload, and `requests.HTTPError` from `raise_for_status()` propagates on HTTP failure

### Requirement: Resilient SyncFrame recovery without silently wiping Credentials
`SyncFrame` SHALL store injected `credentials`, display `load_idat_sync` and `do_sync` exceptions in `status_var`, reset `is_syncing` in `finally:`, retry `create_sync_service(self.credentials, self.config, publish=...)` in `do_sync` whenever `self.idat_sync is None`, and MUST NOT silently delete `Credentials` or auto-switch to `LoginFrame`. Verified at the `SyncFrame` seam.

#### Scenario: Recovering from a transient startup failure during Sync
- **WHEN** `SyncFrame.load_idat_sync(credentials)` fails because `create_sync_service` raises a transient error, leaving `self.idat_sync` as `None`, and the user later selects a folder and invokes `do_sync()`
- **THEN** `load_idat_sync` writes the error into `status_var`, leaves `Credentials` intact on disk without switching frames, resets `is_syncing` to `False` in `finally:` (re-enabling `Browse...`, `Logout`, and `Sync` once a folder is selected), and `do_sync()` retries `create_sync_service(self.credentials, self.config, publish=...)` when `self.idat_sync is None` before running `sync_courses`

#### Scenario: Surfacing CredentialsExpiredError in SyncFrame without deleting stored Credentials
- **WHEN** `load_idat_sync` or `do_sync` catches `CredentialsExpiredError` or another runtime `Exception`
- **THEN** `SyncFrame` stays on screen, writes an error message into `status_var` (instructing the user to click `Logout` when `CredentialsExpiredError` occurs), MUST NOT call `auth.logout()` or delete the credentials file, and resets `is_syncing` in `finally:`
