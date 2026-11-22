# `pywebview` Multiplatform Internals, Static Astro + Tailwind GUI, and Driven SharePoint SSO Research

## Part 1: Primary-Source Findings on `pywebview` (Windows, macOS, Linux, Android)

### 1. Platform Backend Architecture (`r0x0r/pywebview` v6.2.1 / `master`)

In `r0x0r/pywebview`, `webview/window.py` defines the platform-agnostic `Window` class and delegates platform operations to the active backend selected by `webview/guilib.py` ([`webview/guilib.py`](https://github.com/r0x0r/pywebview/blob/master/webview/guilib.py), [`docs/guide/web_engine.md`](https://github.com/r0x0r/pywebview/blob/master/docs/guide/web_engine.md)):

| Platform | Backend Module | Native Host & Web Engine | Native `HttpOnly` Cookie API |
| :--- | :--- | :--- | :--- |
| **Windows** | [`webview/platforms/winforms.py`](https://github.com/r0x0r/pywebview/blob/master/webview/platforms/winforms.py) + [`webview/platforms/edgechromium.py`](https://github.com/r0x0r/pywebview/blob/master/webview/platforms/edgechromium.py) | `System.Windows.Forms.Form` hosting `Microsoft.Web.WebView2.WinForms.WebView2` via `pythonnet` | `CoreWebView2.CookieManager.GetCookiesAsync(self.url)` |
| **macOS** | [`webview/platforms/cocoa.py`](https://github.com/r0x0r/pywebview/blob/master/webview/platforms/cocoa.py) | `AppKit.NSWindow` hosting `WebKit.WKWebView` via `PyObjC` | `WKWebsiteDataStore.defaultDataStore().httpCookieStore().getAllCookies_()` |
| **Linux** | [`webview/platforms/gtk.py`](https://github.com/r0x0r/pywebview/blob/master/webview/platforms/gtk.py) | `Gtk.ApplicationWindow` (GTK 3.0) hosting `WebKit2.WebView` (4.1 / 4.0) via `PyGObject` (`gi`) | `WebKit2.CookieManager.get_cookies(uri, ...)` -> `Soup.Cookie.to_set_cookie_header()` |
| **Android** | [`webview/platforms/android/__init__.py`](https://github.com/r0x0r/pywebview/blob/master/webview/platforms/android/__init__.py) | `android.app.Activity` hosting `android.webkit.WebView` via `pyjnius` | `android.webkit.CookieManager.getInstance().getCookie(url)` |

### 2. Hidden / Headless Execution (`hidden=True`) Across Backends

1. **Windows (`winforms.py` L815–819)**:
   - Sets `browser.Opacity = 0`, calls `browser.Show()` (forcing Win32 `HWND` creation and `EnsureCoreWebView2Async`), calls `browser.Hide()`, and restores `browser.Opacity = 1`.
   - `Navigate()`, `NavigationCompleted`, and `ExecuteScriptAsync()` run unblocked while hidden.
2. **macOS (`cocoa.py` L852–862, L881)**:
   - Skips `makeKeyAndOrderFront_` when `hidden=True` and sets `self.shown.set()`; `hide()` invokes `orderOut_`.
   - `WKWebView.loadRequest_()` and `evaluateJavaScript_completionHandler_()` execute normally while ordered out.
3. **Linux (`gtk.py` L226, L546–554)**:
   - Initializes `self.webview.set_opacity(0.0)` and realizes the widget hierarchy before calling `self.window.hide()`.
   - **Dynamic Secondary Window Quirk (`gtk.py` L549–553)**: When `gtk.main_level() != 0` (creating a secondary window after `webview.start()` is already running), `BrowserView.show()` schedules `glib.idle_add(self.window.show_all)` without checking `if self.pywebview_window.hidden:`. Calling `window.hide()` immediately after `create_window(..., hidden=True)` guarantees the secondary auth window stays hidden on Linux GTK.
4. **Android (`webview/platforms/android/__init__.py` L235–236, L450–452, L489–495, L725–729)**:
   - `pywebview` on Android is **single-window only** (`create_window` logs `'Multiple windows are not supported on Android'` if `app` already exists), ignores `hidden=True`, and maps `window.hide()` to `act.moveTaskToBack(True)` which triggers `on_pause()` -> `webview.pauseTimers()` (globally pausing JS execution across all WebViews in the process per [Android `WebView.pauseTimers()` docs](https://developer.android.com/reference/android/webkit/WebView#pauseTimers())).
   - Additionally, [`PyWebViewClient.java` (`shouldInterceptRequest`, L285–540)](https://github.com/r0x0r/pywebview/blob/master/interop/android/lib/src/main/java/com/pywebview/PyWebViewClient.java#L285-L540) intercepts every `https://` request and replays it via `java.net.HttpURLConnection` without forwarding HTTP POST bodies, which breaks Microsoft Entra ID form POST logins.
   - **Android Solution (`AndroidWebViewDriver`)**: Instantiate a secondary `1x1` `View.INVISIBLE` (never `View.GONE`, which suspends layout and throttles Chromium JS timers) `android.webkit.WebView` via `pyjnius` on the Android UI thread with `javaScriptEnabled = True`, `domStorageEnabled = True`, a standard `android.webkit.WebViewClient` (bypassing `PyWebViewClient`), and custom `User-Agent` (stripping `; wv)`).

### 3. `HttpOnly` Cookie Extraction (`FedAuth`, `rtFa`, `SPOIDCRL`)

- `window.get_cookies()` returns `list[http.cookies.SimpleCookie]` including `HttpOnly` cookies on all desktop backends, but each backend scopes its return value to the **current URL** of the WebView:
  - Windows (`edgechromium.py` L157–196): `CoreWebView2.CookieManager.GetCookiesAsync(self.url)`
  - macOS (`cocoa.py` L990–1018): Filters `WKHTTPCookieStore.getAllCookies_()` by `domain in self.url`
  - Linux (`gtk.py` L694–712): `WebKit2.CookieManager.get_cookies(self.webview.get_uri())`
  - Android (`CookieManager.getInstance().getCookie(url)`): Returns `"name1=value1; name2=value2"` (including `HttpOnly` cookies) for the queried `url` without cookie attribute metadata (`domain` is stamped from `urlparse(url).hostname`).
- **Two-Stop Cookie Capture**: Capturing cookies once after landing on `https://{sharepoint_host}/_layouts/15/sharepoint.aspx` and once after landing on `https://{sharepoint_personal_host}/shared` and merging by `(domain, path, name)` populates `StorageStateData` for both `SharepointClient.cookies_for_host()` and `RecordingDownloader.netscape_cookies_format()`.

### 4. Cross-Platform GUI Framework Comparison Summary

- **`pywebview` + Static Astro + Tailwind CSS (Selected)**:
  - Compiles `.astro` components at build time (`vite-plugin-singlefile`) into a single self-contained `src/idat_sync/ui/index.html` loaded via `webview.create_window(..., html=...)` with **zero Node runtime** and **zero localhost HTTP server** (bypassing `pywebview`'s internal Bottle server and Android API 28+ `usesCleartextTraffic` restrictions).
- **Rejected Alternatives**:
  - **Flet (`flet-webview`)**: [`flet_webview/webview.py` (L23–28, L178–190)](https://github.com/flet-dev/flet/blob/main/sdk/python/packages/flet-webview/src/flet_webview/webview.py#L23-L28) raises `FletUnsupportedPlatformException` on Windows and Linux, has no cookie extraction API, and adds ~60–120 MB of Flutter runtime overhead.
  - **BeeWare Toga (`toga.WebView`)**: [`toga/widgets/webview.py` (L245–256)](https://github.com/beeware/toga/blob/main/core/src/toga/widgets/webview.py#L245-L256) leaves `webview.cookies` unimplemented on Linux and Android and fails on GTK4.
  - **Next.js (`output: 'export'`)**: Hardcodes root-relative `/_next/static/...` asset paths and requires an HTTP origin for client routing, plus ~100+ KB of React/Router overhead.

---

## Part 2: Confirmed Design Decisions (Grilling Session)

1. **Scope & Packaging**:
   - Single combined change replacing both Playwright (`infrastructure/sharepoint_auth.py`) and Tkinter (`ui/`) with `pywebview` across Windows, macOS, Linux, and Android.
   - `pyproject.toml`: Replace `playwright >= 1.55.0` and `async-tkinter-loop >= 0.10.2` with `pywebview >= 5.0` (plus `pyobjc-framework-WebKit >= 9.0; sys_platform == 'darwin'`, optional extras `linux = ["pywebview[gtk]"]` and `android = ["pywebview[android]"]`), and include `src/idat_sync/ui/index.html` as package data.
   - `.github/workflows/build_and_deploy.yml` & `idat_sync.spec`: Build `frontend/` via `actions/setup-node@v4` (`npm --prefix frontend ci && npm --prefix frontend run build`), install WebKitGTK packages on `ubuntu-latest`, remove `playwright install` on Linux/macOS, remove the 45-line macOS Playwright `.framework` symlink repair script, and bundle `src/idat_sync/ui/index.html` via `--add-data`.
   - `buildozer.spec`: Root Android Buildozer specification with `requirements = python3,android,pyjnius,bottle,typing_extensions,pywebview,httpx,office365-rest-python-client,platformdirs,yt-dlp`, `source.include_exts = py,png,jpg,html,jar,js,css`, `pywebview-android.jar`, and `INTERNET`/`READ_EXTERNAL_STORAGE`/`WRITE_EXTERNAL_STORAGE` permissions.
   - `README.md` & `colab.ipynb`: Replace `playwright install` instructions with Linux WebKitGTK (`python3-gi`, `gir1.2-webkit2-4.1`, `xvfb` in Colab).
2. **Driven Adapter (`src/idat_sync/infrastructure/sharepoint_auth.py`)**:
   - `WebViewDriver` protocol: `async def goto(url: str) -> None`, `async def get_url() -> str`, `async def evaluate_js(script: str) -> Any`, `async def get_cookies() -> list[CookieData]`, `async def aclose() -> None`.
   - `PyWebviewDriver` (Desktop) & `AndroidWebViewDriver` (Android, auto-selected when `"ANDROID_ARGUMENT"` or `"ANDROID_BOOTLOGO"` is in `os.environ`).
   - `SharepointBrowserAuth(sharepoint_host: str, sharepoint_personal_host: str, email_domain: str, driver_factory: Callable[[], WebViewDriver] | None = None)`:
     - `_GALAXY_A55_UA = "Mozilla/5.0 (Linux; Android 14; SM-A556E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"`
     - `_TIMEOUT_S = 180.0`, `_POLL_INTERVAL_S = 0.15`, and `await asyncio.sleep(3)` after landing on `https://{sharepoint_host}/_layouts/15/sharepoint.aspx` and `https://{sharepoint_personal_host}/shared`.
     - Knockout-compatible JS `fill` (`HTMLInputElement.prototype.value` setter + bubbling `input` and `change` events) and `click` for `#i0116`, `#idSIButton9`, `#i0118`, `#idDiv_SAOTCS_Proofs > div:nth-child(2)`, `#idTxtBx_SAOTCC_OTC` (raising `AuthenticationError("MFA code is required")` on blank MFA without filling `#idTxtBx_SAOTCC_OTC`), `#idChkBx_SAOTCC_TD`, `#idSubmit_SAOTCC_Continue`, optional `#skipMfaRegistrationLink`, and `#KmsiCheckboxField`.
     - Two-stop cookie capture merged by `(domain, path, name)`.
     - Always awaits `driver.aclose()` in `finally:`; translates `TimeoutError` to `AuthenticationError("SharePoint sign-in timed out.") from exc` and other runtime `Exception`s to `AuthenticationError("SharePoint browser authentication failed") from exc`; propagates `asyncio.CancelledError`.
3. **Driving Adapter (`frontend/` & `src/idat_sync/ui/`)**:
   - Astro + Tailwind components under `frontend/src/components/`: `LoginForm.astro`, `MfaModal.astro`, `FolderBar.astro`, `CourseToolbar.astro`, `CourseTree.astro`, `ActionFooter.astro`, compiled into `src/idat_sync/ui/index.html`.
   - Pure-Python UI component controllers under `src/idat_sync/ui/` (zero `tkinter` or `async_tkinter_loop` imports; `ui/icons.py`, `ui/mfa_dialog.py`, and `ui/dialogs.py` removed):
     - `AppFrame` (`src/idat_sync/ui/base.py`)
     - `LoginFrame` (`src/idat_sync/ui/login_frame.py`)
     - `SyncFrame` (`src/idat_sync/ui/sync_frame.py`)
     - `CourseTree`, `RowStatus`, `Progress` (`src/idat_sync/ui/course_tree.py`)
     - `MainApp` (`src/idat_sync/ui/app.py`)
4. **AST Boundaries & Confirmed Public Test Seams**:
   - `src/idat_sync/domain/` and `src/idat_sync/application/` (including `ports.py` and `exceptions.py`) have zero imports from `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `httpx`, `office365`, `yt_dlp`, `playwright`, `tkinter`, `webview`, or `jnius`.
   - No module under `src/idat_sync/` imports `playwright`, `tkinter`, `async_tkinter_loop`, or `requests`, or calls `print(...)`.
   - `src/idat_sync/ui/` has zero imports from `idat_sync.infrastructure`.
   - Public test seams: `SharepointBrowserAuth`, `PyWebviewDriver`, `AndroidWebViewDriver` (`src/idat_sync/infrastructure/sharepoint_auth.py`), `MainApp` (`src/idat_sync/ui/app.py`), `AppFrame` (`src/idat_sync/ui/base.py`), `LoginFrame` (`src/idat_sync/ui/login_frame.py`), `SyncFrame` (`src/idat_sync/ui/sync_frame.py`), `CourseTree`/`RowStatus`/`Progress` (`src/idat_sync/ui/course_tree.py`), `bootstrap.create_auth_service` (`src/idat_sync/bootstrap.py`), `idat_sync.main.main` (`src/idat_sync/main.py`), `src/idat_sync/ui/index.html`, and AST architecture boundary (`tests/test_architecture.py`).
