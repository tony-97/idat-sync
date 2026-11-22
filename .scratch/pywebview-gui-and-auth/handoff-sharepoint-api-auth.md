# Handoff: SharePoint API Auth / Cookie Capture Fix

- **Branch:** `feat/pywebview-gui-and-auth`
- **Date:** 2026-11-21
- **Status:** Fix implemented & unit-tested; live login test deferred until tomorrow (Entra SMS MFA rate-limited).

## What Was Checked
- `src/idat_sync/infrastructure/sharepoint_client.py` and `src/idat_sync/infrastructure/sharepoint_adapter.py`: **zero diff vs `main`**.
- `src/idat_sync/config.py` URLs and `src/idat_sync/bootstrap.py` SharePoint wiring: **unchanged vs `main`**.
- Inspected `pywebview` Windows `EdgeChrome` (`get_cookies` / `load_url`):
  - `load_url()` is non-blocking.
  - `get_cookies()` queries `CoreWebView2.CookieManager.GetCookiesAsync(self.url)` (only cookies for whatever URL the WebView happens to be on).

## Root Cause
1. In `src/idat_sync/infrastructure/sharepoint_auth.py`, after `driver.goto(f"https://{self.sharepoint_personal_host}/shared")`, the code only slept 3s before calling `get_cookies()`. It did not wait for the silent Entra SSO redirect (`idat628-my.sharepoint.com` -> `login.microsoftonline.com` -> `idat628-my.sharepoint.com`) to finish, so `personal_cookies` could miss host-specific `FedAuth`/`SPOIDCRL` cookies.
2. `.venv\Lib\site-packages\idat_sync` contains a **stale non-editable install** of `idat_sync` (missing recent branch changes).

## Uncommitted Changes Ready to Test
- `src/idat_sync/infrastructure/sharepoint_auth.py`:
  - Added `_HOST_AUTH_COOKIES = frozenset({"FedAuth", "SPOIDCRL"})`, `_has_host_auth_cookie()`, and `_wait_for_host_cookies()`.
  - Both academic (`sharepoint_host`) and personal (`sharepoint_personal_host`) cookie captures now poll until `urlparse(get_url()).hostname == target` **and** a host-specific `FedAuth` or `SPOIDCRL` cookie (matching `domain.lstrip(".").lower() == target`, ignoring wildcard `.sharepoint.com` `rtFa`) is present, then sleep 3s before snapshotting cookies.
- `tests/test_sharepoint_auth.py`:
  - Added `test_waits_for_personal_host_sso_redirect_and_host_auth_cookie`.
- Verified locally with `PYTHONPATH=src`:
  - `pytest tests/test_sharepoint_auth.py tests/test_auth.py tests/test_architecture.py` -> 55 passed.
  - `ruff check` and `ruff format --check` -> clean.

## Next Steps (Tomorrow)
1. Reinstall editable so `.venv` uses `src/` instead of stale `site-packages`:
   ```powershell
   .venv\Scripts\pip.exe install -e .
   ```
2. Run one live SMS login and test SharePoint API access.
3. If the API still fails, capture the exact traceback / `log.err` and inspect cookie names + domains in `session.json` (confirm both `idat628.sharepoint.com` and `idat628-my.sharepoint.com` have `FedAuth` or `SPOIDCRL`).
4. Once verified, commit the fix and delete this handoff file if no longer needed.
