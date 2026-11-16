# Proposal

## Why

On macOS, double-clicking `idat_sync.app` via LaunchServices starts the process with `/` as the current working directory (on the read-only Signed System Volume) while `.app` bundle contents are read-only and code-signed. Because `load_config()` and `SyncConfig` currently resolve `./config.json`, `./session.json`, and `./log.err` relative to CWD, the packaged GUI app crashes opening `log.err` at startup or fails to persist `Credentials` via `JsonCredentialStore`.

## What Changes

- **BREAKING**: Replace `load_config(config_path: str | Path | None = None)` in `src/idat_sync/config.py` with `load_config(root: str | Path = ".") -> SyncConfig`, using `root` as the base directory for optional `config.json` and default `session_file` (`<root>/session.json`) and `log_file` (`<root>/log.err`) while retaining `IDAT_SYNC_CONFIG` as an explicit JSON override and `IDAT_SYNC_* > JSON > root defaults` precedence.
- Update `ui.app.main()` in `src/idat_sync/ui/app.py` to resolve `user_data_path("idat-sync", appauthor=False)` from `platformdirs` and pass it as `root` to `load_config(...)`, keeping `SyncConfig()` and bare `load_config()` at `./` for dev `scripts/` and `tests/test_real_data.py`.
- Update `configure_logging(log_file: Path)` in `src/idat_sync/bootstrap.py` to create `log_file.parent` (`mkdir(parents=True, exist_ok=True)`) before opening `logging.FileHandler`.
- Add `"platformdirs >= 2.1"` to `pyproject.toml` dependencies.
- Record the `platformdirs` root + `0o600` JSON `Credentials` trade-off in a brief ADR (`docs/adr/0005-platformdirs-gui-root-and-file-credentials.md`).

## Non-Goals

- Changing `SyncConfig` defaults (`./session.json`, `./log.err`), `scripts/login_cli.py`, `scripts/moodle_json.py`, or `tests/test_real_data.py` away from `./` for local development.
- Migrating existing `./session.json` or `./config.json` files automatically from CWD into OS user directories.
- Switching `JsonCredentialStore` (`0o600` JSON file) to `keyring` or macOS Keychain, or modifying `GLOSSARY.md`.
- Adding `~` expansion or relative-path anchoring inside `_normalize_path`.

## Applicable Standards

- `global/layer-boundaries`
- `testing/composition-and-seams`

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `course-synchronization`: Update `load_config(root: str | Path = ".")` signature, precedence, and validation; ensure `configure_logging` creates `log_file.parent`; and pass `user_data_path("idat-sync", appauthor=False)` from `ui.app.main()` with `"platformdirs >= 2.1"` declared in `pyproject.toml`.

## Impact

- `pyproject.toml`
- `src/idat_sync/config.py`
- `src/idat_sync/ui/app.py`
- `src/idat_sync/bootstrap.py`
- `tests/test_config.py`, `tests/test_auth.py`, `tests/test_sync_pipeline.py`
- `docs/adr/0005-platformdirs-gui-root-and-file-credentials.md`
