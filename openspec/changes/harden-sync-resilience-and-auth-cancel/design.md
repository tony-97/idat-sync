# Design

## Context

See `proposal.md` for motivation and scope, and [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md), [ADR-0002](../../../docs/adr/0002-path-sanitization-compatibility.md), and [ADR-0004](../../../docs/adr/0004-module-id-folders-and-filepath-preservation.md) for hexagonal boundaries and path rules. The decisions below extend existing ports and adapters without adding new runtime dependencies, `GLOSSARY.md` terms, or ADRs.

## Goals / Non-Goals

**Goals:**
- Cancel the sibling authentication task immediately when either Moodle or SharePoint/MFA rejects login while keeping `AuthService.login(...) -> bool` unchanged for callers (`LoginFrame`, `scripts/login_cli.py`, `colab.ipynb`).
- Preserve existing URL query parameters (such as `?forcedownload=1` or `?id=...`) when `MoodleClient.download_file` appends authentication tokens.
- Refresh local HTML artifacts (`StringContent`) when Moodle edits assignment intros (`timemodified`) or timestamp-less section summaries, labels, or bibliographies.
- Isolate broken SharePoint module links per URL in `CourseRepository.list_url_content` so one dead link does not abort an entire `Course`.

**Non-Goals:**
- Pure bug fixes, missing tests, and dead-code removals covered in the separate TDD bug-fix pass (listed in `proposal.md`).
- Introducing content hashing or persisting extra hash sidecar files for `StringContent`.

## Decisions

### 1. Mutual cancellation via `AuthenticationError` in `AuthService.login`
- **Choice**: Define `AuthenticationError(RuntimeError)` in `src/idat_sync/application/ports.py`. `MoodleTokenAdapter.fetch_token(...) -> str` and `SharepointBrowserAuth.fetch_storage_state(...) -> StorageStateData` raise `AuthenticationError` instead of returning `None` when credentials, tokens, MFA input, or browser login fail. `AuthService.login` runs both inside `asyncio.TaskGroup()` and catches `except* AuthenticationError` to return `False`, while non-`AuthenticationError` exceptions (e.g., HTTP 500 `RuntimeError`) still cancel the sibling and propagate as `ExceptionGroup`. Update `scripts/moodle_json.py` `_get_token` to catch `AuthenticationError` and return `None`.
- **Rationale**: Raising `AuthenticationError` lets `asyncio.TaskGroup()` handle bidirectional cancellation natively without wrapper coroutines or sentinel state, while keeping `AuthService.login(...) -> bool` compatible with `LoginFrame`, `scripts/login_cli.py`, and `colab.ipynb`.
- **Alternatives considered**:
  - Keeping `None` returns on the ports and wrapping both tasks inside `AuthService.login` to raise a private `_AuthRejected` sentinel — rejected as unnecessary boilerplate when the ports can raise `AuthenticationError` directly.
  - Letting `RuntimeError` propagate out of `AuthService.login` to all callers — rejected because `login() -> bool` already cleanly represents auth failure for UI, CLI, and Colab callers.

### 2. Query parameter merging in `MoodleClient.download_file`
- **Choice**: Build the request URL with `httpx.URL(url).copy_merge_params({"wstoken": self.token, "token": self.token})` and call `self._client.stream("GET", target_url, follow_redirects=True, timeout=60, headers=REQUEST_HEADER)` without `params=...`.
- **Rationale**: `httpx` replaces an existing query string on `url` when `params=` is passed to `stream()`. `copy_merge_params` preserves `?forcedownload=1`, `?id=...`, or `?file=...` while injecting the Moodle token parameters.
- **Alternatives considered**: Manipulating query strings via `urllib.parse` — rejected because `httpx.URL.copy_merge_params` is native to the client already in use and handles encoding directly.

### 3. Assignment `timemodified` and direct byte comparison for `StringContent`
- **Choice**:
  - In `MoodleAdapter._to_course_module`, pass `int(assign.get("timemodified") or 0)` as `last_modified` on the assignment intro `StringContent`.
  - Add `read_bytes(path: Path | str) -> bytes | None` to `FileSystemPort` and `LocalFileSystemAdapter` (still keeping `exists` and `get_mtime` absent).
  - In `DownloadManager.diff_trees(root_dir, remote_contents)`, for any `StringContent` present in `local_mtimes` with `last_modified == 0` and non-empty `rendered := "".join(content.contents).strip()`, compare `self.file_system.read_bytes(content.path) != rendered.encode("utf-8")` directly and pass changed paths via an optional `outdated_paths: Collection[Path] = ()` keyword parameter to `SyncDiff.from_trees`.
- **Rationale**: Direct byte comparison is both simpler and faster than hashing (hashing still requires reading all bytes from disk first), handles timestamp-less Moodle sections/labels/bibliographies accurately so unchanged HTML files stay `SYNCED` in the UI, and preserves 2-argument `SyncDiff.from_trees(remote_contents, local_mtimes)` calls.
- **Alternatives considered**:
  - Skipping `FileSystemPort` changes and always marking `StringContent(last_modified=0)` as `OUTDATED` — rejected because every sync would show unchanged HTML files as `Pending` -> `Completed` in the treeview.
  - Storing SHA-256 digests — rejected as unnecessary work over small in-memory HTML snippets.

### 4. Per-link failure isolation in `CourseRepository.list_url_content`
- **Choice**: Wrap only `await strategy.list_url_content(...)` in `CourseRepository.list_url_content` with `except CredentialsExpiredError: raise` followed by `except Exception as exc:` logging a warning (`logging.getLogger(__name__).warning(...)`) and continuing the loop. Keep `self._safe_subfolder(content.filepath)` outside that `try` block so `".."` traversal errors still fail fast with `ValueError`.
- **Rationale**: Prevents a single 404/deleted SharePoint resource in one week from aborting all Moodle files and recordings in the course, while preserving auth-expiry abort behavior and traversal checks.
- **Alternatives considered**: Emitting per-link discovery failures as `TaskDownloadFailed` events in the UI — rejected to avoid coupling `CourseRepository` to `SyncEvent` and changing treeview discovery semantics when logging + skipping suffices.

### 5. Confirmed Public Test Seams
- `AuthenticationError`, `AuthService`, `MoodleTokenAdapter`, `SharepointBrowserAuth` (`tests/test_auth.py`)
- `MoodleClient` (`tests/test_moodle_client.py`)
- `MoodleAdapter` (`tests/test_moodle_adapter.py`)
- `CourseRepository` (`tests/test_course_repository.py`)
- `SyncDiff`, `DownloadManager`, `LocalFileSystemAdapter` (`tests/test_sync_pipeline.py`)
- `scripts/moodle_json.py` (`tests/test_real_data.py`)

## Risks / Trade-offs

- **[Risk] `AuthenticationError` caught during `TaskGroup` cancellation alongside a real sibling error** → Python's `except* AuthenticationError:` automatically strips only `AuthenticationError` instances from the `ExceptionGroup` and re-raises any remaining non-`AuthenticationError` exceptions untouched (`AuthService.login` only returns `False` when no non-`AuthenticationError` errors remain).
- **[Risk] `httpx.URL(url).copy_merge_params` on URLs already containing `token=`** → `copy_merge_params` updates/sets `wstoken` and `token` to the active `self.token` while keeping all unrelated query parameters intact.
