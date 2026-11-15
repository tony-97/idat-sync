# Proposal

## Why

A repository quality audit surfaced correctness, security, resource-leak, and dead-code issues: macOS SharePoint login launches Chromium instead of WebKit, dismissing the MFA dialog freezes the Login button forever, interrupted downloads leave partial files that `SyncDiff` permanently marks `SYNCED`, substring course matching collides on numbered course sequences (e.g. `"Matemática I"` vs. `"Matemática II"`), `JsonCredentialStore` writes `Credentials` with default world-readable permissions, `SharepointClient` leaks unclosed `HttpxTransport` sockets on every context creation, and unused UI/adapter scaffolding remains in the tree. Fixing these now hardens login, synchronization, and resource cleanup without expanding scope.

## What Changes

- **macOS WebKit browser launch**: `SharepointBrowserAuth.fetch_storage_state` launches `await p.webkit.launch(headless=False)` on `"Darwin"`, `await p.chromium.launch(headless=False, channel="msedge")` on `"Windows"`, and `await p.chromium.launch(headless=False, channel="chromium")` on other platforms, and returns `None` without submitting when `await get_mfa_code()` returns a blank/whitespace string.
- **MFA popup dismissal & `LoginFrame` cleanup**: `LoginFrame.get_mfa_code` inlines `get_mfa_code_ui`, binds `<Return>` on the MFA `Entry` to submit, and binds `WM_DELETE_WINDOW` to resolve the future with `""` and destroy the popup so closing the dialog cleanly aborts login and re-enables the `Login` button. Removes commented-out `loading_dialog` and `else: ...` lines.
- **UI dead-code removal**: Deletes `src/idat_sync/ui/dialogs.py` (`LoadingDialog`), removes `LoadingDialog` from `src/idat_sync/ui/__init__.py`, deletes `AppFrame.exit` from `src/idat_sync/ui/app.py`, and updates `MainApp.__change` to call `self.__frame.destroy()`.
- **Owner-only `Credentials` permissions**: `JsonCredentialStore.save` calls `os.chmod(self.credentials_file, 0o600)` after writing `Credentials` JSON.
- **Atomic file downloads & `.part` filtering**: `MoodleDownloader.download` and `SharepointDownloader.download` write to a sibling `<filename>.part` temporary file, atomically `os.replace` onto `content.path` on success, and `unlink(missing_ok=True)` the `.part` file on error. `LocalFileSystemAdapter.scan_mtimes` ignores `*.part` files.
- **Boundary-aware course recording matching & alias cleanup**: `SharepointApiAdapter` matches `course_name` using non-alphanumeric boundaries (`re.search(rf"(?<![0-9A-Za-zÀ-ÿ]){re.escape(course_name)}(?![0-9A-Za-zÀ-ÿ])", ...)`) against `title` and `urllib.parse.unquote(relative_url)`, and removes the redundant `__get_name` and `__get_last_modified` aliases.
- **Shared `HttpxTransport` lifecycle & dead cookie attribute removal**: `SharepointClient` reuses a single `self._transport = HttpxTransport()` across all `get_client(site_url)` calls, exposes `async def aclose(self) -> None` to close `self._transport`, calls `await client.aclose()` on startup failure in `SharepointClient.create`, and removes unused `self.onedrive_cookies`.

## Non-Goals

- Updating `colab.ipynb` (explicitly skipped).
- Altering `request_delay` rate-limiting behavior on `SharepointClient` and `MoodleClient` operations.

## Applicable Standards

- `backend/client-adapter-acl`
- `backend/content-strategy-dispatch`
- `global/layer-boundaries`
- `testing/composition-and-seams`

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `authentication`: Launch `p.webkit.launch(headless=False)` on macOS (`Darwin`), abort cleanly on blank MFA codes or closed MFA popups, enforce `0o600` permissions in `JsonCredentialStore.save`, and remove dead `LoadingDialog` / `AppFrame.exit` / `get_mfa_code_ui` UI code.
- `course-synchronization`: Stream `MoodleDownloader` and `SharepointDownloader` into sibling `.part` files with `os.replace` and `.part` cleanup on error, ignore `*.part` files in `LocalFileSystemAdapter.scan_mtimes`, match `course_name` at non-alphanumeric boundaries on `title` and `unquote(relative_url)` in `SharepointApiAdapter` without `__get_name`/`__get_last_modified` aliases, and reuse a single closable `HttpxTransport` in `SharepointClient` without `onedrive_cookies`.

## Impact

- `src/idat_sync/infrastructure/sharepoint_auth.py`
- `src/idat_sync/infrastructure/credential_store.py`
- `src/idat_sync/infrastructure/downloaders.py`
- `src/idat_sync/infrastructure/filesystem.py`
- `src/idat_sync/infrastructure/sharepoint_adapter.py`
- `src/idat_sync/infrastructure/sharepoint_client.py`
- `src/idat_sync/ui/app.py`, `src/idat_sync/ui/login_frame.py`, `src/idat_sync/ui/__init__.py`, `src/idat_sync/ui/dialogs.py` (deleted)
- `tests/test_auth.py`, `tests/test_sharepoint_adapter.py`, `tests/test_sync_pipeline.py`
