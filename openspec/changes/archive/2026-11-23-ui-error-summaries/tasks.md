# Tasks

## 1. Structured Exception Metadata & UI Error Formatter

- [x] 1.1 Write failing unit tests in `tests/test_auth.py` at the `idat_sync.application.exceptions` seam verifying default and explicit keyword-only attributes (`AuthenticationError.reason`, `CredentialsExpiredError.service`, `FileSystemError.operation`, `RemoteServiceError.service` / `.status_code` / `.is_network`) and re-exports in `idat_sync.application.ports`, then implement them in `src/idat_sync/application/exceptions.py` and `src/idat_sync/application/ports.py` and verify tests pass.
- [x] 1.2 Write/update failing seam tests in `tests/test_moodle_client.py`, `tests/test_sharepoint_adapter.py`, `tests/test_sharepoint_auth.py`, and `tests/test_sync_pipeline.py` asserting structured metadata on raised exceptions, then populate structured facts at `raise` sites in `src/idat_sync/infrastructure/moodle_client.py`, `src/idat_sync/infrastructure/sharepoint_client.py`, `src/idat_sync/infrastructure/sharepoint_auth.py`, and `src/idat_sync/infrastructure/downloaders.py` and verify those test suites pass.
- [x] 1.3 Update `TestErrorFormat` in `tests/test_auth.py` to test attribute-based `format_ui_error(exc)` dispatch across all subclasses (including regression cases where raw `str(exc)` contains `"write_notes"` or `"500_lab.pdf"` without matching), rewrite `src/idat_sync/ui/error_format.py` to dispatch strictly on exception type and structured attributes (zero regex or substring checks), and verify `pytest tests/test_auth.py -k error_format` passes.

## 2. LoginFrame Summarized Errors

- [x] 2.1 Update and expand `LoginFrame` seam tests in `tests/test_auth.py` (including existing `CredentialStoreError` startup and `RemoteServiceError` login assertions) to verify `frame.error` stores `format_ui_error(exc)` while raw `exc` remains logged, then update `src/idat_sync/ui/login_frame.py` to use `format_ui_error(exc)` and verify `pytest tests/test_auth.py -k "login_frame or LoginFrame"` passes.

## 3. SyncFrame Summarized Errors and Startup Error Logging

- [x] 3.1 Update `SyncFrame` seam tests in `tests/test_auth.py` to construct exceptions with structured metadata (e.g. `operation="write"`, `service="Moodle"`, `status_code=500`, `is_network=True`) and use `MagicMock(spec=IDATSync)`, extract `SyncFrame._with_log_hint()` in `src/idat_sync/ui/sync_frame.py` for all 3 log-hint banner sites, and verify `pytest tests/test_auth.py` passes.

## 4. Full Verification

- [x] 4.1 Run the full test suite (`pytest -k "not TestLiveCredentialsIntegration"`), architecture boundary tests (`tests/test_architecture.py`), `ruff check`, `ruff format --check`, and `pylint` on all modified and new files and verify all checks pass with a 10.00/10 pylint score.

## Workflow follow-up

- Commit the implementation before running `/code-review`.
- Archive the change with `/opsx:archive` after review and full test verification pass.
