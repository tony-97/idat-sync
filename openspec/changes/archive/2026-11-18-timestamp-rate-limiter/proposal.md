# Proposal

## Why

`MoodleClient` and `SharepointClient._await_query` currently execute an unconditional trailing `await asyncio.sleep(self.request_delay)` after every network call and file stream, adding a 30-second idle wait after the final request of every batch (including 60+ seconds of dead startup delay before the UI loads courses and a 30-second lag before `.part` files rename and emit `TaskDownloadCompleted`) while leaving `RecordingDownloader` uncoordinated with `SharepointClient`'s cooldown window. Replacing trailing sleeps with an injected `AsyncRateLimiter` async context manager (`__aenter__` / `__aexit__`) fires the first request immediately, returns immediately on completion while stamping the end-to-start cooldown timestamp for the next request, and coordinates `SharepointClient` and `RecordingDownloader` through a single shared limiter.

## What Changes

- Introduce `AsyncRateLimiter(delay: float)` in `src/idat_sync/infrastructure/rate_limiter.py` as an end-to-start async context manager (`__aenter__` acquires an `asyncio.Lock` and awaits `asyncio.sleep(self._next_allowed - now)` when `self.delay > 0` and `now < self._next_allowed`; `__aexit__` stamps `self._next_allowed = time.monotonic() + self.delay` when `exc_type is None` and releases the lock).
- **BREAKING**: Replace `request_delay: float` with `rate_limiter: AsyncRateLimiter` on `MoodleClient.__init__`, `SharepointClient.__init__`, and `SharepointClient.create`, and require `rate_limiter: AsyncRateLimiter` on `RecordingDownloader.__init__` (`request_delay` is rejected on all three).
- Wrap `MoodleClient.call_ws`, `MoodleClient.download_file`, `SharepointClient._await_query` (closing unawaited `coro` if `__aenter__` raises before `await coro`, and suppressing `TypeError` for `odata.null` inside `load_entity`'s query coroutine), and `RecordingDownloader.download` in `async with self.rate_limiter:`, removing all `await asyncio.sleep(self.request_delay)` calls from `moodle_client.py` and `sharepoint_client.py`.
- Wire `moodle_limiter = AsyncRateLimiter(config.request_delay)` into `MoodleClient` and a single shared `sharepoint_limiter = AsyncRateLimiter(config.request_delay)` into both `SharepointClient.create` and `RecordingDownloader` inside `bootstrap.create_sync_service`, and update `scripts/moodle_json.py` and `tests/test_real_data.py`.

## Non-Goals

- Changing `TaskOrchestrator`, `IDATSync`, `CourseRepository`, `DownloadManager`, `SyncConfig`, `GLOSSARY.md`, or adding third-party rate-limiting dependencies.
- Modifying `MoodleTokenClient` or `SharepointBrowserAuth` login flows.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `course-synchronization`: Update composition root (`create_sync_service`), client/downloader rate-limiting (`AsyncRateLimiter` injection on `MoodleClient`, `SharepointClient`, and `RecordingDownloader`), `RecordingDownloader` download execution, client streaming/query execution, `scripts/moodle_json.py`, and `tests/test_real_data.py` requirements.

## Applicable Standards

- `backend/client-adapter-acl`
- `backend/content-strategy-dispatch`
- `global/layer-boundaries`
- `testing/composition-and-seams`

## Impact

- **Code**: `src/idat_sync/infrastructure/rate_limiter.py` (new), `src/idat_sync/infrastructure/moodle_client.py`, `src/idat_sync/infrastructure/sharepoint_client.py`, `src/idat_sync/infrastructure/downloaders.py`, `src/idat_sync/bootstrap.py`, `scripts/moodle_json.py`.
- **Tests & Standards**: `tests/test_moodle_client.py`, `tests/test_sharepoint_adapter.py`, `tests/test_sync_pipeline.py`, `tests/test_real_data.py`, `agent-os/standards/backend/client-adapter-acl.md`, `agent-os/standards/testing/composition-and-seams.md`.
