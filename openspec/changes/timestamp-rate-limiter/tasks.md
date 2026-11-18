# Tasks

## 1. AsyncRateLimiter and MoodleClient Seam

- [x] 1.1 Write failing unit tests for `AsyncRateLimiter` (`src/idat_sync/infrastructure/rate_limiter.py`) and `MoodleClient` (`src/idat_sync/infrastructure/moodle_client.py`) in `tests/test_moodle_client.py`, then implement `AsyncRateLimiter(delay: float)` and update `MoodleClient` (`rate_limiter: AsyncRateLimiter`, wrapping `call_ws` and `download_file` in `async with self.rate_limiter:` without `request_delay` or `asyncio` imports) and verify `pytest tests/test_moodle_client.py` passes.

## 2. SharepointClient Seam

- [x] 2.1 Write failing unit tests for `SharepointClient` (`src/idat_sync/infrastructure/sharepoint_client.py`) in `tests/test_sharepoint_adapter.py`, then update `SharepointClient` and `SharepointClient.create` to require `rate_limiter: AsyncRateLimiter`, wrap `_await_query` in `async with self.rate_limiter:` (closing `coro` if `__aenter__` raises before `await coro`), and suppress `odata.null` `TypeError` inside `load_entity`'s query coroutine without `asyncio.sleep`, verifying `pytest tests/test_sharepoint_adapter.py` passes.

## 3. RecordingDownloader, Composition Root, Scripts, and Standards

- [x] 3.1 Write failing tests in `tests/test_sync_pipeline.py` and `tests/test_real_data.py` for `RecordingDownloader(..., rate_limiter: AsyncRateLimiter)` (`src/idat_sync/infrastructure/downloaders.py`), `create_sync_service` (`src/idat_sync/bootstrap.py` wiring `moodle_limiter` and shared `sharepoint_limiter`), `scripts/moodle_json.py`, and live smoke tests, then implement those changes and update `agent-os/standards/backend/client-adapter-acl.md` and `agent-os/standards/testing/composition-and-seams.md`, verifying `pytest tests/test_sync_pipeline.py tests/test_real_data.py` passes.

## 4. Full Verification

- [x] 4.1 Run the full test suite (`pytest`), typecheck, and linters (`ruff check .`, `ruff format --check .`, `pylint src tests`) and verify all checks pass with zero warnings or errors.
