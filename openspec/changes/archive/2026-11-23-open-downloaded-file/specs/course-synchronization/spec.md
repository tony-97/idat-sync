## ADDED Requirements

### Requirement: Native OS opening of downloaded course files from the UI
`CourseTree` in `src/idat_sync/ui/course_tree.py` SHALL expose `resolve_openable_file(self, file_id: str) -> Path | None`, returning the resolved local `Path` for `file_id` only when the matching file node has status `RowStatus.SYNCED` or `RowStatus.COMPLETED`, the course has a non-`None` `course_dir`, and the resolved path stays strictly contained within `course_dir` (returning `None` for unknown IDs, non-`Synced`/non-`Completed` statuses, or traversal paths escaping `course_dir`). `SyncFrame` in `src/idat_sync/ui/sync_frame.py` SHALL expose `open_file(self, file_id: str) -> None` (callable even while `self.is_syncing` is `True`), resolving `file_id` via `self.course_tree.resolve_openable_file(file_id)`, silently returning when `None`, verifying `path.is_file()` before delegating to `self.app.open_path(path)`, and on a missing file or `OSError` from `open_path` logging the full error and setting `self.error = "Could not open file"` and calling `self._notify()` without altering `self.status` or any row status. `MainApp` in `src/idat_sync/ui/app.py` SHALL expose `open_path(self, path: Path) -> None` and `_JsApi.open_file(self, file_id: str) -> None`, launching the native OS default application per platform:
- Windows (`sys.platform == "win32"`): `os.startfile(str(path))`
- macOS (`sys.platform == "darwin"`): `subprocess.Popen(["open", str(path)])`
- Linux (other non-Android POSIX): `subprocess.Popen(["xdg-open", str(path)])`
- Android (`is_android()`): `jnius` `androidx.core.content.FileProvider.getUriForFile(activity, f"{package_name}.fileprovider", java_file)` with `Intent(Intent.ACTION_VIEW)`, `setDataAndType(uri, mime_type)` (`mimetypes.guess_type(path.name)[0] or "*/*"`), `addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)`, and `activity.startActivity(intent)`.

`buildozer.spec` SHALL configure `androidx.core:core` and `FileProvider` manifest metadata (`<package>.fileprovider` + XML paths resource) matching the Android `open_path` authority. The compiled SolidJS bundle `src/idat_sync/ui/index.html` SHALL make `Synced` and `Completed` file rows in `CourseMain` and `Finished` rows in `ActivityPanel` clickable (even while `is_syncing` is `True`) to invoke `callApi("open_file", file.id)` without opening folders or rendering files inline. Verified at the `CourseTree`, `SyncFrame`, `MainApp`, `src/idat_sync/ui/index.html`, and `buildozer.spec` seams.

#### Scenario: Resolving openable files with status and containment checks in CourseTree
- **WHEN** `CourseTree.resolve_openable_file(file_id)` is called for a `Synced` file, a `Completed` file, a `New`/`Downloading`/`Failed` file, an unknown `file_id`, or a file whose `rel_path` attempts `..` traversal outside `course_dir`
- **THEN** it returns `course_dir / rel_path` for `Synced` and `Completed` files contained within `course_dir`, and returns `None` for all other cases

#### Scenario: Opening a downloaded file and handling missing or OS failure in SyncFrame
- **WHEN** `SyncFrame.open_file(file_id)` is called for an existing `Synced`/`Completed` file (including while `is_syncing` is `True`), for a `New` file, for a `Completed` file deleted from disk, and when `app.open_path(path)` raises `OSError`
- **THEN** it calls `app.open_path(path)` for the existing file, does nothing (no error, no `open_path` call) for the `New` file, and for the missing file or `OSError` logs the error at `logging.ERROR` and sets `self.error = "Could not open file"` while leaving `self.status` and file row statuses unchanged

#### Scenario: Dispatching native OS openers in MainApp.open_path across platforms
- **WHEN** `MainApp.open_path(path)` runs on Windows, macOS, Linux, and Android
- **THEN** it invokes `os.startfile(str(path))` on Windows, `subprocess.Popen(["open", str(path)])` on macOS, `subprocess.Popen(["xdg-open", str(path)])` on Linux, and `FileProvider.getUriForFile` + `Intent.ACTION_VIEW` with `FLAG_GRANT_READ_URI_PERMISSION` via `jnius` on Android

#### Scenario: Wiring clickable downloaded rows in the bundled SolidJS UI and Android FileProvider config
- **WHEN** `src/idat_sync/ui/index.html` and `buildozer.spec` are inspected
- **THEN** `index.html` wires `open_file` on `Synced`/`Completed` rows in `CourseMain` and `Finished` rows in `ActivityPanel`, and `buildozer.spec` declares `androidx.core:core` and `FileProvider` manifest metadata for `.fileprovider`
