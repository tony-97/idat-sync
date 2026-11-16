# Design

## Context

See `proposal.md` for motivation and scope, `specs/course-synchronization/spec.md` for normative behavior, and [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) for package boundaries (`bootstrap.py`, `ui/`, and configuration). Because this change sets the persistent user-facing OS directory layout and keeps `./` defaults in `config.py` with `0o600` JSON `Credentials` rather than macOS Keychain, it will also be recorded in [ADR-0005](../../../docs/adr/0005-platformdirs-gui-root-and-file-credentials.md).

## Goals / Non-Goals

**Goals:**
- Route all runtime GUI files (`config.json`, `session.json`, `log.err`) into a single OS-standard user directory via `platformdirs` when launching through `ui.app.main()`.
- Keep `SyncConfig()` and bare `load_config()` defaulting to `.` so `scripts/` and `tests/test_real_data.py` continue operating on gitignored `./session.json`.
- Ensure `configure_logging` creates missing parent directories before opening `FileHandler`.

**Non-Goals:**
- Backward-compatibility support for passing a file path directly to `load_config(config_path)` (replaced by `root`, while `IDAT_SYNC_CONFIG` preserves explicit JSON file selection).
- Migrating `./` files into `platformdirs`, expanding `~`, anchoring relative JSON paths, or switching `JsonCredentialStore` to `keyring`.

## Decisions

### 1. Single `root: str | Path = "."` parameter on `load_config`
- **Choice**: Replace `config_path` with `load_config(root: str | Path = ".") -> SyncConfig`. Validate `root` via `_normalize_path(root, "root")` and reject existing non-directory files (`root_dir.exists() and not root_dir.is_dir()`) with `ValueError`. Seed fallback defaults `session_file=root_dir / "session.json"` and `log_file=root_dir / "log.err"` whenever `IDAT_SYNC_*` env vars and JSON config do not override them. Read `IDAT_SYNC_CONFIG` if set (raising `FileNotFoundError` when missing or `ValueError` when not a file), otherwise read `root_dir / "config.json"` only if it exists (`is_file()` check; non-file at `config.json` still raises `ValueError`).
- **Rationale**: One `root` directory keeps `config.json`, `session.json`, and `log.err` together, preserves `load_config() == SyncConfig()` (`Path(".") / "session.json" == Path("./session.json")`), keeps `config.py` pure stdlib, and avoids the trap where passing a default file to the old `config_path` raised `FileNotFoundError` on first launch and shadowed `IDAT_SYNC_CONFIG`.
- **Alternatives considered**:
  - Keeping `config_path` alongside separate `default_*` path overrides — rejected as unnecessary signature complexity when backward compatibility is not needed.
  - Changing `SyncConfig` defaults to `platformdirs` directly — rejected so dev `scripts/` and tests stay isolated on `./`.

### 2. Passing `user_data_path("idat-sync", appauthor=False)` from `ui.app.main()`
- **Choice**: Add `"platformdirs >= 2.1"` to `pyproject.toml`, and call `load_config(user_data_path("idat-sync", appauthor=False))` inside `ui.app.main()` before `configure_logging(config.log_file)`. Leave `ensure_exists=False` (default) in `user_data_path`.
- **Rationale**: `user_data_path` resolves to `~/Library/Application Support/idat-sync` on macOS and `%LOCALAPPDATA%\idat-sync` on Windows (omitting nested author folders via `appauthor=False`), covers both PyInstaller `.app`/`.exe` builds and `pip install` `idat_sync` runs without `sys.frozen` branching, and avoids touching real OS user directories during mocked `ui.app.main()` tests.
- **Alternatives considered**:
  - Using `keyring` / macOS Keychain — rejected because the macOS `.app` uses ad-hoc signing (`codesign -s -`), which triggers repeated Keychain ACL prompts across rebuilds, and Windows Credential Locker has tight payload limits for Playwright `storage_state` cookies.
  - Branching on `getattr(sys, "frozen", False)` — rejected so `pip`-installed GUI runs behave identically to bundled builds.

### 3. Directory creation in `bootstrap.configure_logging`
- **Choice**: Call `log_file.parent.mkdir(parents=True, exist_ok=True)` inside `configure_logging(log_file: Path)` before constructing `logging.FileHandler`.
- **Rationale**: Mirrors `JsonCredentialStore.save` (`mkdir(parents=True, exist_ok=True)`) so first launch under `~/Library/Application Support/idat-sync/log.err` or a custom `IDAT_SYNC_LOG_FILE` directory succeeds without pre-creating directories in `ui/app.py`.

### 4. Confirmed Public Test Seams
- `idat_sync.config.load_config` (`tests/test_config.py`)
- `bootstrap.configure_logging` (`tests/test_sync_pipeline.py`)
- `ui.app.main` (`tests/test_auth.py`)

## Risks / Trade-offs

- **[Risk] CLI scripts and GUI app read separate `session.json` files** → Intentional isolation: `scripts/` are dev-only (`./session.json` is gitignored); developers can cross-point with `IDAT_SYNC_SESSION_FILE=./session.json` if needed.
- **[Risk] PyInstaller bundling a new pure-Python dependency (`platformdirs`)** → `platformdirs` uses standard-library modules (`os`, `sys`, `pathlib`, Windows `ctypes`/`winreg`), and CI installs via `pip install .` before running PyInstaller.
