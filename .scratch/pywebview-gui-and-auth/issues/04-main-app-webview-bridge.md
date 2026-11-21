# 04: `MainApp` WebView Coordinator & Headless UI Test Suite

**OpenSpec Change:** `openspec/changes/pywebview-gui-and-auth`

**What to build:** Wire `MainApp` and `src/idat_sync/ui/__init__.py` to coordinate `LoginFrame` and `SyncFrame`, load the bundled static Astro HTML (`src/idat_sync/ui/index.html`) via `html=...`, run the background `asyncio` event loop thread alongside `webview.start(user_agent=_GALAXY_A55_UA)`, push live JSON state snapshots to the WebView via `window.__onPythonState(...)`, close active frame tasks and transports idempotently on `on_close()`, and update the UI test suite in `tests/test_auth.py` to run purely in memory without `tkinter`.

**Blocked by:** 01: Driven SharePoint WebView SSO & MFA Adapter, 02: Static Single-File Astro + Tailwind Frontend Bundle, 03: Pure-Python UI Component Controllers (`AppFrame`, `LoginFrame`, `CourseTree`, `SyncFrame`)

**Status:** resolved

- [x] `MainApp(config: SyncConfig, window: Any | None = None)` initializes `self.loop = asyncio.new_event_loop()` and `self.auth = create_auth_service(self.config)`, starts on `SyncFrame` when authenticated or `LoginFrame` otherwise, and switches cleanly via `switch_to_sync()` and `switch_to_login()`.
- [x] `MainApp.get_state()` and `MainApp.push_state()` serialize the active component's state and push it to any attached window via `window.evaluate_js("window.__onPythonState(...)")`.
- [x] `MainApp.on_close()` is idempotent, awaits `await self._frame.aclose()` once (cancelling active login/sync tasks, resolving/cancelling open MFA futures, and closing transports), and destroys any attached window in `finally:` even if `aclose()` raises.
- [x] `MainApp.run()` starts the background `asyncio` event loop thread, loads `src/idat_sync/ui/index.html` via `importlib.resources` into `webview.create_window(..., html=..., js_api=...)`, and runs `webview.start(user_agent=_GALAXY_A55_UA)`.
- [x] All `TestLoginFrame`, `TestSyncFrame`, `MainApp`, and `idat_sync.main.main` tests in `tests/test_auth.py` pass in memory with zero `tkinter` imports.
