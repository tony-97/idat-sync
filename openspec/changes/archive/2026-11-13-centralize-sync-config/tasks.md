# Tasks

## 1. Configuration Schema and Runtime Loader Seam

- [x] 1.1 Write failing unit tests in `tests/test_config.py` for `SyncConfig` defaults, URL/host/folder normalization (`urllib.parse.urlsplit`/`urlunsplit`, `pathlib.PurePath`), and `ValueError` validation (malformed URLs/hosts, multi-segment folder names, negative/non-finite/boolean `request_delay`), then implement `SyncConfig` in `src/idat_sync/config.py` and verify `tests/test_config.py` passes.
- [x] 1.2 Write failing unit tests in `tests/test_config.py` for `load_config` precedence (`IDAT_SYNC_<UPPER_FIELD_NAME>` env vars > `config.json` / `IDAT_SYNC_CONFIG` > `SyncConfig` defaults), missing default vs. explicit config file (`FileNotFoundError`), and invalid JSON keys/types (`ValueError`), then implement `load_config` in `src/idat_sync/config.py` and verify `tests/test_config.py` passes.

## 2. Moodle & SharePoint Client/Adapter Configuration Seams

- [x] 2.1 Write failing unit tests in `tests/test_moodle_client.py` and `tests/test_moodle_adapter.py` verifying injected `base_url`, `request_delay`, and `moodle_base_url` host matching in `can_handle` without importing `MOODLE_IDAT` or `REQUEST_DELAY` from `idat_sync.utils`, then update `src/idat_sync/infrastructure/moodle_client.py` and `src/idat_sync/infrastructure/moodle_adapter.py` and verify both test files pass.
- [x] 2.2 Write failing unit tests in `tests/test_sharepoint_adapter.py` verifying injected `sharepoint_host`, `sharepoint_personal_host`, `academic_site_url`, `recordings_folder_name`, `week_folder_prefix`, and `request_delay` across `SharepointClient` and `SharepointApiAdapter` without importing `REQUEST_DELAY` from `idat_sync.utils`, then update `src/idat_sync/infrastructure/sharepoint_client.py` and `src/idat_sync/infrastructure/sharepoint_adapter.py` and verify `tests/test_sharepoint_adapter.py` passes.

## 3. Downloaders, Application Service, Auth, and Composition Root Seams

- [x] 3.1 Write failing unit tests in `tests/test_sync_pipeline.py` for `MoodleDownloader` and `SharepointDownloader` (`request_delay`), `RecordingDownloader` (`sharepoint_personal_host`), and `IDATSync` (`recordings_folder_name`), then update `src/idat_sync/infrastructure/downloaders.py` and `src/idat_sync/application/sync_service.py` and verify `tests/test_sync_pipeline.py` passes.
- [x] 3.2 Write failing unit tests in `tests/test_sync_pipeline.py` for `create_sync_service(credentials, config=...)` wiring `SyncConfig` into `IDATSync`, adapters, clients, and downloaders, and for `AuthProvider` / `get_sharepoint_cookies` / `MainApp` / `SyncFrame` accepting configured paths and hosts, then update `src/idat_sync/bootstrap.py`, `src/idat_sync/infrastructure/auth.py`, `src/idat_sync/ui/app.py`, `src/idat_sync/ui/sync_frame.py`, and `src/idat_sync/utils.py` and verify `tests/test_sync_pipeline.py` passes.

## 4. Architecture Boundary Seam & Full Verification

- [x] 4.1 Extend the AST boundary test in `tests/test_sync_pipeline.py` to assert `domain/` and `application/` never import `idat_sync.config`, `infrastructure/` modules never import `MOODLE_IDAT` or `REQUEST_DELAY` from `idat_sync.utils`, and `MOODLE_IDAT`, `REQUEST_DELAY`, and `save_content` are removed from `src/idat_sync/utils.py`, then verify the test passes.
- [x] 4.2 Run the full test suite, typecheck, and lint across the repository and verify all checks pass.
