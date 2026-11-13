# Composition Root & Testable Seams

Wire the production object graph solely in `bootstrap.py` (`create_sync_service`, `create_auth_service`). All application, domain, and adapter classes must receive collaborators via constructor injection.

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

- **Single composition root**: Only `create_sync_service(credentials: Credentials, config: SyncConfig) -> IDATSync` and `create_auth_service(config: SyncConfig) -> AuthService` in `bootstrap.py` instantiate concrete clients, adapters, credential stores, filesystem adapters, and downloader groups from `Credentials` and `SyncConfig`.
- **Pure constructors**: `AuthService`, `CourseRepository`, `DownloadManager`, `TaskOrchestrator`, `IDATSync`, and `*Adapter` classes must only store injected dependencies in `__init__` — never instantiate clients from raw tokens/cookies or perform network/disk I/O in `__init__`.
- **Confirmed test seams**:
  - `idat_sync.config` (`SyncConfig`, `load_config`): unit-tested directly against normalization, validation, and precedence rules.
  - `bootstrap.create_sync_service`: tested by calling the real factory with `SyncConfig` while patching only external HTTP/SDK transport (`ClientContext`).
  - `bootstrap.create_auth_service`: tested by calling the real factory with `SyncConfig` and verifying wired `MoodleTokenAdapter` (wrapping `MoodleTokenClient`), `SharepointBrowserAuth`, and `JsonCredentialStore` attributes.
  - `*Client` tests (`MoodleClient`, `MoodleTokenClient`, `SharepointClient`): patch HTTP/SDK transport (`requests.post`, `ClientContext`, `time.sleep`).
  - `SharepointBrowserAuth` tests: patch external Playwright entrypoint (`idat_sync.infrastructure.auth.async_playwright`, plus `asyncio.sleep`).
  - `JsonCredentialStore` tests: tested against real temporary files (`tempfile.TemporaryDirectory()` and unreadable-file `PermissionError`).
  - `*Adapter` tests (`MoodleAdapter`, `MoodleTokenAdapter`, `SharepointApiAdapter`): inject `MagicMock(spec=*Client)` and assert outputs are `domain/` dataclasses (`Course`, `CourseSection`, `CourseModule`, `RecordingContent`) or port return values, never raw dicts or SDK objects.
  - Domain seam: `SyncDiff.from_trees`, `safe_filename`, `Content.in_folder` tested purely in memory without disk I/O.
  - Application orchestration seam: `AuthService`, `CourseRepository`, `DownloadManager`, `TaskOrchestrator`, `IDATSync` tested with fake ports and stubs.
  - Architecture boundary seam: AST inspection verifying zero forbidden imports in `domain/` and `application/`, zero `idat_sync.infrastructure` imports in `ui/`, zero `idat_sync.utils` imports (`src/idat_sync/utils.py` removed), and `playwright` isolated to `infrastructure/auth.py`.
