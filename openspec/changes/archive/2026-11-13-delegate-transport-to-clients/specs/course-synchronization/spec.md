# Spec Delta

## ADDED Requirements

### Requirement: Client-owned file download streaming and SharePoint SDK query execution
`MoodleClient` and `SharepointClient` SHALL provide `download_file(..., dest: BinaryIO) -> None` to stream remote files into an open binary stream and sleep `self.request_delay`. `MoodleDownloader` and `SharepointDownloader` SHALL accept their `*Client`, create parent directories, open `content.path` as `"wb"`, and delegate to `client.download_file`. `SharepointClient` SHALL execute all guest URL, file-by-ID, and folder SDK queries. Verified at client, adapter, and downloader seams.

#### Scenario: Streaming a Moodle file through MoodleClient
- **WHEN** `MoodleClient.download_file(url, dest)` is called with a target URL and writable binary stream
- **THEN** it performs `requests.get(url, params={"wstoken": self.token, "token": self.token}, stream=True, allow_redirects=True, timeout=60, headers=RQ_HEADER)`, raises `RuntimeError("403 Forbidden: your session cookie is missing/expired.")` on HTTP `403`, calls `raise_for_status()`, writes non-empty `1024 * 256`-byte chunks into `dest`, and calls `time.sleep(self.request_delay)`

#### Scenario: Delegating MoodleDownloader and SharepointDownloader file downloads to clients
- **WHEN** `MoodleDownloader(moodle_client).download(moodle_content)` and `SharepointDownloader(sharepoint_client).download(sp_content)` are called
- **THEN** each stores `self.moodle_client` or `self.sharepoint_client` without accepting `request_delay`, creates `Path(content.path).parent`, opens `content.path` as `"wb"`, and calls `moodle_client.download_file(moodle_content.url, f)` and `sharepoint_client.download_file(sp_content.file, local_file)` respectively (where `SharepointClient.download_file` runs `file.download_session(dest).execute_query_with_incremental_retry()` and sleeps `self.request_delay`)

#### Scenario: Delegating SharePoint guest URLs, direct links, and folder traversal to SharepointClient
- **WHEN** `SharepointApiAdapter.list_url_content`, `fetch_folder_contents`, and `fetch_file_from_direct_link` resolve guest folder URLs, guest file URLs, direct links with `uniqueid`/`sourcedoc`, and folder hierarchies
- **THEN** `SharepointApiAdapter` calls `sp_client.fetch_folder_by_guest_url`, `sp_client.fetch_file_by_guest_url`, `sp_client.fetch_file_by_id(link, file_id)` (skipping the client call and returning `None` when neither `uniqueid` nor `sourcedoc` is present), and calls `sp_client.fetch_folder_files(folder)` before `sp_client.fetch_subfolders(folder)`, while `SharepointClient` executes all `ClientContext` / `execute_query_retry` calls and `time.sleep(self.request_delay)`

## MODIFIED Requirements

### Requirement: SharePoint adapter projects recordings into weekly domain content
`SharepointApiAdapter` SHALL accept `(client: SharepointClient, semester_start: date, sharepoint_host: str, sharepoint_personal_host: str, recordings_folder_name: str, week_folder_prefix: str)` without `request_delay`, without hardcoded defaults, and without instantiating `SharepointClient` in `__init__`. `SharepointApiAdapter` MUST NOT store `request_delay`, `properties_to_retrieve`, `client`, or `search_client`, and MUST NOT import `time` or `ClientContext`. `fetch_recordings(course_name)` SHALL compute `week = 1 + (file.time_created.date() - self.semester_start).days // 7` and return `list[RecordingContent]`. Verified at `SharepointApiAdapter` seam.

#### Scenario: Computing weekly folder and SharePoint stream URL per recording
- **WHEN** `SharepointApiAdapter.fetch_recordings(course_name)` finds recording files with `time_created` and `serverRelativeUrl`
- **THEN** it returns `RecordingContent(Path(f"{self.week_folder_prefix} {week}") / name, last_mod, f"https://{self.sharepoint_personal_host}/:v:/r{quote(file.serverRelativeUrl)}?csf=1&web=1")` for each recording inside `self.recordings_folder_name` (matched case-insensitively)

#### Scenario: Skipping recordings missing creation timestamp without shifting subsequent weeks
- **WHEN** `SharepointApiAdapter.fetch_recordings(course_name)` encounters a recording file with `time_created = None` before a valid recording file
- **THEN** the file missing `time_created` is excluded and the subsequent file's `week` is still computed from its own `time_created.date()`

#### Scenario: Customizing SharePoint hosts, recordings folder name, and week prefix
- **WHEN** `SharepointApiAdapter` is initialized with custom `sharepoint_host`, `sharepoint_personal_host`, `recordings_folder_name="Videos"`, and `week_folder_prefix="Week"`
- **THEN** `can_handle` matches `sharepoint_host`, and `fetch_recordings` matches parent folders named `"videos"` and returns `RecordingContent` paths under `Path(f"Week {week}")` with stream URLs on `sharepoint_personal_host`

### Requirement: Composition root wires production dependencies outside application
`create_sync_service(credentials: Credentials, config: SyncConfig) -> IDATSync` in `src/idat_sync/bootstrap.py` SHALL require an explicit `SyncConfig`, construct all clients, adapters, `LocalFileSystemAdapter`, and `Downloader` groups with `config` values, wire `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)` directly to the low-level clients without `request_delay`, and inject the composed collaborators plus `recordings_folder_name` into `IDATSync`. `IDATSync.__init__` MUST NOT instantiate clients or adapters from `Credentials`. Verified at `bootstrap.create_sync_service` and `IDATSync` seams.

#### Scenario: Constructing IDATSync with injected collaborators
- **WHEN** `IDATSync(course_repository, download_manager, task_orchestrator, file_system, recordings_folder_name="Videos")` is instantiated and `sync_course(course, course_dir)` runs
- **THEN** it stores the injected collaborators without network or credential initialization in `__init__` and ensures and syncs recordings under `Path(course_dir) / "Videos"`

#### Scenario: Wiring SyncConfig through create_sync_service
- **WHEN** `create_sync_service(credentials, config=custom_config)` is called with a `SyncConfig` instance
- **THEN** it forwards `custom_config` fields to `MoodleClient`, `MoodleAdapter`, `SharepointClient`, `SharepointApiAdapter` (without `request_delay`), `RecordingDownloader`, and `IDATSync`, and wires `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)` with `moodle_dl.moodle_client is moodle_adapter.client` and `sp_dl.sharepoint_client is sp_adapter.sp_client`

### Requirement: Sealed Anti-Corruption Layer on Moodle adapter
`MoodleAdapter` public methods SHALL return only `domain/` models (`Course`, `CourseSection`, `CourseModule`, `ModuleContent`, `Content`), and `MoodleAdapter` MUST NOT define or expose a `token` property (`hasattr(MoodleAdapter, "token") == False`). Any helper that retrieves or processes raw vendor structures (`dict`, raw JSON payloads) without converting to domain entities SHALL be private. Verified at the `MoodleAdapter` public seam.

#### Scenario: Inspecting MoodleAdapter public method return types
- **WHEN** all public methods and attributes of `MoodleAdapter` are invoked or inspected
- **THEN** none of them return raw vendor dictionaries or SDK objects, and `MoodleAdapter` does not expose `token`

### Requirement: Configurable tenant endpoints and throttling across clients, adapters, and downloaders
`MoodleClient`, `MoodleAdapter`, `SharepointClient`, `RecordingDownloader`, `AuthProvider`, and `get_sharepoint_cookies` SHALL require injected tenant URL/host parameters without hardcoded defaults, and `MoodleClient` and `SharepointClient` SHALL require `request_delay` without hardcoded defaults as the sole owners of `time.sleep(self.request_delay)`. `MoodleDownloader`, `SharepointDownloader`, and `SharepointApiAdapter` MUST NOT accept `request_delay`. `src/idat_sync/infrastructure/downloaders.py` MUST NOT import `requests`, `RQ_HEADER`, or `time`, and `MOODLE_IDAT`, `REQUEST_DELAY`, and `save_content` MUST NOT exist in `idat_sync.utils`. Verified at client, adapter, downloader, and AST seams.

#### Scenario: Respecting injected Moodle and SharePoint hosts and request delays
- **WHEN** `MoodleClient`, `MoodleAdapter`, `SharepointClient`, and `RecordingDownloader` are constructed with custom `base_url`, `moodle_base_url`, `sharepoint_personal_host`, and `request_delay`
- **THEN** `MoodleAdapter.can_handle` matches the hostname of `moodle_base_url`, `RecordingDownloader.can_handle` matches `f"{sharepoint_personal_host}/:v:"`, and `MoodleClient.call_ws`, `MoodleClient.download_file`, `SharepointClient.download_file`, `SharepointClient.fetch_folder_files`, and `SharepointClient.fetch_subfolders` sleep for the injected `request_delay`

#### Scenario: Inspecting infrastructure imports and utils cleanup
- **WHEN** all Python modules under `src/idat_sync/infrastructure/` and `src/idat_sync/utils.py` are inspected
- **THEN** no `infrastructure/` module imports `MOODLE_IDAT` or `REQUEST_DELAY` from `idat_sync.utils`, `downloaders.py` does not import `requests`, `RQ_HEADER`, or `time`, `sharepoint_adapter.py` does not import `time` or `ClientContext`, and `MOODLE_IDAT`, `REQUEST_DELAY`, and `save_content` are not defined in `src/idat_sync/utils.py`
