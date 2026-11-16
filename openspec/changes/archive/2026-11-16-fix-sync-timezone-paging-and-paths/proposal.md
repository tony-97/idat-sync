# Proposal

## Why

A `/ponytail-audit` and `/grill-with-docs` review identified 15 concrete bugs, lifecycle leaks, data/security risks, missing regression tests, and dead-code cleanups across authentication, Moodle/SharePoint clients, path handling, the Tkinter UI, CLI scripts, and Colab tooling. Addressing all 15 together (while leaving Finding #10 untouched as agreed) resolves every confirmed failure mode before the next release.

## What Changes

- **SharePoint timezone, `datetime.min`, and SDK query fixes**: Add `timezone: str = "America/Lima"` (validated via `zoneinfo.ZoneInfo`) to `SyncConfig` and `"tzdata; sys_platform == 'win32'"` to `pyproject.toml`. **BREAKING**: Require `timezone: str` on `SharepointApiAdapter.__init__` (wired from `config.timezone` in `bootstrap.py`) and convert tz-aware `time_created` values before taking `.date()`; treat `office365`'s `datetime.min` default as missing in `get_last_modified` and `fetch_recordings`. Fix `SharepointClient.fetch_document_library_items` to `.select(["File/Name", "File/ServerRelativeUrl", "File/TimeCreated", "File/TimeLastModified"]).expand(["File"])`, and change `SharepointClient.create` cleanup to `except BaseException:`.
- **Windows long paths and collision loop fixes**: Apply Windows `\\?\<abspath>` (and `\\?\UNC\...`) inside `LocalFileSystemAdapter` and `downloaders.py` (`_atomic_download` and `RecordingDownloader`'s `mkdir`) while keeping domain `Path`s clean. Bound `preserve_extension` in `path_utils._sanitize_segment` to `1–15` chars with no whitespace, fix `safe_suffixed_segment` truncation, and guard `IDATSync._disambiguate_paths` against long dotted-title loops.
- **Moodle pagination and stream lifecycle**: Paginate `MoodleClient.fetch_enrolled_courses` via `nextoffset`, move `asyncio.sleep(self.request_delay)` outside `async with self._client.stream(...)` in `download_file`, and wrap `MoodleClient` in `async with` inside `scripts/moodle_json.py`.
- **UI, MFA popup, and `yt-dlp` cancellation hardening**: Bind `<Return>` on `LoginFrame.pass_entry` with `async_handler`, guard `validate_buttons` with `winfo_exists()`, destroy the MFA popup in `finally:` on cancellation, track and cancel active frame tasks in `LoginFrame.aclose()` / `SyncFrame.aclose()` on `MainApp.on_close`, abort `yt-dlp` worker threads via a `CancelledError -> threading.Event` `progress_hook` raising `yt_dlp.utils.DownloadCancelled`, and show `"Sync completed with errors."` in `SyncFrame.do_sync` when any `Course` fails.
- **Colab fixes and dead-code cleanup**: Update `colab.ipynb` (`subprocess.check_output` without shell, `aclose()`, `course.sections.clear()` + `invalidate_cache()`, HTML-mtime PDF refresh), drop unused imports and dead re-exports in `downloaders.py`, `infrastructure/__init__.py`, and `download_manager.py`, fix `[project.urls]` in `pyproject.toml`, and add regression tests for all fixes.

## Non-Goals

- Changing `SyncConfig.session_file` or `SyncConfig.log_file` defaults (`./session.json` and `./log.err`) for frozen apps (audit Finding #10 explicitly skipped).
- Modifying `GLOSSARY.md` or adding new ADRs under `docs/adr/`.

## Applicable Standards

- `backend/client-adapter-acl`
- `backend/content-strategy-dispatch`
- `global/layer-boundaries`
- `testing/composition-and-seams`

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `authentication`: Bind `<Return>` on `LoginFrame.pass_entry` with `async_handler`, guard `LoginFrame.validate_buttons` with `winfo_exists()`, destroy the MFA popup on cancellation, and cancel active `LoginFrame` / `SyncFrame` tasks in `aclose()` before `MainApp.on_close` destroys widgets.
- `course-synchronization`: Add `SyncConfig.timezone`, `SharepointApiAdapter.timezone`, `datetime.min` guards, SharePoint `$select`/`$expand` + `BaseException` cleanup, Windows `\\?\` paths, bounded `safe_path_segment` / `_disambiguate_paths`, `RecordingDownloader` `DownloadCancelled` hook, `MoodleClient` `nextoffset` paging and post-stream delay, `SyncFrame` cancellation + error summary, and `scripts/moodle_json.py` `async with`.

## Impact

- `pyproject.toml`, `colab.ipynb`, `scripts/moodle_json.py`
- `src/idat_sync/config.py`, `src/idat_sync/bootstrap.py`, `src/idat_sync/domain/path_utils.py`
- `src/idat_sync/application/{download_manager.py,sync_service.py}`
- `src/idat_sync/infrastructure/{__init__.py,downloaders.py,filesystem.py,moodle_client.py,sharepoint_adapter.py,sharepoint_client.py}`
- `src/idat_sync/ui/{login_frame.py,sync_frame.py}`
- `tests/{test_auth.py,test_config.py,test_moodle_client.py,test_real_data.py,test_sharepoint_adapter.py,test_sync_pipeline.py}`
