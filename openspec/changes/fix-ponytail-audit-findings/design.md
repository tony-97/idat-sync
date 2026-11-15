# Design

## Context

See `proposal.md` for motivation and scope, and [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) and [ADR-0002](../../../docs/adr/0002-path-sanitization-compatibility.md) for the hexagonal layer boundaries and path-sanitization rules. All changes stay inside `infrastructure/` and `ui/` without altering `domain/` or `application/` port contracts.

## Goals / Non-Goals

**Goals:**
- Prevent interrupted file downloads from leaving partial files that `SyncDiff` mistakes for synced files.
- Prevent prefix `Course` names (e.g. `"Matemática I"` vs. `"Matemática II"`) from matching the wrong course's SharePoint recordings.
- Ensure macOS Playwright login launches WebKit (`p.webkit.launch(headless=False)`), and ensure closing the MFA popup or submitting `<Return>` never deadlocks `LoginFrame`.
- Restrict persisted `Credentials` file permissions to `0o600` and eliminate unclosed `HttpxTransport` socket leaks in `SharepointClient`.
- Remove dead UI and adapter scaffolding (`dialogs.py`, `AppFrame.exit`, `LoginFrame.get_mfa_code_ui`, `SharepointClient.onedrive_cookies`, and `SharepointApiAdapter.__get_name`/`__get_last_modified`).

**Non-Goals:**
- Changing `colab.ipynb`.
- Changing `request_delay` throttling on `MoodleClient` or `SharepointClient`.

## Decisions

### 1. Atomic `.part` file writes in `MoodleDownloader` and `SharepointDownloader`
- **Choice**: Stream bytes into `part_path = target.with_name(f"{target.name}.part")`, call `os.replace(part_path, target)` only after `download_file` completes, and call `part_path.unlink(missing_ok=True)` in `except:` before re-raising. In `LocalFileSystemAdapter.scan_mtimes`, skip any file where `p.name.endswith(".part")`.
- **Rationale**: `os.replace` is atomic on both POSIX and Windows when source and destination share the same directory (`target.parent`). Filtering `.part` in `scan_mtimes` ensures orphaned `.part` files from hard crashes never appear in `SyncDiff.local_only_paths`.
- **Alternatives considered**: Deleting `target` on exception after writing directly to `target` — rejected because an existing older version of `target` (`OUTDATED`) would be destroyed before the new download succeeded, and a hard crash mid-stream would still leave a corrupted `target` with a fresh `st_mtime`.

### 2. Boundary-aware course name matching in `SharepointApiAdapter`
- **Choice**: Add a helper `_matches_course_name(course_name: str, text: str) -> bool` using `bool(re.search(rf"(?<![0-9A-Za-zÀ-ÿ]){re.escape(course_name)}(?![0-9A-Za-zÀ-ÿ])", text))` and apply it to `title` in `__find_recordings_site` and `urllib.parse.unquote(relative_url)` in `is_course_recording`.
- **Rationale**: Rejects superstring collisions like `"Matemática I"` inside `"Matemática II"` or `"Inglés I"` inside `"Inglés III"`, while still matching `"Grabaciones - Matemática I"`, `"Matemática I"`, and URL-encoded SharePoint paths like `"/personal/u/Documents/Matem%C3%A1tica%20I/Grabaciones/video.mp4"`.
- **Alternatives considered**: Exact path-segment equality — rejected because SharePoint `Title` values frequently include prefixes such as `"Grabaciones - <course_name>"`.

### 3. Playwright browser engine dispatch & MFA popup cancellation
- **Choice**:
  - In `SharepointBrowserAuth.fetch_storage_state`, dispatch browser launch by `platform.system()`: `await p.webkit.launch(headless=False)` on `"Darwin"`, `await p.chromium.launch(headless=False, channel="msedge")` on `"Windows"`, and `await p.chromium.launch(headless=False, channel="chromium")` otherwise. Strip the code returned by `await get_mfa_code()` and return `None` immediately if empty.
  - In `LoginFrame.get_mfa_code`, bind `<Return>` on the MFA `Entry` to submit, and bind `popup.protocol("WM_DELETE_WINDOW", on_close)` to resolve `future` with `""` (if not done) and destroy `popup`.
- **Rationale**: Matches the browsers installed in `.github/workflows/build_and_deploy.yml` and `README.md` (`webkit` on macOS, `chromium` on Linux, built-in `msedge` on Windows). Resolving a dismissed MFA popup with `""` lets `SharepointBrowserAuth` exit cleanly via its normal `finally:` browser/context cleanup and returns `False` from `AuthService.login` without raising an unhandled cancellation traceback in `async_tkinter_loop`.

### 4. Shared `HttpxTransport` in `SharepointClient` and `0o600` in `JsonCredentialStore`
- **Choice**: Instantiate `self._transport = HttpxTransport()` once in `SharepointClient.__init__`, pass `self._transport` to `ctx.pending_request().with_async_transport(self._transport)` in `get_client`, add `async def aclose(self) -> None` awaiting `self._transport.aclose()`, and call `await client.aclose()` if `SharepointClient.create` fails. In `JsonCredentialStore.save`, call `os.chmod(self.credentials_file, 0o600)` after `json.dump`.
- **Rationale**: `HttpxTransport` manages an internal `httpx.Client` and `httpx.AsyncClient` pool; sharing a single transport across `ClientContext` instances avoids creating a new unclosed connection pool per SharePoint URL or folder.

### 5. Confirmed Public Test Seams
- `SharepointBrowserAuth` (`src/idat_sync/infrastructure/sharepoint_auth.py`)
- `JsonCredentialStore` (`src/idat_sync/infrastructure/credential_store.py`)
- `LoginFrame` & `MainApp` (`src/idat_sync/ui/login_frame.py`, `src/idat_sync/ui/app.py`, `src/idat_sync/ui/__init__.py`)
- `MoodleDownloader` & `SharepointDownloader` (`src/idat_sync/infrastructure/downloaders.py`)
- `LocalFileSystemAdapter` (`src/idat_sync/infrastructure/filesystem.py`)
- `SharepointApiAdapter` (`src/idat_sync/infrastructure/sharepoint_adapter.py`)
- `SharepointClient` (`src/idat_sync/infrastructure/sharepoint_client.py`)

## Risks / Trade-offs

- **[Risk] `os.chmod(path, 0o600)` behavior on Windows** → On Windows, Python's `os.chmod` only toggles the read-only attribute and is a safe no-op for POSIX group/other bits (`0o600` keeps owner read/write enabled), while enforcing strict `0600` permissions on Linux and macOS.
