# Tasks

## 1. Redact Moodle token from download HTTP errors

- [x] 1.1 At the `MoodleClient` (`src/idat_sync/infrastructure/moodle_client.py`) seam, add a failing test in `tests/test_moodle_client.py` verifying that `MoodleClient(token="secret-tok", ...).download_file` strips `wstoken` and `token` from `str(exc)` and `str(exc.request.url)` when `response.raise_for_status()` raises `httpx.HTTPStatusError`, then implement the redaction in `download_file` and verify `python -m unittest tests.test_moodle_client` passes.

## 2. Harden MoodleAdapter against null JSON fields and remove ModuleContent.filesize

- [x] 2.1 At the `MoodleAdapter` (`src/idat_sync/infrastructure/moodle_adapter.py`) seam, add a failing test in `tests/test_moodle_adapter.py` with explicit `None` values for `summary`, `intro`, `contents`, `introattachments`, `modules`, and `assignments` (plus asserting `not hasattr(ModuleContent(...), "filesize")`), then coerce `None` to `""` / `[]` in `MoodleAdapter` and remove `filesize` from `ModuleContent` (`src/idat_sync/domain/course.py`) and `_to_module_content`, and verify `python -m unittest tests.test_moodle_adapter` passes.

## 3. Preserve `#` and `?` in SharePoint recording paths

- [x] 3.1 At the `SharepointApiAdapter` (`src/idat_sync/infrastructure/sharepoint_adapter.py`) seam, add a failing test in `tests/test_sharepoint_adapter.py` where `file.server_relative_url` contains `#` and `?` (e.g. `"/personal/user/Documents/Curso #1/Grabaciones/Clase #2.mp4"`), then replace `urlparse(...).path` with direct `urllib.parse.unquote` + `PurePosixPath` in `is_course_recording`, and verify `python -m unittest tests.test_sharepoint_adapter` passes.

## 4. Make JsonCredentialStore.save atomic with 0o600 permissions

- [x] 4.1 At the `JsonCredentialStore` (`src/idat_sync/infrastructure/credential_store.py`) seam, add a failing test in `tests/test_auth.py` verifying that a simulated write/replace failure leaves an existing `session.json` intact and removes the `.part` file, then update `JsonCredentialStore.save` to write via a `0o600` sibling `.part` file and `os.replace`, and verify `python -m unittest tests.test_auth` passes.

## 5. Full verification

- [x] 5.1 Run the full test suite (`python -m unittest discover -s tests -p "test_*.py"`), typecheck, and lint (using whatever tools are configured in the environment), confirming all checks pass with zero failures.
