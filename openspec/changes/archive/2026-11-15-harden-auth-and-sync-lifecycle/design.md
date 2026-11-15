# Design

## Context

See [proposal.md](./proposal.md) for the six audited behavioral and resource-lifecycle gaps motivating this change, and [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) for the inward-pointing four-layer dependency rules (`ui/`, `infrastructure/` -> `application/` -> `domain/`, wired in `bootstrap.py`).

Currently:
- `SharepointClient` (`src/idat_sync/infrastructure/sharepoint_client.py`) collapses filtered auth cookies into a single flat `self.sharepoint_cookies` dictionary via substring matching (`subdomain in cookie_domain`), allowing unrelated lookalike domains to match and different hosts' `FedAuth`/`rtFa` cookies to overwrite one another.
- `MoodleClient` (`src/idat_sync/infrastructure/moodle_client.py`) constructs and tears down a new `httpx.AsyncClient` on every `call_ws` and `download_file` call, while `IDATSync` (`src/idat_sync/application/sync_service.py`) has no `aclose()` hook and `SyncFrame` / `MainApp` can drop or switch frames without closing `SharepointClient` (or `MoodleClient`) transports.
- `SyncFrame.do_sync` keeps populated `Course.sections` and `MoodleAdapter._assignments_cache` alive across clicks, skipping Moodle refreshes during a second `Sync`.
- `SharepointBrowserAuth` swallows browser `Exception`s silently without logging, and `LoginFrame` displays no error text on failed or raised logins.
- `StringDownloader.download` duplicates `mkdir` and writes `.html` files directly without `.part` + `os.replace` atomicity.

## Goals / Non-Goals

**Goals:**
- Isolate SharePoint cookies strictly per request host using exact-host or leading-dot suffix matching.
- Give `MoodleClient`, `SharepointClient`, and `IDATSync` clean async transport lifetimes (`aclose()`, `__aenter__`, `__aexit__`), wired via `bootstrap.py` without breaking [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md), and close active transports deterministically at every GUI exit/retry/logout transition.
- Invalidate Moodle assignment caches and selected `Course.sections` on each `Sync` pass while keeping the active `SharepointClient` connection open.
- Surface login failures and exceptions visibly in `LoginFrame` and log browser auth exceptions in `SharepointBrowserAuth`.
- Route `StringDownloader.download` through `_atomic_download`.

**Non-Goals:**
- Duplicate `Content.path` deduplication (`#5`, deferred to `.scratch/duplicate-content-paths/issues/01-label-and-leaf-path-collisions.md`) and non-spec Track A bug fixes/cleanups (`#1–#4`, `#8–#9`, `#11`, `#14`, `#16–#18`).
- Pooling `MoodleTokenClient.request_token` (single pre-auth POST executed only during login).

## Decisions

### 1. Per-host SharePoint cookie resolution (`#6`)
- **Decision:** Replace `self.sharepoint_cookies` and `subdomain` filtering in `SharepointClient` with `cookies_for_host(self, host: str) -> dict[str, str]` over the raw `cookies` stored on the instance, and bind `lambda host=target_host: self.cookies_for_host(host)` inside `get_client(site_url)` after extracting `urlparse(site_url).hostname or ""`.
- **Matching & precedence rule:** Lowercase both `host` and `cookie.get("domain", "")`. Only accept `FedAuth`, `rtFa`, and `SPOIDCRL` when `cookie_domain == target_host` OR (`cookie_domain.startswith(".")` and (`target_host == cookie_domain.lstrip(".")` or `target_host.endswith(cookie_domain)`)), keeping subdomain matching strictly boundary-safe (`target_host.endswith(cookie_domain)` requires the leading `.` before `cookie_domain` when `target_host` is a subdomain, or exact equality with `cookie_domain.lstrip(".")`, so `"evil.sharepoint.com.attacker.test"` is rejected). Apply domain-suffix wildcard matches before exact-host matches so host-scoped cookies win regardless of input cookie order.
- **Alternative considered:** Keeping one merged `dict` keyed by cookie name, or keeping `ClientContext`'s static dict at `__init__` time. Rejected because multiple hosts (`contoso.sharepoint.com` vs `contoso-my.sharepoint.com`) legitimately carry distinct `FedAuth` values.

### 2. Async transport lifecycle & hexagonal cleanup wiring (`#12`, `#13`)
- **Decision:** Keep [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) inward dependency boundaries intact by injecting optional `closers: Sequence[Callable[[], Awaitable[None]]] = ()` into `IDATSync.__init__` instead of adding `aclose()` methods to `CourseGateway` and `RecordingGateway`.
  - `MoodleClient` instantiates one `self._client = httpx.AsyncClient()` in `__init__` and reuses it across `call_ws` and `download_file`, adding `aclose()` (`await self._client.aclose()`) plus `__aenter__`/`__aexit__`.
  - `SharepointClient` adds `__aenter__`/`__aexit__` delegating to its existing `aclose()`.
  - `bootstrap.create_sync_service` passes `closers=[moodle_client.aclose, sharepoint_raw_client.aclose]` to `IDATSync`, and awaits `moodle_client.aclose()` inside `except BaseException:` if `await SharepointClient.create(...)` fails during startup.
  - `IDATSync.aclose()` consumes and clears `self._closers` once, awaits every closer in `try ... finally` style (ensuring remaining closers run even if an earlier one raises, then re-raising the first failure if any), and supports `__aenter__` / `__aexit__`.
- **UI teardown hooks:**
  - `AppFrame` defines `async def aclose(self) -> None: pass`.
  - `SyncFrame.aclose()` closes and nulls `self.idat_sync` (`service, self.idat_sync = self.idat_sync, None; if service is not None: await service.aclose()`). Called on: (a) `load_idat_sync` / `_ensure_sync_service` failure when `self.idat_sync` was already created before `list_courses()` failed, (b) `async def do_logout()` bound via `async_handler(self.do_logout)` (ignoring clicks while `is_syncing` is `True`, closing `self.idat_sync`, then calling `switch_to_login()` in `finally:`), and (c) `MainApp.on_close()` registered on `"WM_DELETE_WINDOW"` via `async_handler(self.on_close)` (guarding re-entry, awaiting `self.__frame.aclose()` inside `try ... finally: self.destroy()`).
- **Alternative considered:** `__del__()` destructors or tearing down/recreating `IDATSync` on every `Sync`. Rejected because `__del__()` cannot reliably `await` async `httpx` socket cleanup after loop shutdown, and recreating `IDATSync` repeats `get_personal_site()` + `request_delay`.

### 3. Per-sync cache invalidation (`#7`)
- **Decision:** Add `def invalidate_cache(self) -> None` to `CourseGateway`, `MoodleAdapter` (resetting `self._assignments_cache = None`), and `CourseRepository` (delegating to `self.course_gateway.invalidate_cache()`). `IDATSync.sync_courses` calls `self.course_repository.invalidate_cache()` at the start of each sync pass (so assignments are still fetched only once across all courses inside that sync pass, then refreshed on the next pass), and `SyncFrame.do_sync` clears `course.sections.clear()` on checked courses right before calling `sync_courses`.
- **Alternative considered:** Removing `_assignments_cache` completely. Rejected because a 35-course first sync would re-fetch `mod_assign_get_assignments` 35 times with 35 `request_delay` waits.

### 4. Login diagnostics & error banner (`#10`)
- **Decision:** Add `logging.getLogger(__name__).exception(...)` in `SharepointBrowserAuth.fetch_storage_state` while continuing to return `None`, and add `self.error_var = tk.StringVar(value="")` plus a visible red `tk.Label` in `LoginFrame`. Clear `error_var` at the start of `do_login`, display `"Login failed. Check your credentials or MFA and try again."` when `await login_task` returns `False`, and format unpacked `ExceptionGroup.exceptions` / `Exception` messages into `error_var` (alongside `_logger.error(...)`) when raised.

### 5. Atomic HTML writes in `StringDownloader` (`#15`)
- **Decision:** Encode `"".join(content.contents).strip().encode("utf-8")` and route through the existing `_atomic_download(content.path, ...)` helper already used by `MoodleDownloader` and `SharepointDownloader`.

## Risks / Trade-offs

- **[Risk] Removing `SharepointClient.sharepoint_cookies` breaks direct readers or existing `test_sharepoint_adapter.py:L332`** -> Documented as a breaking change; replace assertions on `client.sharepoint_cookies` with `client.cookies_for_host(...)` and `.with_cookies(...)` callback verification, while checking all callers via whole-repo search (none outside tests read `sharepoint_cookies`).
- **[Risk] Reusing `httpx.AsyncClient` on `MoodleClient` changes unit test patch expectations in `tests/test_moodle_client.py`** -> Update `_mock_async_client` helper in `tests/test_moodle_client.py` to mock `httpx.AsyncClient.return_value` methods (`post`, `stream`, `aclose`) directly rather than `__aenter__`/`__aexit__`.
- **[Risk] `MainApp.on_close()` bound via `async_handler` in headless tests without a running loop** -> Unit tests exercise `await app.on_close()` directly inside `asyncio.run(...)` (matching `StubMaster` and `exercise_do_login` conventions in `tests/test_auth.py`), while live execution runs inside `async_mainloop`.
- **[Risk] `SyncFrame` partial initialization (`create_sync_service` succeeds, `list_courses()` fails)** -> Calling `await self.aclose()` in `_ensure_sync_service` / `load_idat_sync` guarantees no `IDATSync` or underlying `httpx` pool survives failed startup attempts, and mocks in `tests/test_auth.py` remain compatible because failure cases set `aclose = AsyncMock()` when mocking returned services.
