# Design

## Context

Per [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md), `SharepointClient` in `src/idat_sync/infrastructure/sharepoint_client.py` is the sole owner of Office365 SharePoint REST SDK transport and `time.sleep(self.request_delay)` throttling. See `proposal.md` for motivation.

## Goals / Non-Goals

**Goals:**
- Ensure `SharepointClient.fetch_document_library_items` sleeps `self.request_delay` once per query rather than twice.
- Avoid the extra unthrottled `ServerRelativePath` GET request during `SharepointClient.download_file`.
- Replace `office365`'s broken `execute_query_with_incremental_retry()` and `execute_query_retry()` wrappers with `execute_query()` managed by `SharepointClient._run_query`.

**Non-Goals:**
- Changing adapter or domain interfaces.

## Decisions

### 1. Pass `use_path=False` to `file.download_session(dest, use_path=False)`
- **Rationale**: `SharepointClient.properties_to_retrieve` loads `["Name", "ServerRelativeUrl", "TimeLastModified"]`. `File.download_session(dest)` defaults to `use_path=True`, which calls `ensure_property("ServerRelativePath", ...)` and triggers an extra HTTP GET before streaming `$value`. With `use_path=False`, `download_session` checks `"ServerRelativeUrl"`, which is already present on loaded `File` instances, so streaming `$value` executes in a single HTTP request.
- **Alternatives considered**: Adding `"ServerRelativePath"` to `properties_to_retrieve`—rejected because `use_path=False` directly uses `"ServerRelativeUrl"` (which is already retrieved and also supports future cache reconstruction).

### 2. Centralize `429`/`503` retry backoff in `SharepointClient._run_query` and use `.execute_query()`
- **Rationale**: In `office365-rest-python-client`, `execute_query_with_incremental_retry()` passes `timeout_secs=0` by value to `execute_query_retry()`, and `execute_query_retry()` catches all `ClientRequestException` errors (including `401`, `403`, `404`), retries 5 times, and silently swallows the exception on exhaustion. Calling `.execute_query()` inside `_run_query` allows `_run_query` to:
  1. Immediately map HTTP `401` and `403` to `CredentialsExpiredError` on the first attempt.
  2. Immediately re-raise non-transient errors (e.g., HTTP `404`).
  3. On HTTP `429` or `503`, re-queue `ctx.current_query` when `ctx` is provided (such as in `load_entity`), sleep `int(Retry-After)` when the response header is a valid integer or `self.request_delay` otherwise, retry up to `max_retry=5` total attempts, and re-raise the exception if all attempts fail.
- **Alternatives considered**: Monkey-patching `ClientContext.execute_query_with_incremental_retry`—rejected because owning retry logic in `SharepointClient._run_query` keeps all transport error mapping and throttling in one testable method.

### 3. Public Test Seams
- `SharepointClient` seam (`tests/test_sharepoint_adapter.py`, `tests/test_sync_pipeline.py`): verified by patching `idat_sync.infrastructure.sharepoint_client.ClientContext` and `idat_sync.infrastructure.sharepoint_client.time.sleep`.

## Risks / Trade-offs

- **[Risk] `ctx.load(entity, ...)` in `load_entity` pops the query from `ctx` before `execute_query()` fails** → **Mitigation**: Perform `ctx.load(entity, self.properties_to_retrieve)` inside the `_run_query` action lambda (`lambda: (ctx.load(entity, self.properties_to_retrieve), ctx.execute_query())`) so each retry queues a fresh load query on `ctx`.
