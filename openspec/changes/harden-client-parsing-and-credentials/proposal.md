# Proposal

## Why

A codebase audit uncovered five correctness, security, and maintainability issues: failed Moodle file downloads leak the user's API token into `log.err`, explicit `null` values in Moodle JSON crash course sync, SharePoint recording paths containing `#` or `?` are truncated and skipped, `JsonCredentialStore.save` overwrites `session.json` non-atomically before restricting permissions, and `ModuleContent.filesize` is dead code.

## What Changes

- **Token redaction in `MoodleClient.download_file`**: Catch `httpx.HTTPStatusError` on non-401/403 download failures and re-raise `httpx.HTTPStatusError` with `wstoken` and `token` query parameters stripped from the error message and `request.url`.
- **Null-safe Moodle JSON mapping in `MoodleAdapter`**: Coerce `None` to `""` / `[]` for `summary`, `intro`, `contents`, `introattachments`, `modules`, and `assignments` at the Anti-Corruption Layer boundary so `StringContent` never receives `None`.
- **Direct path unquoting in `SharepointApiAdapter.fetch_recordings`**: Decode `item.file.server_relative_url` with `urllib.parse.unquote` and `PurePosixPath` instead of `urlparse(...).path`, preserving paths containing `#` or `?`.
- **Atomic `Credentials` persistence in `JsonCredentialStore.save`**: Write `Credentials` to a sibling `.part` file created with `0o600` permissions via `os.open`, atomically replace `credentials_file` via `os.replace`, and unlink the `.part` file on error.
- **Dead field removal (**BREAKING** on internal `ModuleContent` constructor)**: Remove unused `filesize: int = 0` from `ModuleContent` and `MoodleAdapter._to_module_content`, aligning `ModuleContent` with `GLOSSARY.md`.

## Non-Goals

- Redacting tokens from non-`HTTPStatusError` transport exceptions outside `MoodleClient.download_file` (e.g., `call_ws` sends `wstoken` in the POST body, not the URL).
- Changing `StringContent` to accept `None` values; domain models stay strictly typed.
- Refactoring unrelated parts of `SharepointApiAdapter`, `MoodleAdapter`, or `SyncFrame`.

## Capabilities

### New Capabilities

<!-- None -->

### Modified Capabilities

- `authentication`: Update `JSON credential persistence and corrupt-file safety` so `JsonCredentialStore.save` writes via a `0o600` sibling `.part` file and `os.replace` instead of truncating `credentials_file` in place.
- `course-synchronization`:
  - Update `Moodle adapter translates vendor curriculum into domain models` to remove `ModuleContent.filesize` and coerce `null` Moodle JSON fields (`summary`, `intro`, `contents`, `introattachments`, `modules`, `assignments`) to `""` / `[]`.
  - Update `SharePoint adapter projects recordings into weekly domain content` to parse `server_relative_url` via `unquote` + `PurePosixPath` without `urlparse` truncation on `#` / `?`.
  - Update `Client-owned file download streaming and SharePoint SDK query execution` so `MoodleClient.download_file` strips `wstoken` and `token` from `httpx.HTTPStatusError` messages and `request.url`.

## Applicable Standards

From `agent-os/standards/index.yml`:
- `backend/client-adapter-acl`
- `global/layer-boundaries`
- `testing/composition-and-seams`

## Impact

- `src/idat_sync/infrastructure/moodle_client.py`
- `src/idat_sync/infrastructure/moodle_adapter.py`
- `src/idat_sync/domain/course.py`
- `src/idat_sync/infrastructure/sharepoint_adapter.py`
- `src/idat_sync/infrastructure/credential_store.py`
- `tests/test_moodle_client.py`, `tests/test_moodle_adapter.py`, `tests/test_sharepoint_adapter.py`, `tests/test_auth.py`
