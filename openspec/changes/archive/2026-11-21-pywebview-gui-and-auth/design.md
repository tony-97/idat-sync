# Design

## Context

See `proposal.md` for motivation and [ADR-0006](../../../docs/adr/0006-pywebview-and-static-astro-ui.md) for the long-lived architectural record. Per [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) and [ADR-0005](../../../docs/adr/0005-platformdirs-gui-root-and-file-credentials.md), `idat-sync` enforces strict inward dependency inversion (`ui/` and `infrastructure/` -> `application/` -> `domain/`) with `bootstrap.py` as the single composition root. Detailed primary-source verification of `pywebview` platform backends (`edgechromium.py`, `cocoa.py`, `gtk.py`, `android/__init__.py`) lives in `.scratch/pywebview-gui-and-auth/research.md`.

## Goals / Non-Goals

**Goals:**
- Provide a unified `pywebview` execution model where Thread 0 runs the native WebView GUI loop and a dedicated background daemon thread runs `asyncio` for `AuthService` and `IDATSync`.
- Isolate platform-specific browser automation behind `WebViewDriver` (`PyWebviewDriver` for Desktop and `AndroidWebViewDriver` for Android) inside `src/idat_sync/infrastructure/sharepoint_auth.py`.
- Keep all UI state transitions in pure-Python component controllers (`MainApp`, `AppFrame`, `LoginFrame`, `SyncFrame`, `CourseTree`) so unit tests run in memory without launching a display server or WebView window, while rendering the view via modular Astro + Tailwind CSS components compiled into a single static `src/idat_sync/ui/index.html`.

**Non-Goals:**
- Modifying `domain/` entities, `application/` use-case orchestration, or `JsonCredentialStore` serialization format.
- Starting any Node.js server or `pywebview` Bottle HTTP server at runtime.

## Decisions

### 1. `WebViewDriver` Seam with Desktop `PyWebviewDriver` and Android `AndroidWebViewDriver`
- **Choice**: Define `WebViewDriver(Protocol)` in `src/idat_sync/infrastructure/sharepoint_auth.py` with `goto(url)`, `get_url()`, `evaluate_js(script)`, `get_cookies()`, and `aclose()`, implemented by `PyWebviewDriver` and `AndroidWebViewDriver`, and inject the platform `driver: WebViewDriver` instance directly into `SharepointBrowserAuth` from `bootstrap.create_auth_service(config)` using `is_android()` and `WEBVIEW_USER_AGENT` from `src/idat_sync/config.py`.
- **Rationale**:
  - On Desktop (Windows WebView2, macOS `WKWebView`, Linux WebKitGTK), `PyWebviewDriver` creates a hidden `pywebview` window (`hidden=True`, immediately calling `.hide()` to work around Linux GTK's dynamic secondary window visibility branch in `webview/platforms/gtk.py`) with `WEBVIEW_USER_AGENT`. If `webview.windows` is already active (GUI mode), it attaches to the running GUI loop; if no GUI loop is active (`scripts/login_cli.py`), it drives a standalone hidden `webview.start(...)` session in a child process.
  - On Android (`is_android()` checks `"ANDROID_ARGUMENT"` or `"ANDROID_BOOTLOGO"` in `os.environ`), `pywebview`'s Android backend is single-window only and its `PyWebViewClient.shouldInterceptRequest` replays `https://` requests via `HttpURLConnection` without HTTP POST bodies. `AndroidWebViewDriver` uses `pyjnius` on the Android UI thread to attach a `1x1` `View.INVISIBLE` `android.webkit.WebView` with a standard `android.webkit.WebViewClient`, `WEBVIEW_USER_AGENT` (stripping `; wv)`), `evaluateJavascript`, and `CookieManager.getInstance().getCookie(url)`.
  - Both drivers initialize lazily in `goto()` and reset their native handle and `_current_url` in `aclose()`, so `bootstrap.create_auth_service` injects the platform `WebViewDriver` instance directly without a factory wrapper, matching all other adapters.
  - Because `window.get_cookies()` on Windows, macOS, Linux, and Android scopes returned cookies to the currently loaded URL, `SharepointBrowserAuth` captures cookies twice—after landing on `https://{sharepoint_host}/_layouts/15/sharepoint.aspx` and after landing on `https://{sharepoint_personal_host}/shared`—and merges them by `(domain, path, name)`.

### 2. Single-File Static Astro + Tailwind Bundle Loaded via `html=...`
- **Choice**: Author the frontend in `frontend/` using modular `.astro` components (`LoginForm.astro`, `MfaModal.astro`, `FolderBar.astro`, `CourseToolbar.astro`, `CourseTree.astro`, `ActionFooter.astro`) and Tailwind CSS, compiled at build time with `vite-plugin-singlefile` into `src/idat_sync/ui/index.html` and loaded via `webview.create_window(..., html=load_ui_html(), js_api=...)`.
- **Rationale**: Passing `html=...` directly to `pywebview` bypasses `pywebview`'s internal Bottle localhost HTTP server (zero open ports, zero Android API 28+ `usesCleartextTraffic` issues) and eliminates multi-file relative path resolution issues inside PyInstaller `--onefile` bundles. Tracking the compiled `src/idat_sync/ui/index.html` in git and package-data ensures `pip install git+https://...` and `pytest` work without requiring Node.js on end-user machines.

### 3. Pure-Python Component Controllers in `src/idat_sync/ui/`
- **Choice**: Keep modular Python component classes (`MainApp` in `ui/app.py`, `AppFrame` in `ui/base.py`, `LoginFrame` in `ui/login_frame.py`, `SyncFrame` in `ui/sync_frame.py`, and `CourseTree`/`RowStatus`/`Progress` in `ui/course_tree.py`) with zero `tkinter` imports, removing `ui/icons.py`, `ui/mfa_dialog.py`, and `ui/dialogs.py`.
- **Rationale**: Preserves the 1-to-1 component boundaries, keeps class attribute counts within Pylint's `max-attributes = 7` threshold, and allows `tests/test_auth.py` to verify all login, MFA, course tree, error recovery, and cancellation behaviors deterministically in memory.

### 4. Confirmed Public Test Seams
- `SharepointBrowserAuth`, `PyWebviewDriver`, `AndroidWebViewDriver` (`src/idat_sync/infrastructure/sharepoint_auth.py`)
- `MainApp` (`src/idat_sync/ui/app.py`), `AppFrame` (`src/idat_sync/ui/base.py`), `LoginFrame` (`src/idat_sync/ui/login_frame.py`), `SyncFrame` (`src/idat_sync/ui/sync_frame.py`), `CourseTree` / `RowStatus` / `Progress` (`src/idat_sync/ui/course_tree.py`)
- `bootstrap.create_auth_service` (`src/idat_sync/bootstrap.py`) and `idat_sync.main.main` (`src/idat_sync/main.py`)
- Bundled static HTML asset (`src/idat_sync/ui/index.html`) and AST architecture seam (`tests/test_architecture.py`)

## Risks / Trade-offs

- **[Risk] Microsoft Entra ID Knockout.js inputs ignore raw `.value = ...` assignment** → Mitigation: The JS `fill` helper invokes the native `HTMLInputElement.prototype.value` setter and dispatches bubbling `input` and `change` events after verifying element visibility (`getBoundingClientRect()`).
- **[Risk] Linux GTK dynamic secondary windows ignore `hidden=True` when `gtk.main_level() != 0`** → Mitigation: `PyWebviewDriver` explicitly calls `window.hide()` immediately after `webview.create_window(..., hidden=True)`.
- **[Risk] Android `View.GONE` or `window.hide()` pauses WebView JS timers** → Mitigation: `AndroidWebViewDriver` uses `1x1` dimensions with `View.INVISIBLE` (`alpha=0`) rather than `View.GONE` and never calls `pauseTimers()`.
