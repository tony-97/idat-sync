# Proposal

## Why

Users can see synchronized `Content` files in `CourseMain` and `ActivityPanel`, but have no way to open them from the app without manually navigating the local filesystem. Allowing users to click any `Synced` or `Completed` file to launch it in the native OS default application completes the core download-and-study workflow across desktop and Android.

## What Changes

- Expose `open_file(file_id)` on `_JsApi` in `src/idat_sync/ui/app.py`, delegating to `SyncFrame.open_file(file_id)` and `MainApp.open_path(path)`.
- Add `CourseTree.resolve_openable_file(file_id)` in `src/idat_sync/ui/course_tree.py` to resolve `file_id` to its local `Path` only when the file's status is `RowStatus.SYNCED` or `RowStatus.COMPLETED` and the path stays contained within the course directory.
- Implement per-platform native default-app launching in `MainApp.open_path(path)`:
  - Windows: `os.startfile(str(path))`
  - macOS: `subprocess.Popen(["open", str(path)])`
  - Linux: `subprocess.Popen(["xdg-open", str(path)])`
  - Android: `jnius` `androidx.core.content.FileProvider.getUriForFile` (`<package>.fileprovider`) + `Intent(ACTION_VIEW)` with `FLAG_GRANT_READ_URI_PERMISSION` and MIME type lookup (`*/*` fallback), plus matching `FileProvider` manifest/Gradle configuration in `buildozer.spec`.
- Make `Synced`/`Completed` `FileRow` items in `CourseMain.tsx` and `Finished` items in `ActivityPanel.tsx` clickable (even while `is_syncing` is true) to invoke `callApi("open_file", file.id)`.
- Log full errors and set `SyncFrame.error = "Could not open file"` when a file is missing on disk or the OS launcher raises an error, without altering `SyncFrame.status` or row statuses.

## Non-Goals

- Opening local course folders or revealing files in Explorer/Finder.
- Rendering or previewing files inline inside the WebView.
- Changing `domain/`, `application/`, or `infrastructure/` layers.

## Capabilities

### New Capabilities

### Modified Capabilities

- `course-synchronization`: Add native OS file opening for `Synced` and `Completed` `Content` items across Windows, macOS, Linux, and Android through the `pywebview` JS API, `SyncFrame`, `CourseTree`, `MainApp`, bundled SolidJS UI, and `buildozer.spec`.

## Impact

- **Python UI**: `src/idat_sync/ui/course_tree.py`, `src/idat_sync/ui/sync_frame.py`, `src/idat_sync/ui/app.py`
- **Frontend & bundle**: `frontend/src/types.ts`, `frontend/src/components/CourseMain.tsx`, `frontend/src/components/ActivityPanel.tsx`, `frontend/src/styles/global.css`, `src/idat_sync/ui/index.html`
- **Android packaging**: `buildozer.spec` (and `FileProvider` XML resource)
- **Tests**: `tests/test_auth.py`
- **Applicable `agent-os` standards** (`agent-os/standards/index.yml`):
  - `global/layer-boundaries`
  - `testing/composition-and-seams`
