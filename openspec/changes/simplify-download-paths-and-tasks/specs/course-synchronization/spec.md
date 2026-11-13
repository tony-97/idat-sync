# Spec Delta

## ADDED Requirements

### Requirement: Unconditional target file path in RecordingDownloader
`RecordingDownloader.download(content: RecordingContent)` SHALL unconditionally treat `content.path` as the target file path by creating `Path(content.path).parent` (`mkdir(parents=True, exist_ok=True)`) and setting `yt_dlp` `outtmpl` to `f"{prefix}{os.path.abspath(Path(content.path))}"`. `RecordingDownloader.download` MUST NOT branch on `Path(content.path).suffix` or append `"%(title)s %(id)s.%(ext)s"`. Verified at the `RecordingDownloader` public seam.

#### Scenario: Downloading a recording with a file extension
- **WHEN** `RecordingDownloader.download(content)` is called with `RecordingContent(Path("Grabaciones/Semana 1/video.mp4"), 1700000000, url)`
- **THEN** it creates the parent directory `Grabaciones/Semana 1` and passes `f"{prefix}{os.path.abspath(Path('Grabaciones/Semana 1/video.mp4'))}"` as `outtmpl` to `yt_dlp.YoutubeDL`

#### Scenario: Downloading a recording without a file extension in path
- **WHEN** `RecordingDownloader.download(content)` is called with a `RecordingContent` whose `path` has no file suffix (`Path(content.path).suffix == ""`)
- **THEN** it still creates `Path(content.path).parent` (not `Path(content.path)` as a directory) and passes `f"{prefix}{os.path.abspath(Path(content.path))}"` as `outtmpl` without appending `"%(title)s %(id)s.%(ext)s"`

## MODIFIED Requirements

### Requirement: Mandatory FileSystemPort delegation in DownloadManager
`DownloadManager` SHALL require `FileSystemPort` as a non-nullable constructor dependency and delegate all local directory tree modification timestamp scans to `FileSystemPort.scan_mtimes`. `DownloadManager.prepare_download_tasks(contents: Sequence[Content]) -> list[DownloadTask]` SHALL accept only `contents` and map each `Content` to a `DownloadTask` via `create_download_task` without performing filesystem queries. `DownloadManager` MUST NOT define `should_download`, accept `root_dir` in `prepare_download_tasks`, or perform direct filesystem I/O (`os`, `stat`, `rglob`, `mkdir`). `FileSystemPort` and `LocalFileSystemAdapter` MUST NOT define `exists` or `get_mtime`, and `DownloadTask` MUST NOT define `last_modified`. Verified at the `DownloadManager` and `FileSystemPort` / `LocalFileSystemAdapter` public seams.

#### Scenario: Scanning local tree modification times
- **WHEN** `DownloadManager.diff_trees(root_dir, remote_contents)` is called
- **THEN** it delegates scanning to `file_system.scan_mtimes(Path(root_dir))` without calling `Path.rglob` or `Path.stat` directly

#### Scenario: Determining whether a task needs download
- **WHEN** `DownloadManager.prepare_download_tasks(contents)` evaluates a sequence of `Content` items (such as `diff.pending_contents`)
- **THEN** it maps each `Content` to a `DownloadTask` via `create_download_task` without querying `file_system`, and rejects any `root_dir` argument with `TypeError`

#### Scenario: Enforcing minimal FileSystemPort and DownloadTask surface
- **WHEN** `FileSystemPort`, `LocalFileSystemAdapter`, `DownloadManager`, and `DownloadTask` are inspected
- **THEN** `FileSystemPort` and `LocalFileSystemAdapter` expose only `scan_mtimes` and `ensure_dir` (neither `exists` nor `get_mtime`), `DownloadManager` does not expose `should_download`, and `DownloadTask` exposes `path` and `download` without `last_modified`
