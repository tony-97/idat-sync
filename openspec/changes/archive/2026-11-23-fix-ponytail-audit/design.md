# Design

## Context

See `proposal.md` for motivation and scope. All nine audit items were verified against the current codebase after the `pywebview` migration; item 6 (`SyncConfig.request_delay`) is explicitly excluded. No `GLOSSARY.md` changes or new ADRs are required.

Applicable standards: `backend/client-adapter-acl`, `backend/content-strategy-dispatch`, `global/layer-boundaries`, and `testing/composition-and-seams`.

## Goals / Non-Goals

**Goals:**
- Make `RecordingDownloader` atomic and mtime-safe without breaking `ffmpeg` container format inference.
- Make `SharepointBrowserAuth` MFA selection resilient to accounts that jump straight to OTP entry or reorder MFA proof methods.
- Ensure `colab.ipynb` installs non-interactively and produces deduplicated, up-to-date post-processed outputs.
- Gate desktop and Android release builds behind automated tests and linters in CI, and keep the test suite stdlib-only (`unittest`).
- Eliminate eager heavy imports in layer `__init__.py` files and remove single-caller wrappers (`AuthService.authenticate`, `TaskOrchestrator`, `_default_run_task`).

**Non-Goals:**
- Changing `SyncConfig.request_delay` or `AsyncRateLimiter` semantics.
- Changing `TaskChannel` module location (`src/idat_sync/application/task_orchestrator.py` stays in place to keep diff surgical and preserve existing imports/architecture checks).
- Changing `frontend/` or `src/idat_sync/ui/index.html`.

## Decisions

### 1. Sibling `.part` staging directory (`<name>.part/<name>`) for `RecordingDownloader`
- **Choice**: Stage `yt-dlp` output inside `part_dir = os_target.with_name(f"{os_target.name}.part")` with `staged_target = part_dir / os_target.name`, set `"updatetime": False` and `"overwrites": True`, atomically call `staged_target.replace(os_target)` on success, and remove `part_dir` via `shutil.rmtree(part_dir, ignore_errors=True)` in `finally:`. Extend `LocalFileSystemAdapter.scan_mtimes` to exclude any path where `any(part.endswith(".part") for part in rel.parts)`.
- **Rationale**: Inspected `yt_dlp.postprocessor.ffmpeg.FFmpegMergerPP.run` and `FFmpegMetadataPP.run`: both call `prepend_extension(filename, "temp")` and run `ffmpeg` without `-f <format>`. Passing a `.part`-suffixed file directly as `outtmpl` (e.g., `video.mp4.part`) causes `prepend_extension` to emit `video.mp4.temp.part`, which breaks `ffmpeg` container-format inference. Staging `<name>` inside sibling directory `<name>.part/` preserves the exact target filename/extension for `yt-dlp` and `ffmpeg` (including extensionless targets), keeps all intermediate DASH fragment/temp/metadata files isolated for deterministic cleanup in `finally:`, stays on the same filesystem volume for atomic `Path.replace`, and remains invisible to `scan_mtimes` even after a hard process kill.
- **Alternatives considered**:
  - `outtmpl = "<target>.part"`: Rejected because `ffmpeg` fails to infer container format from `.part`.
  - `outtmpl = "<stem>.part<suffix>"`: Rejected because yt-dlp leaves sibling fragment files (`.f137.mp4`, `.temp.mp4`, `.part`) in the destination directory and doesn't match `*.part` scanning rules.

### 2. Combined selector wait + priority branching for SharePoint MFA
- **Choice**: In `SharepointBrowserAuth._complete_login`, replace the hardcoded `#idDiv_SAOTCS_Proofs > div:nth-child(2) [role="button"]` click with:
  1. `await _wait_for_selector(driver, '#idTxtBx_SAOTCC_OTC, #idDiv_SAOTCS_Proofs [data-value="PhoneAppOTP"], #idDiv_SAOTCS_Proofs [data-value="OneWaySMS"]')`
  2. If `not await _eval_bool(driver, _js_is_visible("#idTxtBx_SAOTCC_OTC"))`:
     - If `await _eval_bool(driver, _js_is_visible('#idDiv_SAOTCS_Proofs [data-value="PhoneAppOTP"]'))`: click it
     - Else: click `#idDiv_SAOTCS_Proofs [data-value="OneWaySMS"]`
- **Rationale**: Waiting on the specific clickable proof selectors rather than only `#idDiv_SAOTCS_Proofs` avoids a Knockout render race where the outer container appears before its proof rows hydrate. Checking `#idTxtBx_SAOTCC_OTC` first handles accounts that land directly on the 6-digit code input; preferring `PhoneAppOTP` over `OneWaySMS` matches the agreed MFA priority regardless of row order.
- **Alternatives considered**:
  - Waiting on `#idTxtBx_SAOTCC_OTC, #idDiv_SAOTCS_Proofs` then checking child selectors immediately: Rejected due to hydration race before child `[data-value]` rows become visible.

### 3. Two-pass Colab post-processing
- **Choice**: In `colab.ipynb` cell 3, perform conversions in pass 1 (`.html` → `.pdf` and `video/*` → `.mp3`, both guarded by `os.path.exists(out) and os.path.getmtime(src) <= os.path.getmtime(out)`), then walk `course_folder` in pass 2 collecting `.mp3` paths into `recordings` and all other non-`.html`/non-`video/*` files into `links`.
- **Rationale**: Separating mutation (creating/updating `.pdf` and `.mp3`) from enumeration eliminates `os.walk` double-visiting freshly regenerated `.pdf` files and guarantees every converted output gets a single Drive share link.

### 4. CI workflow gating with stdlib `unittest`
- **Choice**: Add a `check` job (`ubuntu-latest`, Python 3.14) to `.github/workflows/build_and_deploy.yml` running `pip install --group dev .`, `python -m unittest`, `ruff check .`, and `pylint src tests`. Gate `build` and `build-android` with `needs: check` and `if: github.ref_type == 'tag' || github.event_name == 'workflow_dispatch'`. Replace all 126 `pytest.raises` calls across 9 test files with `self.assertRaises` / `self.assertRaisesRegex` and remove `import pytest`.
- **Rationale**: All test classes already inherit from `unittest.TestCase` / `unittest.IsolatedAsyncioTestCase` and only imported `pytest` for `pytest.raises`. Dropping `pytest` removes an undeclared dependency and lets `python -m unittest` work out of the box locally and in CI.
- **Alternatives considered**:
  - Adding `pytest>=8.0` to `[dependency-groups].dev`: Rejected by user to keep zero test-runner dependencies.

### 5. Removing eager `__init__.py` re-exports and single-caller wrappers
- **Choice**:
  - Strip all imports and `__all__` from `src/idat_sync/application/__init__.py`, `src/idat_sync/domain/__init__.py`, and `src/idat_sync/infrastructure/__init__.py`, leaving only each module docstring.
  - Inline `moodle_username = username.split("@", 1)[0]` once inside `AuthService.login`, pass it to `self.moodle_auth.fetch_token(moodle_username, password)` and `_logger.info(...)`, and delete `AuthService.authenticate`.
  - Delete `TaskOrchestrator` and `_default_run_task` from `src/idat_sync/application/task_orchestrator.py`, make `run_task` required on `TaskChannel.create_workers`, pass `downloader_groups` directly into `IDATSync.__init__` from `bootstrap.create_sync_service`, and instantiate `TaskChannel(sync.downloader_groups)` directly in `_CourseStreamRun.__init__`.
- **Rationale**: Every production caller already imports from concrete submodules; removing eager re-exports avoids pulling in `pywebview`, `yt_dlp`, `office365`, and `httpx` when importing lightweight modules such as `credential_store` or `rate_limiter`. Removing `AuthService.authenticate` and `TaskOrchestrator` eliminates pass-through indirection without altering runtime architecture or layer boundaries.

## Risks / Trade-offs

- **[Risk] Windows long-path staging directory length** → `safe_path_segment` caps leaf filenames at 100 chars, so `<name>.part/<name>` adds at most 106 chars; `windows_long_path` already applies the `\\?\` prefix (32,767-char limit), and on POSIX each component remains well below the 255-byte limit.
- **[Risk] Mocked `YoutubeDL` tests that don't write `outtmpl` to disk** → Existing unit tests in `tests/test_sync_pipeline.py` that stub `YoutubeDL.download` without writing a file will fail `staged_target.replace(os_target)` unless the stub writes the staged file; updating those stubs strengthens tests by verifying the real atomic rename and cleanup.
- **[Risk] `pylint` / `ruff` rules on `assertRaises` / `assertRaisesRegex` conversions** → Replacing `pytest.raises(..., match=...)` with `self.assertRaisesRegex(...)` (and `self.assertRaises(...)` when no `match=` is used) requires checking `.exception` instead of `.value` and satisfying `ruff` (`PT` / `B017` / `SIM`) and `pylint` (`10.0/10.0`).
- **[Risk] CI `check` job installing Linux dependencies** → `pip install --group dev .` does not require GTK/WebKit system packages on `ubuntu-latest` because tests inject fake `WebViewDriver`s and mock `webview`; verified when `python -m unittest` runs cleanly without GUI system packages.
