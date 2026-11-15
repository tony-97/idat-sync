# Tasks: Harden Auth and Sync Lifecycle

## 1. Client Cookie Isolation, Connection Pooling & Atomic HTML Writes

- [x] 1.1 At the `SharepointClient` seam, update `tests/test_sharepoint_adapter.py` with failing tests verifying `cookies_for_host(host)` resolves `FedAuth`, `rtFa`, and `SPOIDCRL` by lowercased target host vs exact/wildcard cookie domains (rejecting boundary mismatches and letting exact-host cookies win over wildcard domain cookies), removes flat `self.sharepoint_cookies`, and passes a host-scoped cookie lookup in `get_client(site_url)`; then update `src/idat_sync/infrastructure/sharepoint_client.py` until `tests/test_sharepoint_adapter.py` passes.
- [x] 1.2 At the `SharepointClient` seam, add a failing test in `tests/test_sharepoint_adapter.py` verifying `SharepointClient` supports `async with` (`__aenter__` / `__aexit__` delegating to `aclose()`); then update `src/idat_sync/infrastructure/sharepoint_client.py` until `tests/test_sharepoint_adapter.py` passes.
- [x] 1.3 At the `MoodleClient` seam, update `tests/test_moodle_client.py` with failing tests verifying `MoodleClient` initializes a single `self._client = httpx.AsyncClient()` reused across requests and closes it via `aclose()` and `async with` (`__aenter__` / `__aexit__`) while `MoodleTokenClient` keeps its per-call client; then update `src/idat_sync/infrastructure/moodle_client.py` until `tests/test_moodle_client.py` passes.
- [x] 1.4 At the `StringDownloader` seam, add a failing test in `tests/test_sync_pipeline.py` verifying `StringDownloader.download` writes UTF-8 encoded `StringContent` bytes via `_atomic_download(content.path, ...)` (`*.part` + `os.replace`, preserving existing destination content and cleaning up `.part` on failure); then update `src/idat_sync/infrastructure/downloaders.py` until `tests/test_sync_pipeline.py` passes.

## 2. Cache Invalidation & Composition Root Teardown Wiring

- [x] 2.1 At the `MoodleAdapter` / `CourseGateway` seam, add a failing test in `tests/test_moodle_adapter.py` verifying `MoodleAdapter.invalidate_cache()` clears cached lookup state so subsequent calls fetch fresh data from `MoodleClient`; then add `invalidate_cache(self) -> None` to `CourseGateway` in `src/idat_sync/application/ports.py` and `MoodleAdapter` in `src/idat_sync/infrastructure/moodle_adapter.py` until `tests/test_moodle_adapter.py` passes.
- [x] 2.2 At the `CourseRepository` seam, add a failing test in `tests/test_course_repository.py` verifying `CourseRepository.invalidate_cache()` clears repository state and delegates to `CourseGateway.invalidate_cache()` so the next `list_courses()` re-fetches updated `Course` data; then add `invalidate_cache(self) -> None` to `src/idat_sync/application/course_repository.py` until `tests/test_course_repository.py` passes.
- [x] 2.3 At the `IDATSync` seam, add failing tests in `tests/test_sync_pipeline.py` verifying `IDATSync.sync_courses` invokes `invalidate_cache()` on the repository before iterating selected courses and `IDATSync` executes injected `closers` in `aclose()` (and via `__aenter__` / `__aexit__`); then update `src/idat_sync/application/sync_service.py` until `tests/test_sync_pipeline.py` passes.
- [x] 2.4 At the `bootstrap.create_sync_service` composition seam, add failing tests in `tests/test_sync_pipeline.py` verifying `create_sync_service` injects `[moodle_client.aclose, sharepoint_raw_client.aclose]` into `IDATSync` and awaits `moodle_client.aclose()` if `SharepointClient.create(...)` fails; then update `src/idat_sync/bootstrap.py` until `tests/test_sync_pipeline.py` passes.

## 3. Authentication Diagnostics & UI Lifecycle Teardown

- [x] 3.1 At the `SharepointBrowserAuth` seam, add a failing test in `tests/test_auth.py` verifying `fetch_storage_state()` emits an exception log via `logging.getLogger(__name__).exception(...)` while still returning `None` when Playwright raises; then update `src/idat_sync/infrastructure/sharepoint_auth.py` until `tests/test_auth.py` passes.
- [x] 3.2 At the `LoginFrame` seam, add failing tests in `tests/test_auth.py` verifying `LoginFrame` exposes `error_var` bound to a red `tk.Label`, clears prior errors on submit, shows an error message on `False` auth results, and renders unpacked exception messages (including `ExceptionGroup.exceptions`) on raised errors; then update `src/idat_sync/ui/login_frame.py` until `tests/test_auth.py` passes.
- [x] 3.3 At the `SyncFrame` seam, add failing tests in `tests/test_auth.py` verifying: `SyncFrame.do_sync()` clears selected `Course.sections` before syncing while reusing the existing `SharepointClient`; partial `load_idat_sync` / `_ensure_sync_service` failure closes created `IDATSync` resources via `SyncFrame.aclose()`; and `async def do_logout()` awaits `SyncFrame.aclose()` when bound via `async_handler(self.do_logout)`; then update `src/idat_sync/ui/sync_frame.py` until `tests/test_auth.py` passes.
- [x] 3.4 At the `AppFrame` / `MainApp` seam, add failing tests in `tests/test_auth.py` verifying `AppFrame.aclose()` provides an async teardown hook and `MainApp.on_close` is registered on `WM_DELETE_WINDOW` via `async_handler(self.on_close)` to await active frame `aclose()` before window destruction; then update `src/idat_sync/ui/app.py` until `tests/test_auth.py` passes.

## 4. Final Verification

- [x] 4.1 Run the full test suite (`python -m unittest discover -s tests -v`), compile/syntax check (`python -m compileall src tests`), and configured typecheck/lint checks; review `git diff` against all specs in `openspec/changes/harden-auth-and-sync-lifecycle/`.

## Workflow Notes

- Non-spec bug fixes and small cleanups stay out of OpenSpec (`diagnosing-bugs` + `tdd` per `AGENTS.md`).
- Audit finding `#5` (duplicate `Content.path` collisions) remains deferred in `.scratch/duplicate-content-paths/issues/01-label-and-leaf-path-collisions.md`.
