# Proposal

## Why

`SharepointClient` currently suffers from three bugs that cause unnecessary delays, unthrottled double requests, and broken error/retry handling:
1. `fetch_document_library_items` sleeps `self.request_delay` twice per call—once after the purely in-memory `client.web.default_document_library()` call and again inside `_run_query`.
2. `download_file` calls `file.download_session(dest)` with the default `use_path=True`, which checks for `"ServerRelativePath"` (a property not included in `properties_to_retrieve`) and fires an extra unthrottled HTTP GET immediately before streaming `$value`.
3. `office365`'s `execute_query_with_incremental_retry()` and `execute_query_retry()` pass `timeout_secs=0` by value on `429`/`503` throttling (retrying 5 times with `sleep(0)`) and swallow `ClientRequestException` after exhausting retries instead of failing fast on `401`/`403`/`404` or re-raising on retry exhaustion.

## What Changes

- Remove the redundant `time.sleep(self.request_delay)` after `client.web.default_document_library()` in `SharepointClient.fetch_document_library_items` so it sleeps `self.request_delay` once via `_run_query`.
- Pass `use_path=False` in `SharepointClient.download_file` (`file.download_session(dest, use_path=False)`) so `office365` uses the already-retrieved `"ServerRelativeUrl"` property and streams `$value` in a single HTTP request.
- Replace `.execute_query_with_incremental_retry()` and `.execute_query_retry()` calls in `SharepointClient` with `.execute_query()`, and handle `429`/`503` retry backoff directly inside `SharepointClient._run_query` (re-queuing `ctx.current_query` when needed, sleeping `int(Retry-After)` or `self.request_delay` up to `max_retry=5`, failing immediately without retry on `401`/`403` with `CredentialsExpiredError` and on non-transient errors such as `404`, and re-raising when retries are exhausted).

## Non-Goals

- Implementing local SharePoint metadata caching or per-course UI selection (tracked separately in `.scratch/sharepoint-metadata-cache/exploration.md`).
- Changing `MoodleClient`, `SharepointApiAdapter`, `SharepointDownloader`, `SyncConfig`, or domain/application layers.

## Capabilities

### New Capabilities
- None

### Modified Capabilities
- `course-synchronization`: Update `SharepointClient` query execution, retry/backoff handling, single-sleep document library retrieval, and `download_session(dest, use_path=False)` streaming requirements.

## Impact

- **Code**: `src/idat_sync/infrastructure/sharepoint_client.py`
- **Tests**: `tests/test_sharepoint_adapter.py`, `tests/test_sync_pipeline.py`
- **Applicable Agent OS Standards (`agent-os/standards/index.yml`)**:
  - `backend/client-adapter-acl`
  - `testing/composition-and-seams`
