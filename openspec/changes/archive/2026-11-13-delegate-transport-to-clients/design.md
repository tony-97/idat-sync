# Design

## Context

See `proposal.md` for motivation and scope, and [docs/adr/0001-hexagonal-ports-and-adapters.md](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) for the repository's four-layer hexagonal architecture. Within `src/idat_sync/infrastructure/`, external service integrations follow the two-tier `*Client` / `*Adapter` convention (`agent-os/standards/backend/client-adapter-acl.md`), but `MoodleDownloader`, `SharepointDownloader`, and `SharepointApiAdapter` still perform raw HTTP/SDK queries and `time.sleep(self.request_delay)` calls directly.

## Goals / Non-Goals

**Goals:**
- Make `MoodleClient` and `SharepointClient` the sole owners of HTTP/Office365 SDK execution, auth token/cookie attachment, and `time.sleep(self.request_delay)` throttling.
- Keep `MoodleDownloader` and `SharepointDownloader` focused on local directory creation (`mkdir(parents=True, exist_ok=True)`), opening the target file (`open(content.path, "wb")`), and delegating binary streaming to `client.download_file(..., f)`.
- Make `SharepointApiAdapter` a pure Anti-Corruption Layer over `SharepointClient` with zero `ClientContext` or `time.sleep` usage.

**Non-Goals:**
- Wrapping `yt_dlp.YoutubeDL` in a separate client class for `RecordingDownloader`.
- Modifying `SharepointContent.file: Any`, `domain/`, or `application/`.

## Decisions

1. **Stream-based `download_file(..., dest: BinaryIO) -> None` on `MoodleClient` and `SharepointClient`**
   - *Decision*: Both `MoodleClient.download_file(self, url: str, dest: BinaryIO) -> None` and `SharepointClient.download_file(self, file: File, dest: BinaryIO) -> None` accept an open writable binary stream (`BinaryIO`), while `MoodleDownloader` and `SharepointDownloader` handle `Path(content.path).parent.mkdir(parents=True, exist_ok=True)` and `with open(content.path, "wb") as f:`.
   - *Rationale*: Office365's `file.download_session(dest)` requires a writable stream object, and accepting `BinaryIO` in both clients keeps `MoodleClient` and `SharepointClient` 100% free of local filesystem path manipulation and directly testable in memory using `io.BytesIO()`.
   - *Alternatives considered*: Passing `target_path: Path` into `*Client.download_file` (rejected because it mixes local filesystem I/O into transport clients).

2. **Granular SDK query methods on `SharepointClient` vs URL parsing in `SharepointApiAdapter`**
   - *Decision*: `SharepointClient` exposes `fetch_folder_by_guest_url(url: str) -> Folder`, `fetch_file_by_guest_url(url: str) -> File`, `fetch_file_by_id(url: str, file_id: str) -> File`, `fetch_folder_files(folder: Folder) -> list[File]`, and `fetch_subfolders(folder: Folder) -> list[Folder]`. `SharepointApiAdapter` retains URL pattern matching (`can_handle`, `:f:` guest URL branching, and `uniqueid`/`sourcedoc` query parameter extraction in `fetch_file_from_direct_link`) and recursive `.in_folder(save_name)` composition in `fetch_folder_contents` (calling `fetch_folder_files` before `fetch_subfolders`).
   - *Rationale*: Keeps URL classification and domain path projection in the adapter while isolating `ClientContext`, `execute_query_retry()`, `load_entity()`, and `time.sleep(self.request_delay)` inside `SharepointClient`.

3. **Touched Modules & Confirmed Public Test Seams**
   - `src/idat_sync/infrastructure/moodle_client.py` & `src/idat_sync/infrastructure/moodle_adapter.py` -> verified at `tests/test_moodle_client.py` and `tests/test_moodle_adapter.py`.
   - `src/idat_sync/infrastructure/sharepoint_client.py` & `src/idat_sync/infrastructure/sharepoint_adapter.py` -> verified at `tests/test_sharepoint_adapter.py`.
   - `src/idat_sync/infrastructure/downloaders.py` & `src/idat_sync/bootstrap.py` -> verified at `tests/test_sync_pipeline.py` (including AST import boundary inspection).

## Risks / Trade-offs

- **[Risk] Opening `content.path` with `"wb"` before `download_file` runs creates/truncates the file prior to HTTP status validation** -> Mitigation: Matches existing `SharepointDownloader` behavior and keeps stream lifecycle management inside `with open(...)` in the downloader.
- **[Risk] Existing unit tests pass `request_delay` to `SharepointApiAdapter`, `MoodleDownloader`, and `SharepointDownloader`** -> Mitigation: Update test fixtures at each confirmed seam alongside the constructor changes and assert `TypeError` when `request_delay` is passed to those classes.
