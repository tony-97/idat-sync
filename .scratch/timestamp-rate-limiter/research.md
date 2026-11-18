# Timestamp Rate-Limiter (`AsyncRateLimiter`) Research & Design

## Part 1: Primary-Source Findings

### 1. Python Standard Library Primitives (`time.monotonic`, `asyncio.Lock`, `asyncio.sleep`)
- **`time.monotonic()`** ([Python `time` docs](https://docs.python.org/3/library/time.html#time.monotonic)): Returns the value (in fractional seconds) of a monotonic clock that cannot go backward and is unaffected by system clock updates. Ideal for measuring cooldown intervals (`_next_allowed - now`).
- **`asyncio.Lock()`** ([Python `asyncio` synchronization primitives](https://docs.python.org/3/library/asyncio-sync.html#asyncio.Lock)): A FIFO mutex lock for asyncio tasks, ensuring at most one coroutine enters the rate-limited section at a time and serializing cooldown checks across callers sharing the same limiter.
- **Unawaited Coroutine Cleanup (`coro.close()`)** ([Python Data Model — Coroutine Objects](https://docs.python.org/3/reference/datamodel.html#coroutine-objects)): When a helper method like `SharepointClient._await_query(self, coro: Awaitable[_ResultT])` receives an already-instantiated coroutine object and awaits `self.rate_limiter.__aenter__()` before `await coro`, cancelling the task during `__aenter__` leaves `coro` unawaited unless `coro.close()` is called before re-raising.

### 2. Installed Dependencies & SDK Inspection (`pyproject.toml`, `office365-rest-python-client`)
- **`pyproject.toml` (lines 8–16)**: No third-party rate-limiting dependency (`aiolimiter`, `limits`, etc.) is installed.
- **`office365-rest-python-client==3.3.1`**:
  - `ClientContext.with_auto_retry()` (`office365.runtime.retry`) handles reactive retries on transient HTTP status codes (`408, 429, 500, 502, 503, 504`) honoring `Retry-After` headers.
  - `office365.runtime.http.throttling.RateLimiter` (`ThrottledTransport`) is reactive to response headers (`X-SharePointHealthScore`, `Retry-After`, `RateLimit-*`) rather than proactive fixed-delay pacing, and `ThrottledTransport` in v3.3.1 does not override `BaseTransport.stream_async` used by `File.download_session_async`.

---

## Part 2: Pre-Refactor Audit of `src/idat_sync/`

1. **`src/idat_sync/infrastructure/moodle_client.py`**:
   - `MoodleClient.__init__(self, token: str, base_url: str, request_delay: float)` (lines 64–74) stores `self.request_delay`.
   - `MoodleClient.call_ws` (line 124) and `MoodleClient.download_file` (line 217) call `await asyncio.sleep(self.request_delay)` unconditionally **after** every successful request/stream.
2. **`src/idat_sync/infrastructure/sharepoint_client.py`**:
   - `SharepointClient.__init__` and `SharepointClient.create` (lines 32–67) accept `request_delay: float`.
   - `SharepointClient._await_query` (line 157) calls `await asyncio.sleep(self.request_delay)` **after** every SharePoint SDK query and file download (`create`, `load_entity`, `search_for_recordings`, `fetch_document_library_items`, `fetch_folder_files`, `fetch_subfolders`, `download_file`).
   - `SharepointClient.load_entity` (lines 165–167) catches `TypeError` (from `odata.null` guest URL responses) and duplicates `await asyncio.sleep(self.request_delay)`.
3. **`src/idat_sync/infrastructure/downloaders.py`**:
   - `MoodleDownloader` and `SharepointDownloader` wrap `client.download_file` inside `_atomic_download` (lines 44–59). Because `download_file` sleeps for `request_delay` before returning, `<filename>.part` remains open and `TaskDownloadCompleted` is delayed by 30 seconds after all bytes have arrived.
   - `RecordingDownloader` (lines 115–227) shares Group 3 in `TaskOrchestrator` with `SharepointDownloader` (`bootstrap.py:89–95`), downloading 500 MB+ DASH streams via `yt-dlp`, but previously had no rate-limiter coordination with `SharepointClient`.

---

## Part 3: Confirmed Design Decisions (Grilling Session)

1. **`AsyncRateLimiter` (`src/idat_sync/infrastructure/rate_limiter.py`)**:
   - Implemented as an **End-to-Start** async context manager (`__aenter__` / `__aexit__`) with `self.delay: float = delay`, `self._lock = asyncio.Lock()`, and `self._next_allowed: float = 0.0`.
   - `__aenter__`: If `self.delay <= 0`, returns `self` immediately. Otherwise acquires `self._lock`; if `now < self._next_allowed` (via `time.monotonic()`), awaits `asyncio.sleep(self._next_allowed - now)` (releasing `self._lock` on `BaseException`).
   - `__aexit__`: If `self.delay <= 0`, returns immediately. Otherwise stamps `self._next_allowed = time.monotonic() + self.delay` when `exc_type is None` and releases `self._lock` in `finally`.
2. **Constructor Injection & Shared SharePoint Limiter**:
   - `MoodleClient(token, base_url, rate_limiter: AsyncRateLimiter)`, `SharepointClient(..., rate_limiter: AsyncRateLimiter)` / `SharepointClient.create(..., rate_limiter: AsyncRateLimiter)`, and `RecordingDownloader(cookies, sharepoint_personal_host, rate_limiter: AsyncRateLimiter)` require `rate_limiter: AsyncRateLimiter` (storing `self.rate_limiter`) and reject `request_delay`.
   - `bootstrap.create_sync_service(credentials, config)` constructs `moodle_limiter = AsyncRateLimiter(config.request_delay)` for `MoodleClient` and a single shared `sharepoint_limiter = AsyncRateLimiter(config.request_delay)` passed to both `SharepointClient.create` and `RecordingDownloader`.
   - `scripts/moodle_json.py` passes `rate_limiter=AsyncRateLimiter(config.request_delay)` and `tests/test_real_data.py` passes `rate_limiter=AsyncRateLimiter(0.0)`.
3. **Wrapped Boundaries & Coroutine Cleanup**:
   - `MoodleClient.call_ws`: `async with self.rate_limiter:` wraps the HTTP POST and response/payload validation so `_next_allowed` is stamped only when `call_ws` succeeds.
   - `MoodleClient.download_file`: `async with self.rate_limiter:` wraps `async with self._client.stream("GET", ...):` so the HTTP stream closes before `__aexit__` stamps `_next_allowed` and returns immediately.
   - `SharepointClient._await_query`: wraps `await coro` in `async with self.rate_limiter:`, closing `coro` (`if isinstance(coro, Coroutine): coro.close()`) if `__aenter__` raises before `await coro` executes.
   - `SharepointClient.load_entity`: suppresses `TypeError` (`odata.null`) inside the coroutine passed to `_await_query` so `odata.null` exits `async with self.rate_limiter:` cleanly (`exc_type is None`) and stamps `_next_allowed` without any separate `asyncio.sleep` call.
   - `RecordingDownloader.download`: wraps `await asyncio.to_thread(self._download_sync, content, cancel_event)` inside `async with self.rate_limiter:`.
4. **Unchanged Layers & Docs**:
   - `TaskOrchestrator`, `IDATSync`, `CourseRepository`, `DownloadManager`, `SyncConfig`, `GLOSSARY.md`, and `docs/adr/` remain unchanged.
5. **Public Test Seams**:
   - `AsyncRateLimiter` (`src/idat_sync/infrastructure/rate_limiter.py`)
   - `MoodleClient` (`src/idat_sync/infrastructure/moodle_client.py`)
   - `SharepointClient` (`src/idat_sync/infrastructure/sharepoint_client.py`)
   - `RecordingDownloader` (`src/idat_sync/infrastructure/downloaders.py`)
   - `bootstrap.create_sync_service` (`src/idat_sync/bootstrap.py`)
   - `scripts/moodle_json.py` and `tests/test_real_data.py`
   - AST architecture boundary (`src/idat_sync/`)
