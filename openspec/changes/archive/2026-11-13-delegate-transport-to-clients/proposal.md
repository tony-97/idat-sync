# Proposal

## Why

`MoodleDownloader`, `SharepointDownloader`, and `SharepointApiAdapter` currently perform raw HTTP and Office365 SDK transport operations (`requests.get`, `download_session(...).execute_query_with_incremental_retry()`, `get_files().select(...).execute_query_retry()`, `get_folders().execute_query_retry()`, `get_file_by_guest_url`, `get_folder_by_guest_url`, `get_file_by_id`) and manage their own `time.sleep(self.request_delay)` throttling, while `MoodleAdapter` leaks a `token` property solely for `MoodleDownloader`. Consolidating all HTTP/SDK transport and rate-limiting into `MoodleClient` and `SharepointClient` enforces the two-tier client/adapter boundary and makes downloaders and adapters pure orchestrators over client seams.

## What Changes

- **BREAKING**: Add `MoodleClient.download_file(self, url: str, dest: BinaryIO) -> None` to handle authenticated Moodle file streaming (`requests.get`, `wstoken`/`token`, `RQ_HEADER`, `403` check, `raise_for_status()`, `1024 * 256` chunk streaming, and `time.sleep(self.request_delay)`).
- **BREAKING**: Remove `@property def token` from `MoodleAdapter` and change `MoodleDownloader.__init__(self, client: MoodleClient) -> None` to accept `MoodleClient` directly without `request_delay`.
- **BREAKING**: Add `SharepointClient.download_file(self, file: File, dest: BinaryIO) -> None`, `fetch_folder_by_guest_url(self, url: str) -> Folder`, `fetch_file_by_guest_url(self, url: str) -> File`, `fetch_file_by_id(self, url: str, file_id: str) -> File`, `fetch_folder_files(self, folder: Folder) -> list[File]`, and `fetch_subfolders(self, folder: Folder) -> list[Folder]`, each executing the SDK query and sleeping `self.request_delay`.
- **BREAKING**: Change `SharepointDownloader.__init__(self, client: SharepointClient) -> None` and `SharepointApiAdapter.__init__(self, client: SharepointClient, semester_start: date, sharepoint_host: str, sharepoint_personal_host: str, recordings_folder_name: str, week_folder_prefix: str) -> None` to drop `request_delay` and delegate all SDK queries to `SharepointClient`.
- Update `bootstrap.create_sync_service` to inject `moodle_client` into `MoodleDownloader` and `sharepoint_raw_client` into `SharepointDownloader`, omitting `request_delay` from `SharepointApiAdapter`, `MoodleDownloader`, and `SharepointDownloader`.

## Non-Goals

- Modifying `RecordingDownloader` (`yt_dlp.YoutubeDL`) or `StringDownloader`.
- Changing `SharepointContent.file: Any`, `domain/` models, `application/` ports, `GLOSSARY.md`, or `docs/adr/`.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `course-synchronization`: Update requirements for `SharepointApiAdapter`, `MoodleDownloader`, `SharepointDownloader`, `MoodleClient`, `SharepointClient`, and `bootstrap.create_sync_service` so `MoodleClient` and `SharepointClient` are the sole owners of HTTP/SDK transport and `request_delay` throttling.

## Applicable Standards

- `backend/client-adapter-acl`
- `global/layer-boundaries`
- `testing/composition-and-seams`

## Impact

- **Touched modules**: `src/idat_sync/infrastructure/moodle_client.py`, `src/idat_sync/infrastructure/moodle_adapter.py`, `src/idat_sync/infrastructure/sharepoint_client.py`, `src/idat_sync/infrastructure/sharepoint_adapter.py`, `src/idat_sync/infrastructure/downloaders.py`, `src/idat_sync/bootstrap.py`.
- **Public test seams**: `tests/test_moodle_client.py`, `tests/test_moodle_adapter.py`, `tests/test_sharepoint_adapter.py`, `tests/test_sync_pipeline.py`.
