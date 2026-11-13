# Proposal

## Why

Volatile runtime values (`semester_start = date(2025, 9, 22)`, `REQUEST_DELAY = 30`, `./session.json`, `./log.err`) and scattered tenant/folder constants (`idat628.sharepoint.com`, `idat628-my.sharepoint.com`, `MOODLE_IDAT`, `"Grabaciones"`, `"Semana"`) are hardcoded across `application/`, `infrastructure/`, `ui/`, and `utils.py`. Centralizing them into a validated `SyncConfig` with optional JSON and environment-variable overrides lets users update academic cycle dates, throttling delays, or tenant endpoints without editing source code while eliminating global state imports in `infrastructure/`.

## What Changes

- Introduce a frozen `SyncConfig` dataclass and `load_config(config_path: str | Path | None = None) -> SyncConfig` in `src/idat_sync/config.py` using standard-library `json`, `os.environ`, `urllib.parse`, and `pathlib`.
- Support runtime override precedence: explicit `SyncConfig` argument > `IDAT_SYNC_<UPPER_FIELD_NAME>` environment variables > `./config.json` (or `IDAT_SYNC_CONFIG` path) > `SyncConfig` defaults.
- Validate and normalize URLs, bare hostnames, single-segment folder names, ISO dates, and non-negative numeric delays on `SyncConfig` construction and loading.
- Inject configuration parameters through `bootstrap.create_sync_service(credentials: Credentials, config: SyncConfig) -> IDATSync` into `IDATSync`, `MoodleClient`, `MoodleAdapter`, `SharepointClient`, `SharepointApiAdapter`, `MoodleDownloader`, `SharepointDownloader`, and `RecordingDownloader` without hardcoded fallback defaults.
- Wire `SyncConfig` into `ui/app.py` (`main`, `MainApp`), `ui/sync_frame.py` (`SyncFrame`), `infrastructure/auth.py` (`AuthProvider`), and `utils.py` (`get_sharepoint_cookies`), removing `MOODLE_IDAT`, `REQUEST_DELAY`, and `save_content` from `idat_sync.utils`.

## Non-Goals

- Modifying HTTP/Playwright timeouts, pagination/search limits (`row_limit=15`, `page_size=50`, `limit=100`), download chunk sizes, or `safe_filename` `max_len=100`.
- Changing vendor protocol invariants (Moodle WS function names, `RQ_HEADER`, Microsoft login DOM selectors, cookie names, `-resumen.html` / `-intro.html` suffixes, `WINDOWS_RESERVED`).
- Adding new GUI controls or settings dialogs to `SyncFrame` or `LoginFrame`.
- Changing `GLOSSARY.md` or adding a new ADR beyond `docs/adr/0001-hexagonal-ports-and-adapters.md`.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `course-synchronization`: Add requirements for `SyncConfig` validation and `load_config` precedence (`IDAT_SYNC_*` env vars > `config.json` > defaults), update composition root and adapter/client/downloader requirements to inject configuration parameters without importing `MOODLE_IDAT` or `REQUEST_DELAY` from `idat_sync.utils`, and verify strict inward layer boundaries.

## Impact

- **Modules touched**:
  - `src/idat_sync/config.py` (new)
  - `src/idat_sync/bootstrap.py`
  - `src/idat_sync/application/sync_service.py`
  - `src/idat_sync/infrastructure/auth.py`
  - `src/idat_sync/infrastructure/moodle_client.py`
  - `src/idat_sync/infrastructure/moodle_adapter.py`
  - `src/idat_sync/infrastructure/sharepoint_client.py`
  - `src/idat_sync/infrastructure/sharepoint_adapter.py`
  - `src/idat_sync/infrastructure/downloaders.py`
  - `src/idat_sync/ui/app.py`
  - `src/idat_sync/ui/sync_frame.py`
  - `src/idat_sync/utils.py`
- **Public test seams**:
  - `idat_sync.config` (`SyncConfig`, `load_config`)
  - `idat_sync.bootstrap.create_sync_service`
  - `MoodleClient` & `MoodleAdapter`
  - `SharepointClient` & `SharepointApiAdapter`
  - `MoodleDownloader`, `SharepointDownloader`, `RecordingDownloader`, & `IDATSync`
  - Architecture boundary AST seam
- **Applicable `agent-os` standards**:
  - `global/layer-boundaries`
  - `backend/client-adapter-acl`
  - `backend/content-strategy-dispatch`
  - `testing/composition-and-seams`
