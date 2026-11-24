## MODIFIED Requirements

### Requirement: Strict inward package dependency boundary
The `src/idat_sync/domain/` and `src/idat_sync/application/` packages SHALL have zero imports from `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `httpx`, `office365`, `yt_dlp`, `playwright`, `tkinter`, `async_tkinter_loop`, `webview`, or `jnius`. No module under `src/idat_sync/` SHALL import `requests`, `playwright`, `tkinter`, or `async_tkinter_loop`, or call `print(...)`, `src/idat_sync/ui/` MUST NOT import `idat_sync.infrastructure`, `src/idat_sync/application/task_orchestrator.py` SHALL have zero imports from `idat_sync.application.events` or `threading`, `src/idat_sync/application/__init__.py`, `src/idat_sync/domain/__init__.py`, and `src/idat_sync/infrastructure/__init__.py` SHALL contain only their module docstrings with zero import statements so importing a lightweight submodule MUST NOT eagerly load unrelated adapters or third-party SDKs, `src/idat_sync/infrastructure/auth.py`, `src/idat_sync/utils.py`, `src/idat_sync/ui/dialogs.py`, `src/idat_sync/ui/icons.py`, and `src/idat_sync/ui/mfa_dialog.py` MUST NOT exist, and `safe_filename` and `WINDOWS_RESERVED` SHALL reside in `src/idat_sync/domain/path_utils.py`. Verified at the AST architecture boundary (`src/idat_sync/`) and `domain.path_utils` (`src/idat_sync/domain/path_utils.py`) seams.

#### Scenario: Inspecting domain and application imports
- **WHEN** all Python modules under `src/idat_sync/domain/` and `src/idat_sync/application/` are parsed for import statements
- **THEN** none of them import `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `httpx`, `office365`, `yt_dlp`, `playwright`, `tkinter`, `async_tkinter_loop`, `webview`, or `jnius`, and `src/idat_sync/application/task_orchestrator.py` does not import `idat_sync.application.events` or `threading`

#### Scenario: Keeping layer package __init__.py files free of eager re-exports
- **WHEN** `src/idat_sync/application/__init__.py`, `src/idat_sync/domain/__init__.py`, and `src/idat_sync/infrastructure/__init__.py` are parsed via AST
- **THEN** each contains only a module docstring and zero import statements (`ast.Import` or `ast.ImportFrom`)

#### Scenario: Sanitizing course filenames without importing infrastructure
- **WHEN** `safe_filename(s, replacement=" ", max_len=100)` in `src/idat_sync/domain/path_utils.py` is called with accents, forbidden characters, or Windows reserved names (`WINDOWS_RESERVED`)
- **THEN** it returns the normalized, safe ASCII filename of at most `max_len` characters

#### Scenario: Enforcing ui and playwright isolation and legacy auth/utils removal
- **WHEN** all Python modules under `src/idat_sync/` are parsed via AST for imports and calls and removed legacy paths are checked
- **THEN** `src/idat_sync/utils.py`, `src/idat_sync/infrastructure/auth.py`, `src/idat_sync/ui/dialogs.py`, `src/idat_sync/ui/icons.py`, and `src/idat_sync/ui/mfa_dialog.py` do not exist, no module imports `idat_sync.utils`, `idat_sync.infrastructure.auth`, `requests`, `playwright`, `tkinter`, or `async_tkinter_loop`, no module under `src/idat_sync/ui/` imports `idat_sync.infrastructure`, and zero modules under `src/idat_sync/` contain a `print(...)` call

### Requirement: Composition root wires production dependencies outside application
`async def create_sync_service(credentials: Credentials, config: SyncConfig) -> IDATSync` in `src/idat_sync/bootstrap.py` SHALL require an explicit `SyncConfig` without a `publish` parameter, construct `moodle_limiter = AsyncRateLimiter(config.request_delay)` and `sharepoint_limiter = AsyncRateLimiter(config.request_delay)`, construct `MoodleClient(..., rate_limiter=moodle_limiter)` and `MoodleAdapter`, await `await SharepointClient.create(..., rate_limiter=sharepoint_limiter)` (awaiting `moodle_client.aclose()` before re-raising if `SharepointClient.create(...)` fails), construct `SharepointApiAdapter`, `LocalFileSystemAdapter`, and `Downloader` groups with `config` values, wire `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)` directly to the low-level clients without `request_delay`, wire `RecordingDownloader(cookies, sharepoint_personal_host=config.sharepoint_personal_host, rate_limiter=sharepoint_limiter)` sharing the exact `sharepoint_limiter` instance with `SharepointClient`, and inject the composed collaborators, `downloader_groups=download_tasks_group`, `recordings_folder_name`, and `closers=[moodle_client.aclose, sharepoint_raw_client.aclose]` into `IDATSync` without `TaskOrchestrator`. `IDATSync.__init__` SHALL accept `(course_repository, download_manager, downloader_groups: Sequence[Sequence[Downloader]], file_system, recordings_folder_name, *, closers=(), progress_interval=0.15)` without instantiating clients or adapters from `Credentials` and MUST NOT accept `publish` or `task_orchestrator`; `IDATSync` SHALL expose `async def aclose(self) -> None` (invoking every closer once, ensuring remaining closers still execute if an earlier closer fails, and re-raising the first exception if any) and `__aenter__` / `__aexit__` delegating to `aclose()`. `configure_logging(log_file: Path) -> logging.Logger` in `src/idat_sync/bootstrap.py` SHALL configure `logging.getLogger("idat_sync")` at `logging.INFO` with `propagate = False`, closing and clearing existing handlers before attaching `logging.StreamHandler()` and `logging.FileHandler(log_file, mode="w", encoding="utf-8")`. Verified at `bootstrap.create_sync_service`, `bootstrap.configure_logging`, and `IDATSync` (`src/idat_sync/application/sync_service.py`) seams.

#### Scenario: Constructing IDATSync with injected collaborators
- **WHEN** `IDATSync(course_repository, download_manager, downloader_groups, file_system, recordings_folder_name="Videos")` is instantiated and `async for _ in sync.sync_course(course, course_dir):` runs
- **THEN** it stores the injected collaborators and `downloader_groups` without network or credential initialization in `__init__`, rejects `publish` or `task_orchestrator` keyword arguments with `TypeError`, and ensures and syncs recordings under `Path(course_dir) / "Videos"`

#### Scenario: Closing injected closers deterministically via IDATSync.aclose
- **WHEN** `IDATSync(..., closers=[closer_a, closer_b])` exits `async with` or awaits `await sync.aclose()`
- **THEN** each closer is awaited once (`closer_b` still runs if `closer_a` raises, re-raising the first failure) and repeated `aclose()` calls are safe no-ops

#### Scenario: Wiring SyncConfig through create_sync_service
- **WHEN** `configure_logging(log_file)` and `await create_sync_service(credentials, config=custom_config)` are called
- **THEN** `configure_logging` configures `logging.getLogger("idat_sync")` at `logging.INFO` (`propagate == False`) with one `StreamHandler` and one `FileHandler` targeting `log_file`, `await create_sync_service` forwards `custom_config` fields to `MoodleClient`, `MoodleAdapter`, `await SharepointClient.create(...)`, `SharepointApiAdapter` (without `request_delay`), `RecordingDownloader`, and `IDATSync` (passing `downloader_groups` directly), wires `moodle_adapter.client.rate_limiter` and `sp_adapter.sp_client.rate_limiter` as distinct `AsyncRateLimiter(custom_config.request_delay)` instances with `rec_dl.rate_limiter is sp_adapter.sp_client.rate_limiter`, wires `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_raw_client)` with `moodle_dl.moodle_client is moodle_adapter.client` and `sp_dl.sharepoint_client is sp_adapter.sp_client`, and `await idat_sync.aclose()` closes both `moodle_client` and `sharepoint_raw_client`

#### Scenario: Cleaning up MoodleClient when SharepointClient.create fails in create_sync_service
- **WHEN** `await SharepointClient.create(...)` raises during `await create_sync_service(credentials, config=custom_config)`
- **THEN** `moodle_client.aclose()` is awaited before the exception propagates

### Requirement: Mandatory FileSystemPort delegation in DownloadManager
`application/ports.py` SHALL define `ProgressCallback = Callable[[int, int | None], None]` and update `Downloader.download(self, content: C, on_progress: ProgressCallback | None = None) -> None` and `DownloadTask.download(self, on_progress: ProgressCallback | None = None) -> None` (calling `self.downloader.download(self.content)` when `on_progress is None` and `self.downloader.download(self.content, on_progress=on_progress)` otherwise). `DownloadManager` SHALL require `FileSystemPort` as a non-nullable constructor dependency and delegate all local file reads to `FileSystemPort`. `FileSystemPort` and `LocalFileSystemAdapter` SHALL expose `scan_mtimes(root_dir)`, `ensure_dir(path)`, and `read_bytes(path) -> bytes | None` (returning `None` on `OSError`). `LocalFileSystemAdapter` in `src/idat_sync/infrastructure/filesystem.py` SHALL exclude temporary `*.part` files and any files inside `*.part` staging directories (`not any(part.endswith(".part") for part in rel.parts)`) in `scan_mtimes(root_dir)`, apply Windows extended-length `\\?\` path normalization internally at the OS boundary (returning `scan_mtimes` keys relative to the caller's `root_dir` without `\\?\`) so directories and files exceeding 259 characters on Windows are created, read, and scanned accurately, and translate `OSError` in `scan_mtimes` and `ensure_dir` into `FileSystemError(...) from exc`. `DownloadManager` SHALL expose `create_download_task(self, content: Content) -> DownloadTask | None` and `classify_content(self, content: Content, local_mtimes: Mapping[Path, int]) -> ContentSyncState` (replacing `is_pending`), delegating existence/mtime classification to `domain.sync.classify_content(content, local_mtimes)` and, for any `StringContent` classified as `ContentSyncState.SYNCED` with `last_modified == 0` and non-empty `content.rendered_bytes()`, returning `ContentSyncState.OUTDATED` when `self.file_system.read_bytes(content.path) != rendered` (without hashing). `DownloadManager` MUST NOT perform direct filesystem I/O (`os`, `stat`, `rglob`, `mkdir`) and MUST NOT define `is_pending`. Verified at the `DownloadManager`, `application.ports`, and `FileSystemPort` / `LocalFileSystemAdapter` public seams.

#### Scenario: Scanning local tree modification times
- **WHEN** `LocalFileSystemAdapter.scan_mtimes(root_dir)` runs on a directory containing completed files, `*.part` temporary files, files nested inside `<name>.part/` staging directories, or nested paths exceeding 259 characters on Windows
- **THEN** `LocalFileSystemAdapter.scan_mtimes` returns modification timestamps keyed under `root_dir` (including deep Windows paths) while omitting any file whose filename or any relative ancestor directory ends with `".part"`

#### Scenario: Translating OSError in scan_mtimes and ensure_dir to FileSystemError
- **WHEN** `LocalFileSystemAdapter.scan_mtimes(root_dir)` or `LocalFileSystemAdapter.ensure_dir(path)` encounters an `OSError` vs `read_bytes(path)` encountering an `OSError`
- **THEN** `scan_mtimes` and `ensure_dir` raise `FileSystemError(...) from exc`, while `read_bytes` returns `None`

#### Scenario: Determining whether a task needs download
- **WHEN** `DownloadManager.classify_content(content, local_mtimes)` and `DownloadManager.create_download_task(content)` evaluate missing, outdated, synced, and `StringContent(last_modified=0)` items (with matching, modified, or unreadable local bytes)
- **THEN** `classify_content` returns `ContentSyncState.NEW` for missing files, `ContentSyncState.OUTDATED` for newer remote mtimes and byte-mismatched or unreadable `StringContent(last_modified=0)` items, and `ContentSyncState.SYNCED` for unchanged items without calling `Path.rglob` or `Path.stat` directly, `hasattr(DownloadManager, "is_pending")` is `False`, and `create_download_task` maps each supported `Content` to a `DownloadTask` without querying `file_system`

#### Scenario: Enforcing minimal FileSystemPort and DownloadTask surface
- **WHEN** `FileSystemPort`, `LocalFileSystemAdapter`, `DownloadManager`, and `DownloadTask` are inspected and `await DownloadTask(content, downloader).download()` is called with and without `on_progress`
- **THEN** `FileSystemPort` and `LocalFileSystemAdapter` expose `scan_mtimes`, `ensure_dir`, and `read_bytes`, `DownloadManager` exposes `create_download_task` and `classify_content` without direct filesystem I/O, and `DownloadTask` exposes `path` and `download(on_progress=None)` calling `downloader.download(content)` when `on_progress is None` (preserving custom `Downloader` subclasses omitting `on_progress`) and forwarding `on_progress=callback` when provided

### Requirement: Unconditional target file path in RecordingDownloader
`RecordingDownloader(cookies: list[CookieData], sharepoint_personal_host: str, rate_limiter: AsyncRateLimiter)` in `src/idat_sync/infrastructure/downloaders.py` SHALL store `self.rate_limiter = rate_limiter`, and `async def download(self, content: RecordingContent, on_progress: ProgressCallback | None = None) -> None` SHALL enter `async with self.rate_limiter:` and unconditionally treat `content.path` as the target file path by creating `Path(content.path).parent` (using Windows `\\?\` extended-length normalization before `mkdir(parents=True, exist_ok=True)`), staging the download inside a sibling directory `part_dir = os_target.with_name(f"{os_target.name}.part")` at `staged_target = part_dir / os_target.name` (cleaning any pre-existing `part_dir` first, creating `part_dir`, passing `outtmpl` for `staged_target` with `"updatetime": False` and `"overwrites": True` in `ydl_opts`), passing a `progress_hook` checked against a per-download `threading.Event` that raises `yt_dlp.utils.DownloadCancelled` when set, bridging progress updates onto the caller's event loop via `loop.call_soon_threadsafe(...)` (only when `on_progress is not None` and `not cancel_event.is_set()`), tracking cumulative bytes across sequential video and audio DASH passes by accumulating finished pass byte counts into `completed_bytes` and reporting `(completed_bytes + downloaded_bytes, completed_bytes + total_bytes if total_bytes is not None else None)` where `total_bytes = status.get("total_bytes") or status.get("total_bytes_estimate")`, offloading `YoutubeDL(ydl_opts).download([content.url])` via `await asyncio.to_thread(...)`, atomically renaming `staged_target.replace(os_target)` only on success, always removing `part_dir` via `shutil.rmtree(part_dir, ignore_errors=True)` in `finally:` (on success, error, or cancellation), catching `asyncio.CancelledError` to set the `threading.Event` before re-raising, translating `401`/`403` `yt_dlp.utils.DownloadError` to `CredentialsExpiredError(str(exc), service="SharePoint") from exc`, translating non-auth `yt_dlp.utils.DownloadError` to `RemoteServiceError(str(exc), service="SharePoint", ...) from exc`, and translating `OSError` to `FileSystemError(...) from exc` (with `operation="write"` for staging/download/rename failures and default `operation="access"` for parent directory creation). `RecordingDownloader.download` MUST NOT branch on `Path(content.path).suffix`, MUST NOT append `"%(title)s %(id)s.%(ext)s"`, and `src/idat_sync/infrastructure/downloaders.py` MUST NOT import `time`. Verified at the `RecordingDownloader` (`src/idat_sync/infrastructure/downloaders.py`) public seam.

#### Scenario: Downloading a recording with a file extension
- **WHEN** `await RecordingDownloader.download(content)` is called with `RecordingContent(Path("Grabaciones/Semana 1/video.mp4"), 1700000000, url)`
- **THEN** it enters `async with self.rate_limiter:`, creates `Grabaciones/Semana 1`, passes `ydl_opts` with `"updatetime": False`, `"overwrites": True`, and `"outtmpl"` targeting `<parent>/video.mp4.part/video.mp4` (with `\\?\` prefix on Windows) to `yt_dlp.YoutubeDL` inside `await asyncio.to_thread(...)`, atomically moves the staged file to `video.mp4`, and removes `video.mp4.part` in `finally:`

#### Scenario: Downloading a recording without a file extension in path
- **WHEN** `await RecordingDownloader.download(content)` is called with a `RecordingContent` whose `path` has no file suffix (`Path(content.path).suffix == ""`)
- **THEN** it creates `Path(content.path).parent` (not `Path(content.path)` as a directory), passes `ydl_opts` with `"updatetime": False`, `"overwrites": True`, and `"outtmpl"` targeting `<parent>/<name>.part/<name>` without appending `"%(title)s %(id)s.%(ext)s"`, atomically moves the staged file to `content.path`, and removes `<parent>/<name>.part` in `finally:`

#### Scenario: Cleaning up staged recording files on failure or cancellation without corrupting target
- **WHEN** `YoutubeDL.download(...)` raises `DownloadError`, `DownloadCancelled`, or `OSError` while a pre-existing `content.path` exists (or does not exist)
- **THEN** `part_dir` (`<parent>/<name>.part`) and any partial/intermediate files inside it are deleted in `finally:`, `content.path` is left untouched (neither created as a corrupted file nor overwritten), and the error is translated to `CredentialsExpiredError`, `RemoteServiceError`, or `FileSystemError` (or `DownloadCancelled` on cancellation)

#### Scenario: Reporting cumulative DASH video and audio byte progress onto the event loop
- **WHEN** `await RecordingDownloader.download(content, on_progress=cb)` runs and `yt_dlp`'s `progress_hook` receives downloading/finished dicts for a 400-byte video pass (`total_bytes_estimate=400`) followed by a 100-byte audio pass (`total_bytes=100`), plus an indeterminate pass where both `total_bytes` and `total_bytes_estimate` are `None`
- **THEN** `cb` is scheduled onto the caller's asyncio event loop via `loop.call_soon_threadsafe` with cumulative `(downloaded, total)` pairs `(200, 400)`, `(400, 400)`, `(450, 500)`, `(500, 500)` (never resetting downloaded bytes back to `0` on the audio pass), passes `total=None` when neither total nor estimate is present, and stops scheduling progress once `cancel_event` is set

#### Scenario: Aborting worker thread download on asyncio task cancellation
- **WHEN** an active `RecordingDownloader.download(content)` task is cancelled via `asyncio.CancelledError` while `yt_dlp` is downloading in `asyncio.to_thread(...)`
- **THEN** the per-download `threading.Event` is set immediately before `CancelledError` propagates, and invoking the registered `progress_hook` raises `yt_dlp.utils.DownloadCancelled`

#### Scenario: Translating DownloadError and OSError in RecordingDownloader
- **WHEN** `RecordingDownloader.download(content)` encounters a `401`/`403` `DownloadError`, a non-auth `DownloadError`, or an `OSError` (such as directory creation or write/rename failure)
- **THEN** it raises `CredentialsExpiredError(str(exc), service="SharePoint") from exc`, `RemoteServiceError(str(exc), service="SharePoint", ...) from exc`, or `FileSystemError(...) from exc` respectively

### Requirement: Per-item failure isolation and early credential abort in TaskOrchestrator
`src/idat_sync/application/task_orchestrator.py` SHALL define `TaskChannel(groups: Sequence[Sequence[Downloader]])` and SHALL NOT define `TaskOrchestrator` or `_default_run_task`, and SHALL NOT import `idat_sync.application.events` or `threading`. `TaskChannel` SHALL own one unbounded `asyncio.Queue[DownloadTask | None]` per group in `groups` and expose `enqueue(self, task: DownloadTask) -> None` (routing by `task.downloader` and raising `ValueError` for an unregistered downloader), `close(self) -> None` (idempotently pushing `None` sentinels into every group queue so workers drain remaining tasks and exit), and `create_workers(self, run_task: Callable[[DownloadTask], Awaitable[None]]) -> list[Coroutine[object, object, None]]` (requiring `run_task` as a mandatory parameter and returning one sequential FIFO worker coroutine per group awaiting `run_task(task)` until receiving `None`). Verified at the `TaskChannel` (`src/idat_sync/application/task_orchestrator.py`) seam.

#### Scenario: Continuing remaining tasks after a single-item download failure
- **WHEN** `channel = TaskChannel([[dl_a, dl_b], [dl_c]])` starts `create_workers(run_task)` concurrently with a producer calling `channel.enqueue(...)` for `dl_a`, `dl_b`, and `dl_c` followed by `channel.close()` (including repeated `channel.close()` calls), and `task_orchestrator` module attributes and `create_workers()` without `run_task` are inspected
- **THEN** `dl_a` and `dl_b` tasks execute sequentially in FIFO order inside the first group worker while `dl_c` executes concurrently in the second group worker, repeated `channel.close()` calls are safe no-ops, `hasattr(task_orchestrator_mod, "TaskOrchestrator")` and `hasattr(task_orchestrator_mod, "_default_run_task")` are `False`, and calling `channel.create_workers()` without `run_task` raises `TypeError`

#### Scenario: Aborting all remaining tasks on CredentialsExpiredError
- **WHEN** `channel.enqueue(task)` is called with a `Downloader` absent from `groups`, or `run_task` raises an unhandled exception (such as `CredentialsExpiredError`) inside a worker coroutine returned by `channel.create_workers(run_task)`
- **THEN** `enqueue` raises `ValueError` immediately, and an unhandled worker exception stops subsequent tasks in that group and propagates to the caller awaiting or gathering the workers

### Requirement: Pure per-item pending classification in domain.sync
`src/idat_sync/domain/sync.py` SHALL define `ContentSyncState(StrEnum)` with members `SYNCED = "Synced"`, `NEW = "New"`, and `OUTDATED = "Outdated"`, and `classify_content(content: Content, local_mtimes: Mapping[Path, int]) -> ContentSyncState` returning `ContentSyncState.NEW` purely in memory when `content.path not in local_mtimes`, `ContentSyncState.OUTDATED` when `content.last_modified > 0 and content.last_modified > local_mtimes[content.path]`, and `ContentSyncState.SYNCED` otherwise. `is_content_pending` MUST NOT exist in `domain/sync.py`. Verified at the `domain.sync` (`src/idat_sync/domain/sync.py`) seam.

#### Scenario: Classifying missing, outdated, and synced items purely in memory
- **WHEN** `classify_content(content, local_mtimes)` is called for a path absent from `local_mtimes`, a path with `content.last_modified > local_mtimes[path]` (`> 0`), and a path with `content.last_modified <= local_mtimes[path]` or `0`
- **THEN** it returns `ContentSyncState.NEW`, `ContentSyncState.OUTDATED`, and `ContentSyncState.SYNCED` respectively without performing disk I/O, and `hasattr(domain.sync, "is_content_pending")` is `False`

### Requirement: Streaming case-insensitive path disambiguation in PathDisambiguator
`PathDisambiguator` in `src/idat_sync/domain/path_utils.py` SHALL expose `disambiguate(path: Path) -> Path` keyed by `path.as_posix().casefold()` without importing `Content`. First occurrences SHALL stay unchanged; collisions SHALL rename the leaf via `split_extension` and `safe_suffixed_segment(stem, f" ({counter}){ext}")` from `counter = 2` (`<= 100` chars). Verified at `domain.path_utils`.

#### Scenario: Disambiguating case-insensitive and truncated leaf path collisions
- **WHEN** `PathDisambiguator().disambiguate(path)` receives `"Course/Sec/Mod-[1]/Parte 1/guia.pdf"`, `"Course/Sec/Mod-[1]/parte 1/guia.pdf"`, and `"Course/Sec/Mod-[1]/Parte 1/guia.pdf"`, or two 100-character filenames colliding in the same folder
- **THEN** the first keeps its exact `Path`, the second becomes `.../parte 1/guia (2).pdf`, the third becomes `.../Parte 1/guia (3).pdf`, and every leaf segment has length `<= 100`

#### Scenario: Disambiguating long early-dot leaf path collisions without looping
- **WHEN** `PathDisambiguator().disambiguate(path)` receives two 100-character paths whose leaf is `"1." + "x" * 98` in the same folder
- **THEN** the first keeps its exact `Path` and the second terminates immediately as a distinct path ending in `" (2)"` of length `<= 100`

## ADDED Requirements

### Requirement: Non-interactive Colab setup and idempotent Drive post-processing
`colab.ipynb` cell 1 SHALL install `weasyprint` via `%pip install -U` and pass `-y` to every `apt-get install` command. Cell 3 post-processing SHALL check `mtime` before skipping `.mp3` conversion, run all conversions in a first pass, and collect Drive share links in a second pass skipping `.html` and `video/*` sources. Verified at the `colab.ipynb` seam.

#### Scenario: Running Colab setup cell non-interactively with Python weasyprint
- **WHEN** cell 1 of `colab.ipynb` is inspected
- **THEN** `%pip install -U` includes `weasyprint`, no `apt install weasyprint` command exists, and every `apt`/`apt-get install` command includes `-y` (`!apt-get install -y xattr`)

#### Scenario: Converting updated HTML and video files before collecting deduplicated Drive links
- **WHEN** `colab.ipynb` cell 3 post-processing runs on a folder containing an `.html` file newer than its existing `.pdf` and a video file newer than its existing `.mp3`
- **THEN** the first pass compares `os.path.getmtime(path) <= os.path.getmtime(out_path)` for both `.pdf` and `.mp3` and regenerates stale outputs, and the second pass emits each `.pdf` share link once under `links` and each `.mp3` share link once under `recordings` while skipping `.html` and `video/*` source files

### Requirement: CI quality gate before desktop and Android release builds
`.github/workflows/build_and_deploy.yml` SHALL run a `check` job on `ubuntu-latest` (`python -m unittest`, `ruff check .`, `pylint src tests`) for `push` and `pull_request` to `main`, `v*` tags, and `workflow_dispatch`, and gate both `build` and `build-android` behind `needs: check` on tags and manual runs only. Verified at the `.github/workflows/build_and_deploy.yml` workflow seam.

#### Scenario: Gating release builds and validating pushes and pull requests to main
- **WHEN** `.github/workflows/build_and_deploy.yml` is parsed
- **THEN** `on` includes `push` (`branches: [main]`, `tags: ["v*"]`), `pull_request` (`branches: [main]`), and `workflow_dispatch`, the `check` job runs on `ubuntu-latest` executing `python -m unittest`, `ruff check .`, and `pylint src tests`, and both `build` and `build-android` declare `needs: check` with `if: github.ref_type == 'tag' || github.event_name == 'workflow_dispatch'`

### Requirement: Standard-library unittest suite without undeclared pytest dependency
All Python test modules under `tests/` SHALL have zero `pytest` imports and SHALL use `unittest.TestCase` exception assertions (`self.assertRaises` / `self.assertRaisesRegex`) so `python -m unittest` runs cleanly using only the dependencies declared in `pyproject.toml`. Verified at the `tests/` AST and `python -m unittest` seams.

#### Scenario: Running the full test suite with zero pytest imports
- **WHEN** all `tests/*.py` files are parsed via AST and `python -m unittest` runs
- **THEN** zero modules under `tests/` import `pytest`, and `python -m unittest` discovers and passes the full suite without `pytest` installed
