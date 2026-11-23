# Tasks

## 1. CourseTree File Resolution

- [x] 1.1 Write failing unit tests in `tests/test_auth.py` at the `CourseTree` seam for `resolve_openable_file(file_id)` (returns `Path` for `Synced`/`Completed` contained in `course_dir`, returns `None` for non-done statuses, unknown IDs, or `..` traversal), implement `resolve_openable_file` in `src/idat_sync/ui/course_tree.py`, and verify `pytest tests/test_auth.py -k CourseTree` passes.

## 2. SyncFrame Open-File Controller

- [x] 2.1 Write failing unit tests in `tests/test_auth.py` at the `SyncFrame` seam for `open_file(file_id)` (opens existing `Synced`/`Completed` files including when `is_syncing=True`, silently ignores non-ready rows, and logs + sets `error = "Could not open file"` without changing `status` or row states on missing file or `OSError`), implement `SyncFrame.open_file` in `src/idat_sync/ui/sync_frame.py`, and verify `pytest tests/test_auth.py -k SyncFrame` passes.

## 3. MainApp Native OS Opener & JS Bridge

- [x] 3.1 Write failing unit tests in `tests/test_auth.py` at the `MainApp` / `_JsApi` seam for `_JsApi.open_file` forwarding and `MainApp.open_path` across Windows (`os.startfile`), macOS (`open`), Linux (`xdg-open`), and Android (`jnius` `FileProvider` + `ACTION_VIEW` + `FLAG_GRANT_READ_URI_PERMISSION`), implement `MainApp.open_path` and `_JsApi.open_file` in `src/idat_sync/ui/app.py`, and verify `pytest tests/test_auth.py -k MainApp` passes.

## 4. Android FileProvider Packaging

- [x] 4.1 Write a failing test in `tests/test_auth.py` at the `buildozer.spec` seam asserting `androidx.core:core` and `FileProvider` manifest config exist, add the `FileProvider` Gradle/manifest configuration and XML paths resource to `buildozer.spec` (and Android XML file), and verify the test passes.

## 5. SolidJS UI Clickable Rows & Bundle

- [x] 5.1 Write a failing test in `tests/test_auth.py` at the `src/idat_sync/ui/index.html` seam asserting `open_file` is wired in the bundle, update `frontend/src/types.ts`, `frontend/src/components/CourseMain.tsx`, `frontend/src/components/ActivityPanel.tsx`, and `frontend/src/styles/global.css` so `Synced`/`Completed` `FileRow` items and `ActivityPanel` `Finished` items invoke `callApi("open_file", id)`, rebuild `src/idat_sync/ui/index.html` via `npm run typecheck && npm run build`, and verify `pytest tests/test_auth.py -k bundled_html` passes.

## 6. Full Verification

- [x] 6.1 Run the full test suite (`pytest`), Python lint & format (`ruff check src/ tests/`, `ruff format --check`), Python typecheck (`mypy src/idat_sync/ui/`), and frontend typecheck (`npm run typecheck`), confirming zero failures.
