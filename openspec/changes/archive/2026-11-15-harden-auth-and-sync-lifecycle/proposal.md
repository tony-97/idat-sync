# Proposal

## Why

The codebase audit uncovered six behavioral and resource-lifecycle gaps where the existing OpenSpec contracts lock in broken or leaky behavior: cross-host SharePoint cookie overwriting via substring matching (`#6`), stale Moodle `Course.sections` and cached assignments on repeat desktop syncs (`#7`), silent browser SSO failures and missing on-screen `LoginFrame` error feedback (`#10`), leaked `httpx` transports during UI retry/logout/close (`#12`), unpooled per-call TLS handshakes in `MoodleClient` (`#13`), and non-atomic HTML writes in `StringDownloader` (`#15`). Fixing these requires updating the affected behavioral specifications cleanly before implementation.

## What Changes

- **BREAKING** (`#6`): Replace `SharepointClient`'s substring domain filter and single flat `self.sharepoint_cookies` dictionary with per-host cookie resolution using exact-host or leading-dot suffix matching (with exact-host precedence), exposed via `cookies_for_host(host: str) -> dict[str, str]` and bound per site in `get_client(site_url)`.
- **BREAKING** (`#7`): Add `invalidate_cache() -> None` to `CourseGateway`, `MoodleAdapter` (resetting `_assignments_cache` to `None`), and `CourseRepository`, invoke it at the start of `IDATSync.sync_courses(...)`, and clear selected `Course.sections` before re-syncing in `SyncFrame.do_sync`.
- (`#10`): Log caught `Exception` failures in `SharepointBrowserAuth.fetch_storage_state` via `logging.getLogger(__name__).exception(...)` while still returning `None`, and add a visible error label (`error_var`) to `LoginFrame` updated both when `AuthService.login(...)` returns `False` and when it raises.
- (`#12`, `#13`): Reuse a single shared `httpx.AsyncClient` inside `MoodleClient` across `call_ws` and `download_file`; add `aclose()` and async context manager methods (`__aenter__`/`__aexit__`) on `MoodleClient`, `SharepointClient`, and `IDATSync` (via optional `closers` injected in `bootstrap.create_sync_service`, closing `moodle_client` if `SharepointClient.create` fails); and close active transports in `SyncFrame.load_idat_sync` error recovery, async `SyncFrame.do_logout`, `AppFrame.aclose()`, and `MainApp.on_close` (`WM_DELETE_WINDOW`).
- (`#15`): Route `StringDownloader.download` through `_atomic_download` so UTF-8 `.html` writes use sibling `<filename>.part` files and `os.replace`.

## Non-Goals

- Disambiguating duplicate `Content.path` values and tightening `SyncDiff.from_trees` (`#5`): deferred for separate triage in `.scratch/duplicate-content-paths/issues/01-label-and-leaf-path-collisions.md`.
- Non-spec bug fixes and cleanups (`#1–#4`, `#8–#9`, `#11`, `#14`, `#16–#18`): handled outside OpenSpec via `diagnosing-bugs` and `tdd` per `AGENTS.md`.
- Converting `MoodleTokenClient.request_token` (single pre-auth POST) to a persistent client.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `authentication`: Log browser auth exceptions in `SharepointBrowserAuth`, display login failure/exception messages in `LoginFrame`, and close `IDATSync` transports cleanly during `SyncFrame` startup failure, async logout, and `MainApp` window close (`WM_DELETE_WINDOW`).
- `course-synchronization`: Scope SharePoint cookies per host in `SharepointClient`, invalidate Moodle assignment caches and selected `Course.sections` per sync pass, share a pooled `httpx.AsyncClient` in `MoodleClient` with `aclose()`/`__aenter__`/`__aexit__` wired through `bootstrap.create_sync_service` and `IDATSync`, and make `StringDownloader.download` atomic via `_atomic_download`.

## Impact

- **Modules touched**: `src/idat_sync/application/ports.py`, `src/idat_sync/application/course_repository.py`, `src/idat_sync/application/sync_service.py`, `src/idat_sync/infrastructure/sharepoint_client.py`, `src/idat_sync/infrastructure/sharepoint_auth.py`, `src/idat_sync/infrastructure/moodle_client.py`, `src/idat_sync/infrastructure/moodle_adapter.py`, `src/idat_sync/infrastructure/downloaders.py`, `src/idat_sync/bootstrap.py`, `src/idat_sync/ui/app.py`, `src/idat_sync/ui/login_frame.py`, `src/idat_sync/ui/sync_frame.py`.
- **Tests updated**: `tests/test_sharepoint_adapter.py`, `tests/test_moodle_client.py`, `tests/test_course_repository.py`, `tests/test_sync_pipeline.py`, `tests/test_auth.py`.
- **Applicable `agent-os` standards**: `global/layer-boundaries`, `backend/client-adapter-acl`, `testing/composition-and-seams`.
