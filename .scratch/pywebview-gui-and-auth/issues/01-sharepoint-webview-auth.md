# 01: Driven SharePoint WebView SSO & MFA Adapter

**OpenSpec Change:** `openspec/changes/pywebview-gui-and-auth`

**What to build:** Replace Playwright-based SharePoint authentication with a hidden WebView-driven SSO and MFA flow that works across Desktop (`PyWebviewDriver` using a hidden `pywebview` window) and Android (`AndroidWebViewDriver` using an invisible `1x1` native Android WebView via `pyjnius` with a standard `WebViewClient` and `CookieManager`), executing the scripted Microsoft Entra ID login and 6-digit MFA submission with a Knockout-compatible input helper, capturing and deduplicating `HttpOnly` SharePoint cookies (`FedAuth`, `rtFa`, `SPOIDCRL`) across both the academic and personal SharePoint hosts, and translating timeouts, runtime errors, and task cancellations cleanly.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] `SharepointBrowserAuth` drives the full Microsoft SSO and MFA flow behind `WebViewDriver` (`goto`, `get_url`, `evaluate_js`, `get_cookies`, `aclose`) using the `Galaxy A55` mobile User-Agent (`_GALAXY_A55_UA`), `0.15s` polling interval, `180s` timeout, and `3s` post-load settle sleep at both SharePoint hosts.
- [x] Desktop `PyWebviewDriver` creates a hidden `pywebview` window (`hidden=True` plus immediate `.hide()` for Linux GTK compatibility), converts `window.get_cookies()` `SimpleCookie` morsels into `CookieData` (defaulting empty cookie domains to the current URL hostname), and destroys the window in `aclose()`.
- [x] Android `AndroidWebViewDriver` (auto-selected when `"ANDROID_ARGUMENT"` or `"ANDROID_BOOTLOGO"` is present in `os.environ`) runs on the Android UI thread via `pyjnius`, attaches a `1x1` `View.INVISIBLE` `android.webkit.WebView` with `javaScriptEnabled = True`, `domStorageEnabled = True`, `_GALAXY_A55_UA`, and a standard `android.webkit.WebViewClient` (bypassing `PyWebViewClient.shouldInterceptRequest` POST stripping), extracts `HttpOnly` cookies via `CookieManager.getInstance().getCookie(url)`, and cleans up the WebView in `aclose()`.
- [x] Cookies are captured at both the academic SharePoint site and the personal SharePoint site (`/shared`) and merged deduplicated by `(domain, path, name)` into `StorageStateData`.
- [x] Blank or whitespace MFA input raises `AuthenticationError("MFA code is required")` before filling the OTP field, `TimeoutError` translates to `AuthenticationError("SharePoint sign-in timed out.") from exc`, other runtime `Exception`s translate to `AuthenticationError("SharePoint browser authentication failed") from exc` (logged at `ERROR` with traceback), `asyncio.CancelledError` propagates unchanged, and `await driver.aclose()` always runs in `finally:`.
- [x] Unit tests for `SharepointBrowserAuth`, `PyWebviewDriver`, and `AndroidWebViewDriver` pass without importing or launching Playwright.
