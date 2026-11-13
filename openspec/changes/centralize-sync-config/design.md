# Design

## Context

See `proposal.md` — Why and `specs/course-synchronization/spec.md` for behavioral requirements.

In accordance with [ADR 0001: Hexagonal Ports and Adapters Architecture](../../../docs/adr/0001-hexagonal-ports-and-adapters.md), `domain/` and `application/` must remain completely isolated from `infrastructure/`, `ui/`, and filesystem/environment I/O. Currently, infrastructure clients and adapters import `MOODLE_IDAT` and `REQUEST_DELAY` directly from `src/idat_sync/utils.py`, while `SharepointClient`, `SharepointApiAdapter`, `RecordingDownloader`, `AuthProvider`, and `IDATSync` embed raw tenant hostnames (`idat628.sharepoint.com`, `idat628-my.sharepoint.com`), `semester_start = date(2025, 9, 22)`, and folder names (`"Grabaciones"`, `"Semana"`).

## Goals / Non-Goals

**Goals:**
- Provide a single, immutable `SyncConfig` dataclass in `src/idat_sync/config.py` with standard-library validation (`urllib.parse`, `pathlib`, `datetime`, `math`) and zero external dependencies.
- Load runtime overrides deterministically via `load_config(config_path: str | Path | None = None) -> SyncConfig` (`IDAT_SYNC_<UPPER_FIELD_NAME>` env vars > JSON file > `SyncConfig` defaults).
- Inject configuration values into `IDATSync`, clients, adapters, and downloaders via `bootstrap.create_sync_service(credentials, config)` while keeping `domain/` and `application/` free of `idat_sync.config`, `os`, and `json` imports.

**Non-Goals:**
- Exposing configuration fields in the Tkinter UI (`SyncFrame` / `LoginFrame`).
- Externalizing HTTP/Playwright timeouts, pagination/search limits, download chunk sizes, `max_len`, or vendor protocol invariants (`RQ_HEADER`, Moodle WS names, MS login DOM selectors, `-resumen.html` / `-intro.html`, `WINDOWS_RESERVED`).

## Decisions

1. **Top-level `src/idat_sync/config.py` with required primitive constructor injection into layers**
   - *Decision*: Place `SyncConfig` and `load_config` in `src/idat_sync/config.py` alongside `bootstrap.py`. Keep `SyncConfig` as the single source of truth for default values: require explicit primitive/domain parameters (`str`, `float`, `date`, `Path`) without duplicated defaults in `IDATSync` (`recordings_folder_name: str`) and `infrastructure/` constructors, wired by `bootstrap.create_sync_service(credentials: Credentials, config: SyncConfig) -> IDATSync` and `ui/app.py` (`main()` calls `load_config()` once and passes `config: SyncConfig` into `MainApp` and `SyncFrame`).
   - *Rationale*: Preserves [ADR 0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) inward dependency rules (`application/` never imports `config.py`, `os`, or `json`) while eliminating duplicate default values across constructors and globals.
   - *Alternatives considered*: Duplicating default values in every constructor or making `config: SyncConfig | None = None` optional everywhere (rejected: spreads fallback values across multiple files instead of centralizing defaults in `SyncConfig`).

2. **Standard-library URL, hostname, and path validation in `SyncConfig.__post_init__`**
   - *Decision*: Use `urllib.parse.urlsplit` / `urlunsplit` and `pathlib.PurePath` / `Path` inside `SyncConfig.__post_init__`:
     - **URL fields (`moodle_base_url`, `sharepoint_academic_site_url`)**: Parse with `parts = urlsplit(val.strip())`, require `parts.scheme in {"http", "https"}` and `bool(parts.hostname)`, and normalize via `urlunsplit(parts._replace(path=parts.path.rstrip("/"), query="", fragment=""))`.
     - **Host fields (`sharepoint_host`, `sharepoint_personal_host`, `email_domain`)**: Parse with `parts = urlsplit(f"//{val.strip()}")` and require `parts.hostname == val.strip().lower()`, `parts.port is None`, and `not parts.path` (plus `@` not in `val`).
     - **Single-segment folder fields (`recordings_folder_name`, `week_folder_prefix`)**: Require `val.strip()` non-empty, `val.strip() not in {".", ".."}`, and `PurePath(val.strip()).name == val.strip()` with no path separators.
     - **Delay & paths (`request_delay`, `session_file`, `log_file`)**: Reject `isinstance(request_delay, bool)`, non-numeric, `math.isnan`, `math.isinf`, or `< 0`; coerce `session_file` and `log_file` to `Path` after verifying non-empty path strings.
   - *Rationale*: Fails fast on misconfiguration using Python's standard library rather ad-hoc regexes or silent string concatenation bugs.

3. **Explicit vs. default config file handling in `load_config`**
   - *Decision*: Resolve path from `config_path`, else `os.environ.get("IDAT_SYNC_CONFIG")`, else `Path("./config.json")`. If neither `config_path` nor `IDAT_SYNC_CONFIG` was provided and `./config.json` does not exist, start from `SyncConfig` defaults; if an explicit path is missing, raise `FileNotFoundError`. Reject non-dict JSON roots and unknown JSON keys with `ValueError`.
   - *Rationale*: Zero-config startup works out of the box while explicit user config paths or typos in `config.json` fail immediately and visibly.

4. **Modules Touched & Public Test Seams**
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
     1. `idat_sync.config` (`SyncConfig`, `load_config`)
     2. `idat_sync.bootstrap.create_sync_service`
     3. `MoodleClient` & `MoodleAdapter`
     4. `SharepointClient` & `SharepointApiAdapter`
     5. `MoodleDownloader`, `SharepointDownloader`, `RecordingDownloader`, & `IDATSync`
     6. Architecture boundary AST seam (`domain/` and `application/` import purity + no `infrastructure/` imports of `MOODLE_IDAT` or `REQUEST_DELAY` from `idat_sync.utils`).

## Risks / Trade-offs

- **[Risk] Boolean values in JSON (`true`/`false`) pass `isinstance(x, int)` in Python** → Mitigation: Explicitly check `isinstance(val, bool)` before `isinstance(val, (int, float))` in both `load_config` and `SyncConfig.__post_init__`.
- **[Risk] Legacy constants in `idat_sync.utils` drifting from `SyncConfig`** → Mitigation: Remove `MOODLE_IDAT`, `REQUEST_DELAY`, and dead `save_content` from `idat_sync.utils` and enforce via AST tests that they are absent from `utils.py` and never imported in `infrastructure/`.
