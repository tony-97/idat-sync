# Composition Root & Testable Seams

Wire the production object graph solely in `bootstrap.py` (`create_sync_service`). All application, domain, and adapter classes must receive collaborators via constructor injection.

```python
# Production wiring in bootstrap.py
def create_sync_service(credentials: Credentials, config: SyncConfig) -> IDATSync:
    moodle_client = MoodleClient(credentials.get("token"), base_url=config.moodle_base_url, request_delay=config.request_delay)
    moodle_adapter = MoodleAdapter(moodle_client, moodle_base_url=config.moodle_base_url)
    ...
    return IDATSync(course_repository, download_manager, task_orchestrator, file_system, recordings_folder_name=config.recordings_folder_name)

# Seam testing with spec-constrained mocks + real domain objects
mock_gw = MagicMock(spec=CourseGateway)
repo = CourseRepository(course_gateway=mock_gw, recording_gateway=mock_rec, url_strategies=[mock_strat])
```

- **Single composition root**: Only `create_sync_service(credentials: Credentials, config: SyncConfig) -> IDATSync` in `bootstrap.py` instantiates concrete clients, adapters, filesystem adapter, and downloader groups from `Credentials` and `SyncConfig`.
- **Pure constructors**: `CourseRepository`, `DownloadManager`, `TaskOrchestrator`, `IDATSync`, and `*Adapter` classes must only store injected dependencies in `__init__` — never instantiate clients from raw tokens/cookies or perform network/disk I/O in `__init__`.
- **Confirmed test seams**:
  - `idat_sync.config` (`SyncConfig`, `load_config`): unit-tested directly against normalization, validation, and precedence rules.
  - `bootstrap.create_sync_service`: tested by calling the real factory with `SyncConfig` while patching only external HTTP/SDK transport (`ClientContext`).
  - `*Client` tests: patch HTTP/SDK transport (`requests.post`, `ClientContext`, `time.sleep`).
  - `*Adapter` tests: inject `MagicMock(spec=*Client)` and assert outputs are `domain/` dataclasses (`Course`, `CourseSection`, `CourseModule`, `RecordingContent`), never raw dicts or SDK objects.
  - Domain seam: `SyncDiff.from_trees`, `safe_filename`, `Content.in_folder` tested purely in memory without disk I/O.
  - Application orchestration seam: `CourseRepository`, `DownloadManager`, `TaskOrchestrator`, `IDATSync` tested with mock ports and stubs.
  - Architecture boundary seam: AST inspection verifying zero imports from `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `office365`, `yt_dlp`, or `playwright` across `domain/` and `application/`.
