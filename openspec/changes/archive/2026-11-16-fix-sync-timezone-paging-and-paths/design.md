# Design

## Context

See `proposal.md` for motivation and scope, and [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md), [ADR-0002](../../../docs/adr/0002-path-sanitization-compatibility.md), and [ADR-0004](../../../docs/adr/0004-module-id-folders-and-filepath-preservation.md) for hexagonal boundaries and path rules. Neither `GLOSSARY.md` nor `docs/adr/` requires changes, and audit Finding #10 (`SyncConfig` `session_file`/`log_file` defaults) is explicitly out of scope.

## Goals / Non-Goals

**Goals:**
- Resolve all 15 confirmed audit findings (`#1`–`#9`, `#11`–`#16`) cleanly inside one cohesive change with regression tests at every public seam.

**Non-Goals:**
- Changing `SyncConfig.session_file` or `SyncConfig.log_file` defaults (`./session.json` and `./log.err`, Finding #10 skipped).

## Decisions

### 1. Recording timezone, `datetime.min` guard, and SharePoint `$select` / `create()` fixes
- **Choice**:
  - Add `timezone: str = "America/Lima"` to `SyncConfig`, stripped and validated via `ZoneInfo(cleaned)` (catching `(ZoneInfoNotFoundError, ValueError)` and raising `ValueError`), and add `"tzdata; sys_platform == 'win32'"` to `pyproject.toml`. Require `timezone: str` on `SharepointApiAdapter.__init__` wired from `config.timezone` in `bootstrap.create_sync_service`. Convert tz-aware `file.time_created` values via `.astimezone(self.timezone)` before `.date()`, leaving naive test datetimes unchanged.
  - Guard `office365`'s `datetime.min` sentinel in `SharepointApiAdapter.get_last_modified` (return `fallback_last_modified` when `f.time_last_modified` is `None` or `== datetime.min`, catching `OSError` on `.timestamp()`) and `fetch_recordings` (skip when `file.time_created is None` or `== datetime.min`).
  - Fix `SharepointClient.fetch_document_library_items` to `.select(["File/Name", "File/ServerRelativeUrl", "File/TimeCreated", "File/TimeLastModified"]).expand(["File"]).get_all_async(page_size=50)` (removing the overwritten `.select(["FileSystemObjectType"])`), and change `SharepointClient.create`'s `except Exception:` to `except BaseException:` so cancellation still awaits `client.aclose()`.
- **Rationale**: `office365` defaults missing dates to `datetime.min` (truthy, crashing `.timestamp()` on Windows), parses `TimeCreated` as UTC, and overwrites `query_options.select` on chained `.select(...)` calls. Using `ZoneInfo` + `tzdata` works on both Windows and UTC Colab containers without guessing local host timezones.

### 2. Windows `\\?\` long paths, bounded extensions, and cycle-safe disambiguation
- **Choice**:
  - Add a Windows extended-length path helper (`\\?\<abspath>` for drive paths, `\\?\UNC\<server\share...>` for UNC paths) applied only at the OS boundary inside `LocalFileSystemAdapter` (`scan_mtimes`, `ensure_dir`, `read_bytes`) and `downloaders.py` (`_atomic_download` and `RecordingDownloader._download_sync`'s `mkdir`). `LocalFileSystemAdapter.scan_mtimes` builds relative paths from the `\\?\` root and re-joins them to `Path(root_dir)` so `Content.path` and `SyncDiff` keys stay unprefixed.
  - Restrict `_sanitize_segment(..., preserve_extension=True)` to `1 <= len(ext) <= 15` with `not any(ch.isspace() for ch in ext)`. Update `safe_suffixed_segment` to truncate `prefix` directly within `max_len - len(suffix)` without re-running `preserve_extension` across `prefix + suffix`. In `IDATSync._disambiguate_paths`, only split `item.path.suffix` when `1 <= len(ext) <= 16` (including `.`) and `not any(ch.isspace() for ch in ext)`.
- **Rationale**: Preserves `ADR-0002` and `ADR-0004` (`max_len=100`, clean UI/log paths) while supporting 233+ char course paths on Windows and preventing infinite loops on long dotted titles.

### 3. Moodle pagination and stream sleep lifecycle
- **Choice**: Keep `MoodleClient.fetch_enrolled_courses(classification="all", limit=100, offset=0)` and loop `call_ws(...)` advancing `offset` via `nextoffset` until `courses` is empty, `nextoffset` is not a non-bool `int`, or `nextoffset <= current_offset`. Move `await asyncio.sleep(self.request_delay)` outside `async with self._client.stream(...)` in `MoodleClient.download_file`.
- **Rationale**: Covers >100-course students without breaking single-page callers/mocks and closes HTTP streams immediately after writing bytes.

### 4. UI `<Return>`, destroyed-widget safety, task/MFA/`yt-dlp` cancellation, and error status
- **Choice**:
  - Bind `LoginFrame.pass_entry` `<Return>` with `async_handler(self.do_login)`.
  - Guard `validate_buttons` (on both `LoginFrame` and `SyncFrame`) and `finally:` widget updates with `self.winfo_exists()`.
  - Wrap `return await future` in `LoginFrame.get_mfa_code` in `try ... finally:` to destroy `popup` when cancelled.
  - Track active async tasks (`self._active_task`) on `LoginFrame` and `SyncFrame`, cancelling and awaiting them inside `aclose()` before `MainApp.on_close` calls `destroy()`.
  - In `RecordingDownloader.download`, create a per-download `cancel_event = threading.Event()`, register a `yt_dlp` `progress_hook` that raises `yt_dlp.utils.DownloadCancelled` when `cancel_event.is_set()`, and catch `asyncio.CancelledError` around `await asyncio.to_thread(...)` to set `cancel_event` before re-raising.
  - Track `has_errors` in `SyncFrame.do_sync` from `CourseSyncCompleted.error` and display `"Sync completed with errors."` vs `"Sync done."`.
- **Rationale**: Stops background tasks, MFA dialogs, and `yt-dlp` threads cleanly on `on_close`, login rejection, or `CredentialsExpiredError` without modifying `Downloader` or `IDATSync` signatures.

### 5. Colab, CLI scripts, dead exports, and package URLs
- **Choice**:
  - In `colab.ipynb`: replace `getoutput(f'xattr ... "{path}"')` with `subprocess.check_output(["xattr", "-p", "user.drive.id", path], text=True).strip()`; add `course.sections.clear()` and `idat_sync.course_repository.invalidate_cache()` before `sync_course`; refresh `.pdf` if `os.path.getmtime(path) > os.path.getmtime(pdf_path)`; close `idat_sync` via `await idat_sync.aclose()` when done.
  - In `scripts/moodle_json.py`: wrap `MoodleClient` in `async with`.
  - Remove unused imports (`ABC`, `abstractmethod`, `dataclass`) and dead `__all__` re-exports in `downloaders.py`, `infrastructure/__init__.py`, and `download_manager.py` (updating `tests/test_sync_pipeline.py` to import `Downloader` from `idat_sync.application.ports`).
  - Fix `[project.urls]` in `pyproject.toml` (`Homepage = "https://github.com/tony-97/idat-sync"`, `Issues = "https://github.com/tony-97/idat-sync/issues"`).

### 6. Confirmed Public Test Seams
- `SyncConfig`, `load_config` (`tests/test_config.py`)
- `SharepointApiAdapter`, `SharepointClient` (`tests/test_sharepoint_adapter.py`, `tests/test_real_data.py`), `bootstrap.create_sync_service` (`tests/test_sync_pipeline.py`)
- `LocalFileSystemAdapter`, `_atomic_download`, `RecordingDownloader`, `path_utils`, `IDATSync` (`tests/test_sync_pipeline.py`)
- `MoodleClient` (`tests/test_moodle_client.py`)
- `LoginFrame`, `SyncFrame`, `MainApp` (`tests/test_auth.py`)
- `scripts/moodle_json.py` (`tests/test_real_data.py`)

## Risks / Trade-offs

- **[Risk] `RecordingDownloader` worker thread finishes aborting asynchronously after `CancelledError`** → `yt_dlp` invokes `progress_hook` on every downloaded chunk, raising `DownloadCancelled` promptly so threads do not linger past chunk boundaries.
- **[Risk] `colab.ipynb` executes against live Colab Drive/X11** → Validated syntactically (valid JSON/Python cells, AST/grep checks) since Colab Drive runtime is unavailable locally.
