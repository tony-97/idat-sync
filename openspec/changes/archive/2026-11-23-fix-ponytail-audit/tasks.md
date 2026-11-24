# Tasks

## 1. Atomic Recording Downloads and `.part` Directory Filtering

- [x] 1.1 Update `LocalFileSystemAdapter.scan_mtimes` in `src/idat_sync/infrastructure/filesystem.py` to skip any file whose filename or relative ancestor directory ends with `".part"` (`not any(part.endswith(".part") for part in rel.parts)`). Write a failing test first at the `LocalFileSystemAdapter` seam in `tests/test_sync_pipeline.py` covering a file nested inside `<name>.part/`, implement the minimal change, and verify with `python -m unittest tests.test_sync_pipeline`.
- [x] 1.2 Update `RecordingDownloader._download_sync` in `src/idat_sync/infrastructure/downloaders.py` to set `"updatetime": False` and `"overwrites": True`, stage downloads in `part_dir = os_target.with_name(f"{os_target.name}.part")` at `staged_target = part_dir / os_target.name`, atomically call `staged_target.replace(os_target)` on success, and delete `part_dir` via `shutil.rmtree(part_dir, ignore_errors=True)` in `finally:`. Write failing tests first at the `RecordingDownloader` seam in `tests/test_sync_pipeline.py` verifying `ydl_opts`, atomic move for suffixed and extensionless paths, and cleanup without corrupting existing targets on error/cancellation, then verify with `python -m unittest tests.test_sync_pipeline`.

## 2. Flexible SharePoint MFA Selection

- [x] 2.1 Update `SharepointBrowserAuth._complete_login` in `src/idat_sync/infrastructure/sharepoint_auth.py` to wait for `#idTxtBx_SAOTCC_OTC, #idDiv_SAOTCS_Proofs [data-value="PhoneAppOTP"], #idDiv_SAOTCS_Proofs [data-value="OneWaySMS"]`, skip clicking any proof row when `#idTxtBx_SAOTCC_OTC` is already visible, and otherwise click `#idDiv_SAOTCS_Proofs [data-value="PhoneAppOTP"]` if visible or fall back to `#idDiv_SAOTCS_Proofs [data-value="OneWaySMS"]` without `nth-child(2)`. Write failing tests first at the `SharepointBrowserAuth` seam in `tests/test_sharepoint_auth.py` covering direct OTP entry, `PhoneAppOTP` preferred over `OneWaySMS`, and `OneWaySMS`-only fallback, then verify with `python -m unittest tests.test_sharepoint_auth`.

## 3. Remove Single-Caller Wrappers

- [x] 3.1 Inline `moodle_username = username.split("@", 1)[0]` once inside `AuthService.login` in `src/idat_sync/application/auth_service.py`, call `self.moodle_auth.fetch_token(moodle_username, password)` directly, reuse `moodle_username` in `_logger.info`, and remove `AuthService.authenticate`. Update tests at the `AuthService` seam in `tests/test_auth.py` (asserting `not hasattr(AuthService, "authenticate")` and verifying domain stripping in `login`), then verify with `python -m unittest tests.test_auth`.
- [x] 3.2 Remove `TaskOrchestrator` and `_default_run_task` from `src/idat_sync/application/task_orchestrator.py`, make `run_task` required on `TaskChannel.create_workers`, replace `task_orchestrator` with `downloader_groups` in `IDATSync.__init__` / `_CourseStreamRun.__init__` (`src/idat_sync/application/sync_service.py`) and `bootstrap.create_sync_service` (`src/idat_sync/bootstrap.py`). Update tests at the `TaskChannel`, `IDATSync`, and `bootstrap.create_sync_service` seams in `tests/test_sync_pipeline.py` (asserting removed symbols and `TypeError` on missing `run_task`), then verify with `python -m unittest tests.test_sync_pipeline tests.test_architecture`.

## 4. Trim Eager Package `__init__.py` Re-exports

- [x] 4.1 Add an AST test at the architecture boundary seam in `tests/test_architecture.py` asserting that `src/idat_sync/application/__init__.py`, `src/idat_sync/domain/__init__.py`, and `src/idat_sync/infrastructure/__init__.py` contain zero import statements, strip imports and `__all__` from all three `__init__.py` files leaving only module docstrings, update any `domain_mod` assertion in `tests/test_sync_pipeline.py`, and verify with `python -m unittest tests.test_architecture tests.test_sync_pipeline`.

## 5. Colab Notebook and CI Quality Gate

- [x] 5.1 Update `colab.ipynb` cell 1 to move `weasyprint` into `%pip install -U` and change `apt-get install xattr` to `!apt-get install -y xattr`, and update cell 3 to check `os.path.getmtime(path) <= os.path.getmtime(mp3_path)` before skipping `.mp3` conversion and split conversions (pass 1) from deduplicated Drive link collection skipping `.html` and `video/*` sources (pass 2). Add a test verifying `colab.ipynb` cell 1 commands and cell 3 two-pass conversion/deduplication behavior, and run it with `python -m unittest`.
- [x] 5.2 Update `.github/workflows/build_and_deploy.yml` to trigger on `push` (`branches: [main]`, `tags: ["v*"]`), `pull_request` (`branches: [main]`), and `workflow_dispatch`, add the `ubuntu-latest` `check` job running `python -m unittest`, `ruff check .`, and `pylint src tests`, and gate both `build` and `build-android` with `needs: check` and `if: github.ref_type == 'tag' || github.event_name == 'workflow_dispatch'`. Verify workflow triggers, job dependencies, and commands via a unit test or YAML parse check.

## 6. Stdlib `unittest` Migration and Full Verification

- [x] 6.1 Add an AST test in `tests/test_architecture.py` asserting zero `pytest` imports across `tests/*.py`, replace all `pytest.raises` calls with `self.assertRaises` / `self.assertRaisesRegex` and remove `import pytest` across all 9 test files, and verify with `python -m unittest`.
- [x] 6.2 Run the full verification suite (`python -m unittest`, `ruff check .`, and `pylint src tests`) and confirm all tests pass and both linters pass cleanly (Pylint `10.0/10.0`).

## Workflow follow-up

- Commit the changes and run `/code-review` in a fresh session before archiving.
- Run `/opsx-archive` after code review and full test verification pass.
