# Design

## Context

See `proposal.md` for motivation and scope. Hexagonal layer boundaries are governed by [ADR 0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) and `agent-os/standards/global/layer-boundaries.md`; client/adapter separation by `agent-os/standards/backend/client-adapter-acl.md`.

## Goals / Non-Goals

**Goals:**
- Prevent Moodle API tokens from appearing in `httpx.HTTPStatusError` messages or `request.url` on failed downloads.
- Prevent explicit JSON `null` values from entering `StringContent` or crashing `MoodleAdapter` list iteration.
- Preserve SharePoint recording paths containing `#` or `?` when filtering by folder name.
- Make `JsonCredentialStore.save` atomic and owner-only from file creation.
- Remove the unused `ModuleContent.filesize` field.

**Non-Goals:**
- Redacting `RequestError` outside `MoodleClient.download_file` or changing `MoodleClient.call_ws` (which passes `wstoken` in the POST body).
- Weakening `StringContent.contents` typing to `list[str | None]`.

## Decisions

### 1. Redacting tokens in `MoodleClient.download_file`
- **Decision:** Wrap `response.raise_for_status()` in `try ... except httpx.HTTPStatusError as exc:` and raise a new `httpx.HTTPStatusError` built from a sanitized `httpx.Request` whose URL has `wstoken` and `token` query params removed via `httpx.URL.copy_remove_param(...)` (with the message rewritten using the sanitized URL).
- **Alternatives considered:** Raising `RuntimeError` without a URL would break callers/tests expecting `httpx.HTTPStatusError`. Logging a sanitized message only in `log_sync_event` would still leave the token inside `exc` and UI status messages.

### 2. Normalizing `null` Moodle fields in `MoodleAdapter`
- **Decision:** Coerce optional fields at the Anti-Corruption Layer boundary (`raw_section.get("summary") or ""`, `assign.get("intro") or ""`, `raw_module.get("contents") or []`, `assign.get("introattachments") or []`, `raw_section.get("modules") or []`, `raw_course.get("assignments") or []`) instead of adding `None` guards inside `StringContent.rendered_bytes()`.
- **Alternatives considered:** Filtering `None` inside `StringContent.rendered_bytes()` would leak vendor `null` handling into the domain layer.

### 3. Direct path unquoting in `SharepointApiAdapter.fetch_recordings`
- **Decision:** Replace `urlparse(item.file.server_relative_url or "").path` with `urllib.parse.unquote(item.file.server_relative_url or "")` and `pathlib.PurePosixPath(decoded_path).parent.name.lower()`, matching `SharepointApiAdapter.get_name`.
- **Alternatives considered:** Pre-escaping `#` and `?` before `urlparse` is unnecessary because `server_relative_url` is already a server-relative path, not a full URL.

### 4. Atomic `0o600` writes in `JsonCredentialStore.save`
- **Decision:** Write JSON to a sibling `<filename>.part` file opened via `os.open(part_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)` wrapped in `os.fdopen(..., "w", encoding="utf-8")`, call `os.chmod(part_path, 0o600)` (in case an existing `.part` file had looser permissions), then `os.replace(part_path, self.credentials_file)`. In `except BaseException:`, unlink `part_path` (`missing_ok=True`) before handling `OSError` / re-raising.
- **Alternatives considered:** `tempfile.NamedTemporaryFile` in the same directory works too, but a deterministic sibling `.part` path matches `_atomic_download` in `downloaders.py` and avoids orphaned random temp files.

### 5. Removing `ModuleContent.filesize`
- **Decision:** Delete `filesize: int = 0` from `ModuleContent` and `MoodleAdapter._to_module_content`.

## Risks / Trade-offs

- [External callers constructing `ModuleContent(..., filesize=...)`] → None found in `src/` or `scripts/`; only one test fixture in `tests/test_moodle_adapter.py` passes `"filesize"` in raw JSON, which `MoodleAdapter` will ignore.
- [`os.open` mode `0o600` on Windows] → Windows ignores POSIX group/other permission bits, which is acceptable and matches the existing `os.chmod(..., 0o600)` behavior.
