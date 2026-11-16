# Proposal

## Why

Four audit findings expose behavior gaps in the current authentication and synchronization contracts: bad Moodle credentials still launch the browser and prompt for MFA before failing, `MoodleClient.download_file` overwrites query parameters already present in Moodle download URLs, edited Moodle assignment intros and inline HTML notices (`StringContent`) are never refreshed once written to disk, and a single broken SharePoint link aborts synchronization for an entire `Course`.

## What Changes

- **Mutual login cancellation via `AuthenticationError`**: Introduce `AuthenticationError(RuntimeError)` in `src/idat_sync/application/ports.py`. `MoodleTokenAdapter.fetch_token` and `SharepointBrowserAuth.fetch_storage_state` raise `AuthenticationError` instead of returning `None` when credentials, tokens, MFA input, or browser login fail; `AuthService.login` catches `except* AuthenticationError` around `asyncio.TaskGroup()` so either provider failing cancels the sibling immediately and still returns `False` while real transport/unexpected errors propagate as `ExceptionGroup`. Update `scripts/moodle_json.py` to catch `AuthenticationError` when authenticating directly.
- **Query-preserving Moodle downloads**: `MoodleClient.download_file` merges `wstoken` and `token` into `url`'s existing query parameters instead of passing `params=...` to `httpx.AsyncClient.stream` (which strips parameters like `?forcedownload=1` or `?id=...`).
- **Edited `StringContent` synchronization**: `MoodleAdapter` populates `StringContent.last_modified` from `assign.get("timemodified")` on assignment intros. Add `FileSystemPort.read_bytes(path)` (and `LocalFileSystemAdapter.read_bytes`) so `DownloadManager.diff_trees` compares rendered UTF-8 bytes of existing non-empty `StringContent` items with `last_modified == 0` against disk and passes changed paths into `SyncDiff.from_trees(..., outdated_paths=...)` without breaking 2-argument callers.
- **Per-link resilience in `CourseRepository.list_url_content`**: Catch non-credential exceptions raised while resolving an individual `ModuleContent` URL strategy, log a warning, and continue processing remaining links while still propagating `CredentialsExpiredError` and `ValueError` (path traversal).

## Non-Goals

- Pure bug fixes, missing unit tests, and dead-code cleanups from the audit that preserve existing spec contracts (SharePoint `datetime.min` guard, Windows `\\?\` long-path handling, `LoginFrame` `<Return>` binding, `MainApp.on_close` task cancellation and MFA popup cleanup, `MoodleClient` post-stream `request_delay` and course paging, `colab.ipynb` `xattr` invocation, and removing stale `.select` / `*.spec` files), which are handled directly via TDD without an OpenSpec change.
- Changing `GLOSSARY.md` vocabulary or creating new ADRs.

## Applicable Standards

- `backend/client-adapter-acl`
- `backend/content-strategy-dispatch`
- `global/layer-boundaries`
- `testing/composition-and-seams`

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `authentication`: Raise `AuthenticationError` from `MoodleTokenPort.fetch_token` and `SharepointAuthPort.fetch_storage_state` on rejected `Credentials` or MFA failure so `AuthService.login` cancels the sibling provider via `asyncio.TaskGroup()` and catches `except* AuthenticationError` to return `False`.
- `course-synchronization`: Preserve existing URL query parameters in `MoodleClient.download_file`, detect edited `StringContent` via assignment `timemodified` and `FileSystemPort.read_bytes` byte comparison in `DownloadManager.diff_trees` / `SyncDiff.from_trees`, and skip non-credential URL resolution failures per link in `CourseRepository.list_url_content`.

## Impact

- `src/idat_sync/application/ports.py`
- `src/idat_sync/application/auth_service.py`
- `src/idat_sync/application/course_repository.py`
- `src/idat_sync/application/download_manager.py`
- `src/idat_sync/domain/sync.py`
- `src/idat_sync/infrastructure/filesystem.py`
- `src/idat_sync/infrastructure/moodle_adapter.py`
- `src/idat_sync/infrastructure/moodle_client.py`
- `src/idat_sync/infrastructure/sharepoint_auth.py`
- `scripts/moodle_json.py`
- `tests/test_auth.py`, `tests/test_moodle_client.py`, `tests/test_moodle_adapter.py`, `tests/test_course_repository.py`, `tests/test_sync_pipeline.py`, `tests/test_real_data.py`
