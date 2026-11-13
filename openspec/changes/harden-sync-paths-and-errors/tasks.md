# Tasks

## 1. Domain Path Sanitization & Traversal Containment

- [x] 1.1 Write failing unit tests in `tests/test_sync_pipeline.py` for `domain.path_utils.safe_filename` (`CON.txt` -> `_CON.txt`, post-truncation trailing dot/space removal), extract shared `_sanitize_segment` helper keeping ASCII normalization in `safe_filename`, and verify `PYTHONPATH=src python -m unittest tests.test_sync_pipeline -v` passes.
- [x] 1.2 Write failing unit tests in `tests/test_sync_pipeline.py` for `domain.path_utils.safe_path_segment` (UTF-8 preservation, stripping `<>:"/\|?*` and `\x00-\x1F`/`\x7F`, `.`/`..` fallback, `WINDOWS_RESERVED` stems, and extension preservation on `max_len` truncation), implement `safe_path_segment` via `_sanitize_segment`, and verify `PYTHONPATH=src python -m unittest tests.test_sync_pipeline -v` passes.
- [x] 1.3 Write failing unit tests in `tests/test_sync_pipeline.py` at the `Content.in_folder` seam asserting `ValueError` on anchored/absolute `self.path` or `..` segments in `self.path` or `folder` while allowing an absolute `course_dir`, implement the fail-closed check in `src/idat_sync/domain/content.py`, and verify `PYTHONPATH=src python -m unittest tests.test_sync_pipeline -v` passes.

## 2. Adapter & Repository Path Sanitization

- [x] 2.1 Write failing unit tests in `tests/test_moodle_adapter.py` at the `MoodleAdapter` seam for unsafe section summary, label, assignment intro, and `list_url_content` filenames (`:` `/` `?` `CON.txt` and long titles preserving `-[id]-...html`), sanitize via `safe_path_segment`, and verify `PYTHONPATH=src python -m unittest tests.test_moodle_adapter -v` passes.
- [x] 2.2 Write failing unit tests in `tests/test_sharepoint_adapter.py` at the `SharepointApiAdapter` seam for unsafe recording names, subfolder names (`CON`), and shared file names while preserving UTF-8 accents, sanitize via `safe_path_segment`, and verify `PYTHONPATH=src python -m unittest tests.test_sharepoint_adapter -v` passes.
- [x] 2.3 Write failing unit tests in `tests/test_course_repository.py` at the `CourseRepository.get_course_content` seam for unsafe `CourseSection.name` and `CourseModule.name` values while still skipping empty raw names, pass `safe_path_segment` results to `.in_folder(...)`, and verify `PYTHONPATH=src python -m unittest tests.test_course_repository -v` passes.

## 3. Credential Expiry Port Mapping

- [x] 3.1 Write failing unit/AST tests in `tests/test_auth.py` and `tests/test_sync_pipeline.py` checking `CredentialsExpiredError(RuntimeError)` export and zero vendor/UI imports in `src/idat_sync/application/ports.py`, define and export `CredentialsExpiredError`, and verify `PYTHONPATH=src python -m unittest tests.test_auth tests.test_sync_pipeline -v` passes.
- [x] 3.2 Write failing unit tests in `tests/test_moodle_client.py` at the `MoodleClient` seam verifying `CredentialsExpiredError` on HTTP `401`/`403` in `call_ws` and `download_file` and on `errorcode == "invalidtoken"` (preserving HTTP `500` and non-auth exceptions), implement the mapping in `src/idat_sync/infrastructure/moodle_client.py`, and verify `PYTHONPATH=src python -m unittest tests.test_moodle_client -v` passes.
- [x] 3.3 Write failing unit tests in `tests/test_sharepoint_adapter.py` at the `SharepointClient` seam verifying `CredentialsExpiredError` on empty `personal_site.url` and HTTP `401`/`403` SDK responses (while propagating HTTP `404`/`500`), route SDK queries through a shared mapping helper in `src/idat_sync/infrastructure/sharepoint_client.py`, and verify `PYTHONPATH=src python -m unittest tests.test_sharepoint_adapter -v` passes.
- [x] 3.4 Write failing unit tests in `tests/test_sync_pipeline.py` at the `RecordingDownloader` seam verifying `CredentialsExpiredError` on HTTP `401`/`403` `yt_dlp` `DownloadError`s (preserving non-auth failures), implement mapping in `src/idat_sync/infrastructure/downloaders.py`, and verify `PYTHONPATH=src python -m unittest tests.test_sync_pipeline -v` passes.

## 4. Partial-Failure Isolation & SyncFrame Recovery

- [x] 4.1 Write failing unit tests in `tests/test_sync_pipeline.py` at the `TaskOrchestrator` seam verifying single-item non-credential exceptions log and continue remaining tasks in order while `CredentialsExpiredError` sets the cross-group abort flag, skips remaining unstarted tasks across groups, and propagates directly, implement in `src/idat_sync/application/task_orchestrator.py`, and verify `PYTHONPATH=src python -m unittest tests.test_sync_pipeline -v` passes.
- [x] 4.2 Write failing unit tests in `tests/test_sync_pipeline.py` at the `IDATSync.sync_courses` seam verifying single-`Course` non-credential exceptions log and skip while subsequent courses still sync whereas `CredentialsExpiredError` aborts remaining courses immediately, implement in `src/idat_sync/application/sync_service.py`, and verify `PYTHONPATH=src python -m unittest tests.test_sync_pipeline -v` passes.
- [x] 4.3 Write failing unit tests in `tests/test_auth.py` at the `SyncFrame` seam verifying startup `load_idat_sync` failure recovery without deleting stored `Credentials`, `is_syncing` reset and control re-enabling in `finally:`, lazy retry of `create_sync_service` in `do_sync` when `self.idat_sync is None`, and `Progress:` error output including `Logout` guidance on `CredentialsExpiredError`, implement in `src/idat_sync/ui/sync_frame.py`, and verify `PYTHONPATH=src python -m unittest tests.test_auth -v` passes.

## 5. Full Verification

- [x] 5.1 Run the full test suite (`PYTHONPATH=src python -m unittest discover -s tests -v`), bytecode check (`python -m compileall -q src tests`), strict OpenSpec validation (`cmd /c "openspec validate harden-sync-paths-and-errors --strict --json"`), and run available typecheck/lint tools (`ruff`, `mypy`, or `pyright` if installed; note if absent in environment) to confirm all checks pass.

## Workflow follow-up

- Commit changes before running `/code-review BASE openspec/changes/harden-sync-paths-and-errors`.
- Archive via `/opsx:archive` after review passes.
- Handle direct non-spec TDD bugfixes (atomic `.part` writes, SharePoint `datetime.min`/`timezone.utc` timestamps, macOS `p.webkit.launch` + `sys.stderr`, `LoginFrame` `<Return>` & MFA cleanup, etc.) and deferred `colab.ipynb` migration outside this OpenSpec change.
