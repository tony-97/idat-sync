# Tasks

## 1. Driven SharePoint WebView Authentication Seam

- [x] 1.1 Replace Playwright in `src/idat_sync/infrastructure/sharepoint_auth.py` with `WebViewDriver`, `PyWebviewDriver`, `AndroidWebViewDriver`, and the Knockout-compatible polling loop + two-host `HttpOnly` cookie merger in `SharepointBrowserAuth(host, personal_host, email_domain, driver)` wired by `bootstrap.create_auth_service` using `WEBVIEW_USER_AGENT` and `is_android()` from `src/idat_sync/config.py`, and verify `TestSharepointBrowserAuth` in `tests/test_sharepoint_auth.py` passes for desktop driver selection, Android driver selection, SSO/MFA step sequence, blank-MFA abort, two-host cookie deduplication, timeout/runtime error translation, and `CancelledError` cleanup.

## 2. Static Astro + Tailwind Frontend Bundle Seam

- [x] 2.1 Create the Astro + Tailwind CSS project under `frontend/` with modular components (`LoginForm.astro`, `MfaModal.astro`, `FolderBar.astro`, `CourseToolbar.astro`, `CourseTree.astro`, `ActionFooter.astro`) configured with `vite-plugin-singlefile` to build `src/idat_sync/ui/index.html`, and verify `npm --prefix frontend run build` produces a non-empty self-contained `src/idat_sync/ui/index.html` tested via `importlib.resources` in `tests/test_auth.py`.

## 3. Pure-Python UI Component Controllers Seam

- [x] 3.1 Refactor `AppFrame` (`src/idat_sync/ui/base.py`) and `LoginFrame` (`src/idat_sync/ui/login_frame.py`) into pure-Python component controllers (removing `src/idat_sync/ui/mfa_dialog.py`) with `_busy`, `aclose()`, `can_login`, `get_mfa_code()`/`submit_mfa()`/`cancel_mfa()`, and `do_login()`, and verify `TestLoginFrame` in `tests/test_auth.py` passes without `tkinter`.
- [x] 3.2 Refactor `CourseTree`, `RowStatus`, and `Progress` (`src/idat_sync/ui/course_tree.py`) and `SyncFrame` (`src/idat_sync/ui/sync_frame.py`) into pure-Python component controllers (removing `src/idat_sync/ui/icons.py`) with `to_dict()`, additive `show_files`, `browse_folder()` delegating to `MainApp.pick_folder()`, `set_folder_path()`, and resilient `CredentialsExpiredError`/startup error recovery, and verify `TestSyncFrame` in `tests/test_auth.py` passes without `tkinter`.
- [x] 3.3 Refactor `MainApp` (`src/idat_sync/ui/app.py`) and `src/idat_sync/ui/__init__.py` to coordinate `LoginFrame` and `SyncFrame`, own desktop folder dialog + Android Downloads fallback in `pick_folder()`, push state snapshots via `window.__onPythonState(...)`, run the background `asyncio` event loop thread with `webview.start(user_agent=WEBVIEW_USER_AGENT)` loading `src/idat_sync/ui/index.html` via `html=...`, and await `self._frame.aclose()` before destroying the window in `on_close()`, and verify `MainApp` and `idat_sync.main.main` tests in `tests/test_auth.py` pass.

## 4. Architecture Boundaries, Packaging, CI, and Documentation Seam

- [x] 4.1 Update `pyproject.toml`, `buildozer.spec`, `.github/workflows/build_and_deploy.yml`, `README.md`, `colab.ipynb`, `agent-os/standards/{backend/client-adapter-acl.md,global/layer-boundaries.md,testing/composition-and-seams.md}`, and AST boundary checks in `tests/test_architecture.py` and `tests/test_auth.py` (forbidding `playwright`, `tkinter`, `async_tkinter_loop`, `requests` across `src/idat_sync/`, forbidding `webview` and `jnius` in `domain/` and `application/`, and isolating `webview` to `ui/app.py` and `infrastructure/sharepoint_auth.py`), and verify `pytest tests/test_architecture.py tests/test_auth.py` passes.

## 5. Final Verification

- [x] 5.1 Run the full test suite (`pytest`), Ruff lint and format checks (`ruff check .` and `ruff format --check .`), and Pylint (`pylint src tests`) and verify all pass with zero warnings/errors and a 10.00/10 Pylint score.
