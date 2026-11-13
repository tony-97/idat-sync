# Spec Delta

## MODIFIED Requirements

### Requirement: Strict inward package dependency boundary
The `src/idat_sync/domain/` and `src/idat_sync/application/` packages SHALL have zero imports from `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `office365`, `yt_dlp`, or `playwright`. `src/idat_sync/ui/` MUST NOT import `idat_sync.infrastructure`, `src/idat_sync/infrastructure/auth.py` SHALL be the sole module importing `playwright`, `src/idat_sync/utils.py` MUST NOT exist, and `safe_filename` and `WINDOWS_RESERVED` SHALL reside in `src/idat_sync/domain/path_utils.py`. Verified at the architecture boundary and `domain.path_utils` seams.

#### Scenario: Inspecting domain and application imports
- **WHEN** all Python modules under `src/idat_sync/domain/` and `src/idat_sync/application/` are parsed for import statements
- **THEN** none of them import `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `office365`, `yt_dlp`, or `playwright`

#### Scenario: Sanitizing course filenames without importing infrastructure
- **WHEN** `safe_filename(s, replacement=" ", max_len=100)` in `src/idat_sync/domain/path_utils.py` is called with accents, forbidden characters, or Windows reserved names (`WINDOWS_RESERVED`)
- **THEN** it returns the normalized, safe ASCII filename of at most `max_len` characters

#### Scenario: Enforcing ui and playwright isolation and utils removal
- **WHEN** all Python modules under `src/idat_sync/` are parsed for imports and `src/idat_sync/utils.py` is checked
- **THEN** `src/idat_sync/utils.py` does not exist, no module imports `idat_sync.utils`, no module under `src/idat_sync/ui/` imports `idat_sync.infrastructure`, and `src/idat_sync/infrastructure/auth.py` is the only module importing `playwright`

### Requirement: Configurable tenant endpoints and throttling across clients, adapters, and downloaders
`MoodleClient`, `MoodleTokenAdapter`, `MoodleAdapter`, `SharepointClient`, `RecordingDownloader`, and `SharepointBrowserAuth` SHALL require injected tenant URL/host parameters without hardcoded defaults, and `MoodleClient` and `SharepointClient` SHALL require `request_delay` without hardcoded defaults as the sole owners of `time.sleep(self.request_delay)`. `MoodleDownloader`, `SharepointDownloader`, and `SharepointApiAdapter` MUST NOT accept `request_delay`. `src/idat_sync/infrastructure/downloaders.py` MUST NOT import `requests`, `RQ_HEADER`, or `time`, `RQ_HEADER` SHALL reside in `src/idat_sync/infrastructure/moodle_client.py`, and `src/idat_sync/utils.py` MUST NOT exist. Verified at client, adapter, downloader, and AST seams.

#### Scenario: Respecting injected Moodle and SharePoint hosts and request delays
- **WHEN** `MoodleClient`, `MoodleAdapter`, `SharepointClient`, and `RecordingDownloader` are constructed with custom `base_url`, `moodle_base_url`, `sharepoint_personal_host`, and `request_delay`
- **THEN** `MoodleAdapter.can_handle` matches the hostname of `moodle_base_url`, `RecordingDownloader.can_handle` matches `f"{sharepoint_personal_host}/:v:"`, and `MoodleClient.call_ws`, `MoodleClient.download_file`, `SharepointClient.download_file`, `SharepointClient.fetch_folder_files`, and `SharepointClient.fetch_subfolders` sleep for the injected `request_delay`

#### Scenario: Inspecting infrastructure imports and utils cleanup
- **WHEN** all Python modules under `src/idat_sync/infrastructure/` and `src/idat_sync/utils.py` are inspected
- **THEN** `src/idat_sync/utils.py` does not exist, no `infrastructure/` module imports `idat_sync.utils`, `downloaders.py` does not import `requests`, `RQ_HEADER`, or `time`, `sharepoint_adapter.py` does not import `time` or `ClientContext`, and `RQ_HEADER` is defined in `src/idat_sync/infrastructure/moodle_client.py`
