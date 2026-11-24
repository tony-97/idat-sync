# Proposal

## Why

The quality audit in `.scratch/ponytail-audit/audit.md` verified nine defects and simplifications still present in the codebase: `RecordingDownloader` writes directly to final paths with `yt-dlp`'s stream-header `updatetime`, risking corrupted files marked `Synced` or infinite re-syncs; `SharepointBrowserAuth` hardcodes the second MFA proof row; `colab.ipynb` hangs on interactive `apt` prompts, misses Python `weasyprint`, duplicates PDF links, and skips updated recordings; the release workflow publishes binaries without running tests or linters; tests import undeclared `pytest` only for `pytest.raises`; unused package `__init__.py` re-exports eagerly load heavy third-party libraries; and `AuthService.authenticate` and `TaskOrchestrator` add single-caller pass-through wrappers.

## What Changes

- **Atomic `RecordingContent` downloads & mtime fix**: Configure `RecordingDownloader` with `"updatetime": False` and `"overwrites": True`, download into a sibling `<name>.part/<name>` staging directory so `ffmpeg` keeps the target extension and all intermediate fragments stay isolated, atomically `replace` onto the target path on success, and remove the staging directory in `finally:`. Extend `LocalFileSystemAdapter.scan_mtimes` to exclude files inside `*.part` directories as well as `*.part` files.
- **Flexible SharePoint MFA selection**: Update `SharepointBrowserAuth._complete_login` to wait for `#idTxtBx_SAOTCC_OTC` or proof rows (`PhoneAppOTP`, `OneWaySMS`), proceeding straight to OTP entry if `#idTxtBx_SAOTCC_OTC` is visible, otherwise clicking `PhoneAppOTP` first and falling back to `OneWaySMS`.
- **Google Colab setup & post-processing**: Move `weasyprint` into `%pip install -U`, add `-y` to `apt-get install xattr`, check `mtime` before skipping `.mp3` extraction, and run conversions before a separate share-link collection pass that skips `.html` and `video/*` sources.
- **CI quality gate**: Add a `check` job (`python -m unittest`, `ruff check .`, `pylint src tests`) on `ubuntu-latest` triggered on `push`/`pull_request` to `main`, `v*` tags, and `workflow_dispatch`, gating both `build` and `build-android` via `needs: check` (builds still run only on `v*` tags or `workflow_dispatch`).
- **Drop undeclared `pytest`**: Replace all `pytest.raises` calls across `tests/` with `self.assertRaises` / `self.assertRaisesRegex` and remove `import pytest`.
- **Trim eager package `__init__.py` re-exports**: Keep only module docstrings in `application/__init__.py`, `domain/__init__.py`, and `infrastructure/__init__.py`.
- **BREAKING — Remove single-caller wrappers**: Inline Moodle username splitting once inside `AuthService.login` and remove `AuthService.authenticate`; pass `downloader_groups` directly into `IDATSync` / `TaskChannel`, make `run_task` required on `TaskChannel.create_workers`, and remove `TaskOrchestrator` and `_default_run_task`.

## Non-Goals

- Changing `SyncConfig.request_delay` or `AsyncRateLimiter` defaults (audit item 6 explicitly skipped).
- Modifying `GLOSSARY.md` or creating new ADRs.
- Altering `frontend/` or `src/idat_sync/ui/index.html`.

## Applicable Standards

- `backend/client-adapter-acl`
- `backend/content-strategy-dispatch`
- `global/layer-boundaries`
- `testing/composition-and-seams`

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `authentication`: Remove `AuthService.authenticate` (inline Moodle username split in `AuthService.login`) and select SharePoint MFA proof rows dynamically (`#idTxtBx_SAOTCC_OTC` direct, else `PhoneAppOTP` then `OneWaySMS`).
- `course-synchronization`: Stage `RecordingDownloader` atomically in `<name>.part/<name>` with `updatetime=False` and `overwrites=True`, ignore `*.part` ancestor segments in `LocalFileSystemAdapter.scan_mtimes`, pass `downloader_groups` directly to `IDATSync` / `TaskChannel` without `TaskOrchestrator` or `_default_run_task`, remove eager `__init__.py` re-exports in `application/`, `domain/`, and `infrastructure/`, fix `colab.ipynb` setup and post-processing, add CI `check` gating in `.github/workflows/build_and_deploy.yml`, and run the test suite with stdlib `unittest` without `pytest`.

## Impact

- `src/idat_sync/infrastructure/downloaders.py`, `filesystem.py`, `sharepoint_auth.py`, `__init__.py`
- `src/idat_sync/application/auth_service.py`, `sync_service.py`, `task_orchestrator.py`, `__init__.py`
- `src/idat_sync/domain/__init__.py`, `src/idat_sync/bootstrap.py`
- `colab.ipynb`, `.github/workflows/build_and_deploy.yml`
- `tests/*.py`
