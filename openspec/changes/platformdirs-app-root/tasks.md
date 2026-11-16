# Tasks

## 1. Root-Based `load_config` in `config.py`

- [x] 1.1 Update and add failing tests in `tests/test_config.py` at the `idat_sync.config.load_config` seam verifying `load_config(root: str | Path = ".")` defaults to `"."` (`load_config() == SyncConfig()`), falls back cleanly to `Path(root) / "session.json"` and `Path(root) / "log.err"` when `root` or `Path(root) / "config.json"` does not exist, preserves `IDAT_SYNC_* > JSON > root` precedence and `IDAT_SYNC_CONFIG` explicit `FileNotFoundError`, and raises `ValueError` on blank `root`, an existing non-directory file passed as `root`, unknown keys, or invalid types; implement `load_config(root: str | Path = ".")` in `src/idat_sync/config.py`, and verify `python -m unittest tests/test_config.py -v` passes.

## 2. Parent Directory Creation in `bootstrap.configure_logging`

- [x] 2.1 Add a failing test in `tests/test_sync_pipeline.py` at the `bootstrap.configure_logging` seam verifying nested non-existent `log_file.parent` directories are created before attaching `logging.FileHandler`; add `log_file.parent.mkdir(parents=True, exist_ok=True)` to `configure_logging` in `src/idat_sync/bootstrap.py`, and verify `python -m unittest tests/test_sync_pipeline.py -v` passes.

## 3. `platformdirs` Dependency, `ui.app.main` Wiring, and ADR

- [x] 3.1 Declare `"platformdirs >= 2.1"` in `pyproject.toml` (and install dependencies in the local Python environment); add a failing test in `tests/test_auth.py` at the `ui.app.main` seam verifying `ui.app.main()` invokes `load_config(user_data_path("idat-sync", appauthor=False))` (without creating the OS directory inside `user_data_path`), passes `config.log_file` to `configure_logging`, and runs `MainApp(config=config)`, update `src/idat_sync/ui/app.py`, and verify `python -m unittest tests/test_auth.py -v` passes.
- [x] 3.2 Write `docs/adr/0005-platformdirs-gui-root-and-file-credentials.md` recording the decision to pass `user_data_path("idat-sync", appauthor=False)` as `root` in `ui.app.main()` with `0o600` JSON `Credentials` (over macOS Keychain) while keeping `./` defaults in `SyncConfig` / `load_config()` for dev `scripts/` and tests, and verify the ADR matches the concise repo format in `docs/adr/`.

## 4. Final Verification

- [x] 4.1 Run the full test suite (`python -m unittest discover -s tests -v`), AST architecture checks (`python -m unittest tests/test_architecture.py -v`), `openspec validate platformdirs-app-root --strict`, and any available typecheck and lint checks, and verify all pass.
