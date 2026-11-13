# Design

## Context

See [proposal.md](./proposal.md) for motivation and scope. Architectural boundaries follow [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) (four-layer inward dependency inversion) and [ADR-0002](../../../docs/adr/0002-path-sanitization-compatibility.md) (compatibility-split path sanitization), plus the applicable conventions in `agent-os/standards/index.yml` (`backend/client-adapter-acl`, `backend/content-strategy-dispatch`, `global/layer-boundaries`, `testing/composition-and-seams`).

## Goals / Non-Goals

**Goals:**
- Sanitize every remote filename and nested folder segment before local filesystem paths are built or compared by `SyncDiff`.
- Reject path traversal and anchored relative paths at the `Content.in_folder` domain boundary.
- Surface authentication expiry uniformly across Moodle, SharePoint, and recording downloads via a pure-Python `CredentialsExpiredError` exception without leaking vendor SDK types across layer boundaries.
- Recover gracefully from single-item errors, single-`Course` errors, and `SyncFrame` startup/sync failures while keeping stored `Credentials` intact.

**Non-Goals:**
- Deferred migration of `colab.ipynb` and pure non-spec TDD bugfixes enumerated in [proposal.md](./proposal.md#non-goals).
- Deduplicating colliding sanitized names when two distinct remote titles normalize to the same OS-safe string.

## Decisions

### 1. Shared single-segment sanitizer helper in `domain/path_utils.py`
Extract an internal helper `_sanitize_segment(s, *, replacement, max_len, ascii_only, preserve_extension)` backing both `safe_filename` (`ascii_only=True, preserve_extension=False`) and `safe_path_segment` (`ascii_only=False, preserve_extension=True`):
- Replaces `[<>:"/\\|?*\x00-\x1F\x7F]` with `replacement`, collapses whitespace and consecutive `replacement` tokens.
- When `preserve_extension=True` and a valid extension suffix (`len(suffix) < max_len`) is present, truncates only the stem to `max_len - len(suffix)` and strips `[ .]+$` from the stem before reattaching the extension (so `"Guía: Tema 1/2?.pdf"` becomes `"Guía Tema 1 2.pdf"` without trailing spaces before `.pdf`). Otherwise truncates to `max_len` and strips `[ .]+$` afterward so truncation never leaves a trailing dot or space.
- Replaces empty strings, `"."`, or `".."` with `"untitled"`.
- Checks `stem = result.split(".", 1)[0]` against `WINDOWS_RESERVED` (catching `"CON.txt"` as well as `"CON"`) and prefixes `_` while respecting `max_len`.

*Alternatives considered:*
- *Sanitizing only inside `DownloadManager`*: rejected because `SyncDiff.from_trees` compares `Content.path` against `local_mtimes` prior to `prepare_download_tasks`, so sanitization must occur when adapters and `CourseRepository` build `Content` items.
- *Duplicating regex and Windows checks in two functions*: rejected per repository quality rules; one parameterized helper eliminates drift.

### 2. Adapter + repository segment sanitization paired with `Content.in_folder` fail-closed guard
- `MoodleAdapter` sanitizes generated inline HTML filenames (`-[id]-resumen.html`, `.html`, `-[id]-intro.html`, bounding the prefix stem so `-[id]-...` is never truncated away) and `list_url_content` filenames via `safe_path_segment`.
- `SharepointApiAdapter` sanitizes file names, subfolder names, and week folder segments via `safe_path_segment`.
- `CourseRepository` preserves existing blank-name skipping (`if not module.name:` / `if not section.name:`) and passes `safe_path_segment(module.name)` / `safe_path_segment(section.name)` to `.in_folder(...)`.
- `Content.in_folder(folder)` validates `self.path` before returning early on empty `folder`: it raises `ValueError` if `self.path.is_absolute()`, `bool(self.path.anchor)`, or `".." in self.path.parts`, and if `folder` has `".." in Path(folder).parts`. Absolute `course_dir` values remain permitted as the outer root.

*Alternatives considered:*
- *Silently stripping `..` inside `Content.in_folder`*: rejected as the primary guard because adapters already sanitize untrusted input; failing closed in `Content.in_folder` makes any bypassed sanitization immediately visible in tests instead of silently flattening paths.

### 3. Layer-pure `CredentialsExpiredError(RuntimeError)` mapping
- Add `CredentialsExpiredError(RuntimeError)` to `application/ports.py` (`__all__`). Subclassing `RuntimeError` preserves backwards compatibility with callers/tests expecting `RuntimeError`.
- `MoodleClient`: `call_ws` checks `response.status_code in (401, 403)` before `raise_for_status()` and maps `errorcode == "invalidtoken"` to `CredentialsExpiredError`; `download_file` raises `CredentialsExpiredError("403 Forbidden: your session cookie is missing/expired.")` on HTTP `401` or `403`.
- `SharepointClient`: raises `CredentialsExpiredError` when `not personal_site.url`, and routes SDK query calls through one helper catching `ClientRequestException` / HTTP errors whose response `status_code in (401, 403)` to raise `CredentialsExpiredError`.
- `RecordingDownloader`: catches `yt_dlp.utils.DownloadError`, inspects attached `HTTPError` status `(401, 403)` or HTTP `401`/`403` failure strings, and raises `CredentialsExpiredError`.

*Alternatives considered:*
- *Handling `requests.HTTPError` / `ClientRequestException` in `TaskOrchestrator` or `SyncFrame`*: rejected because it violates [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) inward dependency boundaries.

### 4. Early cross-group abort and per-item / per-`Course` isolation
- `TaskOrchestrator.run_async` passes a `threading.Event` (`abort_event`) to `_run_group`. Before each `task.download()`, `_run_group` exits early if `abort_event.is_set()`. On `CredentialsExpiredError`, `_run_group` calls `abort_event.set()` and re-raises; any other `Exception` is logged with the task path and skipped so sibling tasks and groups finish.
- Keeping `asyncio.gather` (rather than `TaskGroup`) propagates `CredentialsExpiredError` directly without wrapping in `ExceptionGroup`.
- `IDATSync.sync_courses` wraps each `self.sync_course(course, course_dir)` in `try ... except CredentialsExpiredError: raise` / `except Exception` (logging failed `Course.name` and continuing to subsequent courses).

### 5. Non-destructive `SyncFrame` recovery and lazy retry
- `SyncFrame.__init__` saves `self.credentials = credentials`.
- `load_idat_sync` and `do_sync` run inside `try ... except CredentialsExpiredError ... except Exception ... finally: self.is_syncing.set(False)`, writing status/error lines into `self.progress_queue`.
- Neither method clears stored `Credentials` or auto-switches to `LoginFrame`; expired-credential messages direct the user to click `Logout`.
- `do_sync` skips empty `folder_path` or concurrent `is_syncing`, and when `self.idat_sync is None`, retries `await asyncio.to_thread(create_sync_service, self.credentials, self.config)` before calling `sync_courses`.

## Risks / Trade-offs

- **Worker thread unwind delay on abort** → If a second downloader group is sleeping inside `request_delay` when another group hits `CredentialsExpiredError`, `asyncio.gather` delivers the failure to the caller while the sleeping worker unwinds cleanly on its next loop iteration because `abort_event.is_set()` skips further `task.download()` calls.
- **Extension truncation with unusually long suffixes** → If a filename has a long dot-separated tail near `max_len`, `preserve_extension` only keeps extensions strictly shorter than `max_len` (and strips trailing `[ .]+$` after any fallback slicing) so output length is always bounded by `max_len`.
- **Two distinct remote names normalizing to one local segment** → Existing risk shared with `safe_filename`; no automatic suffix disambiguation is added so `SyncDiff` stays deterministic against existing directories.
