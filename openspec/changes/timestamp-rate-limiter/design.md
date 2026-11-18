# Design

## Context

See [proposal.md](./proposal.md) for motivation and [specs/course-synchronization/spec.md](./specs/course-synchronization/spec.md) for exact requirements. Per [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md), transport rate-limiting belongs exclusively in `infrastructure/`, wired at the composition root (`src/idat_sync/bootstrap.py`), while `domain/` and `application/` (`IDATSync`, `TaskOrchestrator`, `DownloadManager`, `CourseRepository`) remain pure and decoupled from `infrastructure/`.

## Goals / Non-Goals

**Goals:**
- Provide a single standard-library `AsyncRateLimiter` async context manager in `src/idat_sync/infrastructure/rate_limiter.py` that paces operations end-to-start without trailing post-request sleeps.
- Coordinate `SharepointClient` and `RecordingDownloader` through a single shared `AsyncRateLimiter` instance while giving `MoodleClient` its own `AsyncRateLimiter` instance.
- Preserve existing AST import boundaries (`moodle_client.py`, `sharepoint_client.py`, `sharepoint_adapter.py`, and `downloaders.py` must not import `time` or `requests`).

**Non-Goals:**
- Replacing `TaskOrchestrator` (which still governs per-group sequential task execution, UI `TaskDownloadStarted` ordering, and single-active `.part` file creation).

## Decisions

### 1. End-to-Start `AsyncRateLimiter` Context Manager in `infrastructure/rate_limiter.py`
- **Decision**: Implement `AsyncRateLimiter(delay: float)` with `async def __aenter__(self) -> Self` and `async def __aexit__(self, exc_type, exc_val, exc_tb) -> None` holding `self._lock = asyncio.Lock()` across the guarded operation and stamping `self._next_allowed = time.monotonic() + self.delay` in `__aexit__` when `exc_type is None`.
- **Rationale**:
  - **Why End-to-Start (`__aenter__` / `__aexit__`) over Start-to-Start (`acquire()`)**: Multi-minute file streams and 500 MB `RecordingContent` DASH downloads actively transfer fragments throughout the entire download. Stamping `_next_allowed` in `__aexit__` guarantees a full cooldown window after a long stream closes, while still firing the first request in `0s`, returning in `0s` after the final request (eliminating trailing sleeps and `.part` file rename lag), and allowing cooldown time to elapse while other hosts or local disk diffing run.
  - **Why `if exc_type is None:` in `__aexit__`**: Failed requests (`CredentialsExpiredError`, `RemoteServiceError`, `FileSystemError`, `CancelledError`) do not penalize subsequent calls with a cooldown wait.
  - **Why a separate `rate_limiter.py` module**: Isolates `import time` and `asyncio.sleep` in `src/idat_sync/infrastructure/rate_limiter.py`, keeping `moodle_client.py`, `sharepoint_client.py`, and `downloaders.py` free of `time` imports and direct `asyncio.sleep` calls.

### 2. Shared `AsyncRateLimiter` Injection via `bootstrap.py`
- **Decision**: Replace `request_delay: float` with `rate_limiter: AsyncRateLimiter` on `MoodleClient`, `SharepointClient` (`.create`), and add `rate_limiter: AsyncRateLimiter` to `RecordingDownloader`. In `bootstrap.create_sync_service`, instantiate `moodle_limiter = AsyncRateLimiter(config.request_delay)` and `sharepoint_limiter = AsyncRateLimiter(config.request_delay)`, passing `sharepoint_limiter` to both `SharepointClient.create` and `RecordingDownloader`.
- **Rationale**: `SharepointDownloader` (via `SharepointClient`) and `RecordingDownloader` (via `yt-dlp`) hit the same SharePoint tenant in `TaskOrchestrator`'s third group and immediately follow `SharepointClient` metadata/recording discovery queries. Sharing one `AsyncRateLimiter` instance enforces the cooldown across discovery -> file download -> recording download transitions.

### 3. Unawaited Coroutine Cleanup and `odata.null` Handling in `SharepointClient`
- **Decision**:
  - In `SharepointClient._await_query(self, coro: Awaitable[_ResultT])`, wrap `await coro` in `async with self.rate_limiter:`. If `self.rate_limiter.__aenter__()` raises `BaseException` (such as `asyncio.CancelledError` while sleeping) before `await coro` is reached, close `coro` (`if isinstance(coro, Coroutine): coro.close()`) before re-raising.
  - In `SharepointClient.load_entity`, pass an inner coroutine to `_await_query` that suppresses `TypeError` (`with contextlib.suppress(TypeError): await ctx.execute_query_with_incremental_retry_async()`) so `odata.null` responses exit `async with self.rate_limiter:` cleanly (`exc_type is None`) and stamp `_next_allowed` without a separate `asyncio.sleep` call.
- **Rationale**: Prevents `RuntimeWarning: coroutine was never awaited` on cancellation during rate-limit waits and eliminates duplicate sleep logic in `load_entity`.

## Touched Modules and Public Test Seams

- **Modules touched**:
  - `src/idat_sync/infrastructure/rate_limiter.py`
  - `src/idat_sync/infrastructure/moodle_client.py`
  - `src/idat_sync/infrastructure/sharepoint_client.py`
  - `src/idat_sync/infrastructure/downloaders.py`
  - `src/idat_sync/bootstrap.py`
  - `scripts/moodle_json.py`
  - `agent-os/standards/backend/client-adapter-acl.md`
  - `agent-os/standards/testing/composition-and-seams.md`
- **Public test seams**:
  - `AsyncRateLimiter` (`src/idat_sync/infrastructure/rate_limiter.py`)
  - `MoodleClient` (`src/idat_sync/infrastructure/moodle_client.py`)
  - `SharepointClient` (`src/idat_sync/infrastructure/sharepoint_client.py`)
  - `RecordingDownloader` (`src/idat_sync/infrastructure/downloaders.py`)
  - `bootstrap.create_sync_service` (`src/idat_sync/bootstrap.py`)
  - `scripts/moodle_json.py` and `tests/test_real_data.py`
  - AST architecture boundary (`src/idat_sync/`)

## Risks / Trade-offs

- **[Risk] Coroutine passed to `SharepointClient._await_query(coro)` is leaked if `rate_limiter.__aenter__` is cancelled before `await coro`** → Track whether `await coro` started (or close `coro` in `except BaseException:` around `__aenter__`) so `coro.close()` always runs if `coro` was never awaited.
- **[Risk] Lock leak if `asyncio.sleep` in `AsyncRateLimiter.__aenter__` is cancelled** → Wrap the post-acquire check/sleep in `try ... except BaseException: self._lock.release(); raise`.
