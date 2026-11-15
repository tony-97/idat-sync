# Tasks

## 1. Authentication, Credentials & UI Fixes

- [x] 1.1 Add failing tests in `tests/test_auth.py` at the `SharepointBrowserAuth` seam for Darwin (`p.webkit.launch(headless=False)`), Windows (`channel="msedge"`), Linux (`channel="chromium"`), and returning `None` when `get_mfa_code()` returns blank/whitespace; update `src/idat_sync/infrastructure/sharepoint_auth.py` and verify `python -m unittest tests/test_auth.py -v` passes.
- [x] 1.2 Add failing test in `tests/test_auth.py` at the `JsonCredentialStore` seam asserting `os.chmod(..., 0o600)` is invoked on `save(credentials)`; update `src/idat_sync/infrastructure/credential_store.py` and verify `python -m unittest tests/test_auth.py -v` passes.
- [x] 1.3 Add failing tests in `tests/test_auth.py` at the `LoginFrame`, `MainApp`, and UI package seams for `<Return>` submit and `WM_DELETE_WINDOW` dismissal resolving `get_mfa_code()` to `""`, removing `get_mfa_code_ui`, deleting `src/idat_sync/ui/dialogs.py` and `LoadingDialog` export in `src/idat_sync/ui/__init__.py`, removing `AppFrame.exit`, and calling `self.__frame.destroy()` in `MainApp.__change`; implement in `src/idat_sync/ui/login_frame.py`, `src/idat_sync/ui/app.py`, and `src/idat_sync/ui/__init__.py` and verify `python -m unittest tests/test_auth.py -v` passes.

## 2. Atomic Downloads & SharePoint Adapter/Client Fixes

- [x] 2.1 Add failing tests in `tests/test_sync_pipeline.py` at the `MoodleDownloader`, `SharepointDownloader`, and `LocalFileSystemAdapter` seams for streaming into `<filename>.part`, atomic `os.replace` on success, `.part` cleanup without leaving `content.path` on failure, and excluding `*.part` files in `scan_mtimes`; implement in `src/idat_sync/infrastructure/downloaders.py` and `src/idat_sync/infrastructure/filesystem.py` and verify `python -m unittest tests/test_sync_pipeline.py -v` passes.
- [x] 2.2 Add failing tests in `tests/test_sharepoint_adapter.py` at the `SharepointApiAdapter` seam for boundary-aware course recording matching (rejecting `"Matemática II"` when fetching `"Matemática I"`, matching URL-encoded `"Matem%C3%A1tica%20I"`) and absence of `_SharepointApiAdapter__get_name` / `_SharepointApiAdapter__get_last_modified`; implement in `src/idat_sync/infrastructure/sharepoint_adapter.py` and verify `python -m unittest tests/test_sharepoint_adapter.py -v` passes.
- [x] 2.3 Add failing tests in `tests/test_sharepoint_adapter.py` at the `SharepointClient` seam for removing `onedrive_cookies`, reusing a single `HttpxTransport` instance across `get_client` calls, and calling `aclose()` (including on failed `SharepointClient.create`); implement in `src/idat_sync/infrastructure/sharepoint_client.py` and verify `python -m unittest tests/test_sharepoint_adapter.py -v` passes.

## 3. Final Verification

- [x] 3.1 Run the full test suite (`python -m unittest discover -s tests -v`), AST boundary tests (`python -m unittest tests/test_architecture.py -v`), and any available typecheck and lint checks, and verify all pass.
