# Design

## Context

See [proposal.md](./proposal.md) for motivation and capability impact. The `pywebview` + Vite/SolidJS single-file UI architecture is documented in [ADR-0007](../../../docs/adr/0007-pywebview-and-static-astro-ui.md) and [ADR-0008](../../../docs/adr/0008-vite-solidjs-single-file-ui.md).

Key codebase constraints:
- `ui/` MUST NOT import `idat_sync.infrastructure` (`global/layer-boundaries`).
- `domain/` and `application/` MUST NOT import `webview` or `jnius`; platform host glue (`pick_folder`, `webview.create_window`, and now `open_path`) lives in `src/idat_sync/ui/app.py`.
- `buildozer.spec` targets `pe.edu.idat.idatsync` with `android.minapi = 24` and `android.api = 34`, where Android enforces `FileUriExposedException` on raw `file://` URIs.

## Goals / Non-Goals

**Goals:**
- Open any `Synced` or `Completed` `Content` file in its native OS default application from `CourseMain` (`FileRow`) and `ActivityPanel` (`Finished` list), including while a sync is running.
- Resolve `file_id` strictly on the Python side inside `CourseTree` so the WebView never supplies arbitrary filesystem paths.
- Support Windows, macOS, Linux, and Android (`FileProvider` + `ACTION_VIEW` intent).

**Non-Goals:**
- Opening folders or revealing files in Explorer/Finder.
- Rendering or previewing files inside the WebView.
- Modifying `domain/`, `application/`, or `infrastructure/`.

## Decisions

1. **ID-Based Path Resolution in `CourseTree` (`resolve_openable_file(file_id)`)**:
   - Store or look up `(course_dir, file_node)` by `file_id` in `CourseTree`, returning `(course_dir / file_node.rel_path).resolve()` only when `file_node.is_done` (`RowStatus.SYNCED` or `RowStatus.COMPLETED`) and the resolved path is relative to `course_dir.resolve()`.
   - *Alternative rejected*: Passing `rel_path` or full local paths from JS to `_JsApi.open_file` (allows arbitrary path traversal / command launching from the WebView).

2. **Host-Owned Native Opener in `MainApp.open_path(path: Path)`**:
   - Keep OS dispatch in `MainApp.open_path(path)` (matching `MainApp.pick_folder()`):
     - `is_android()`: dynamically import `jnius` (`importlib.import_module("jnius")`), obtain `PythonActivity.mActivity`, build `content://` URI via `androidx.core.content.FileProvider.getUriForFile(activity, f"{activity.getPackageName()}.fileprovider", java_file)`, set MIME type via `mimetypes.guess_type(path.name)[0] or "*/*"`, add `Intent.FLAG_GRANT_READ_URI_PERMISSION`, and call `activity.startActivity(intent)`.
     - `sys.platform == "win32"`: `os.startfile(str(path))`
     - `sys.platform == "darwin"`: `subprocess.Popen(["open", str(path)])`
     - Linux / other POSIX: `subprocess.Popen(["xdg-open", str(path)])`
   - *Alternative rejected*: `webbrowser.open(path.as_uri())` (opens a browser tab instead of the native app for many file types and fails on Android).

3. **Android `FileProvider` Manifest & Paths Resource**:
   - Add `androidx.core:core:1.13.1` (or compatible `androidx.core:core`) to `android.gradle_dependencies` in `buildozer.spec`, add an XML `<provider>` snippet for `androidx.core.content.FileProvider` (`${applicationId}.fileprovider`, `grantUriPermissions="true"`) and a `file_paths.xml` allowing external storage (`<external-path name="external" path="." />` + `<files-path name="files" path="." />`).

4. **Clickable `FileRow` & `Finished` Rows in SolidJS**:
   - In `CourseMain.tsx`, when `file.status === "Synced" || file.status === "Completed"`, make `FileRow` clickable (`onClick={() => callApi("open_file", props.file.id)}`, pointer cursor + accessible `role="button"` / `tabIndex={0}` or `<button>`).
   - In `ActivityPanel.tsx`, make `Finished` `.mini` items `<button type="button" class="mini clickable" onClick={() => callApi("open_file", item.id)}>`.
   - Do not disable while `appState.is_syncing` is `true` because `Synced`/`Completed` files are already atomically written to disk.

## Risks / Trade-offs

- [No default app registered for an exotic file extension on Linux/Android] → Catch `OSError` / Android `ActivityNotFoundException` (via `Exception` in the Android branch mapped to `OSError`), log full details, and surface `"Could not open file"` in `SyncBar`.
- [Android `buildozer` APK build is not executed in Windows CI/local dev] → Unit-test the Android `jnius` `FileProvider` + `Intent` call sequence with a stubbed `jnius` module and assert `buildozer.spec` contains the `FileProvider` configuration.

## Modules Touched & Confirmed Public Test Seams

- **Python UI (3)**: `src/idat_sync/ui/course_tree.py`, `src/idat_sync/ui/sync_frame.py`, `src/idat_sync/ui/app.py`
- **Frontend & bundle (5)**: `frontend/src/types.ts`, `frontend/src/components/CourseMain.tsx`, `frontend/src/components/ActivityPanel.tsx`, `frontend/src/styles/global.css`, `src/idat_sync/ui/index.html`
- **Android packaging (2)**: `buildozer.spec`, Android `FileProvider` XML resource
- **Confirmed public test seams (5)**:
  1. `CourseTree` (`tests/test_auth.py`): `resolve_openable_file` status and containment checks.
  2. `SyncFrame` (`tests/test_auth.py`): `open_file` delegation (including mid-sync), no-op on non-ready rows, and missing-file / `OSError` error handling.
  3. `MainApp` & `_JsApi` (`tests/test_auth.py`): `open_file` bridge forwarding and `open_path` dispatch across Windows, macOS, Linux, and Android (`jnius` stub).
  4. `src/idat_sync/ui/index.html` & `frontend/` (`npm run typecheck && npm run build`, `tests/test_auth.py`): bundled `open_file` wiring.
  5. `buildozer.spec` (`tests/test_auth.py`): Android `FileProvider` config presence.
