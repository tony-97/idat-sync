## ADDED Requirements

### Requirement: ASCII compatibility and edge-case hardening in safe_filename
Per ADR-0002, `safe_filename(s, replacement=" ", max_len=100)` SHALL preserve ASCII NFKD normalization for top-level `Course` directories while checking `WINDOWS_RESERVED` against the stem before extensions and stripping trailing `[ .]+$` after `max_len` truncation. Verified at the `domain.path_utils` seam.

#### Scenario: Preserving ASCII course compatibility while fixing reserved stems and trailing dots
- **WHEN** `safe_filename` receives `"CON.txt"`, `"Lección 1: Diseño"`, or `"a" * 98 + "...b"` with `max_len=100`
- **THEN** it returns `"_CON.txt"`, `"Leccion 1 Diseno"`, and `"a" * 98` (with no trailing dot after truncation and length `<= 100`)

### Requirement: UTF-8 and extension-preserving segment sanitization in safe_path_segment
Per ADR-0002, `safe_path_segment(s, replacement=" ", max_len=100)` SHALL preserve UTF-8 accents and file extensions for `CourseSection`, `CourseModule`, and file names while stripping `<>:"/\|?*`, control characters `\x00-\x1F`/`\x7F`, `.`/`..`, post-truncation `[ .]+$`, and `WINDOWS_RESERVED` stems. Verified at the `domain.path_utils` seam.

#### Scenario: Preserving UTF-8 and extensions while making nested segments OS-safe
- **WHEN** `safe_path_segment` receives `"Lección 1: Diseño/Guía?.pdf"`, `"CON.txt"`, `".."`, or `"a" * 110 + ".mp4"` with `max_len=100`
- **THEN** it returns `"Lección 1 Diseño Guía.pdf"`, `"_CON.txt"`, `"untitled"`, and `"a" * 96 + ".mp4"` (length `<= 100`, UTF-8 and extension intact, no forbidden characters or trailing dots/spaces)

### Requirement: Traversal-safe path containment in Content.in_folder
`Content.in_folder(folder)` in `src/idat_sync/domain/content.py` SHALL fail closed with `ValueError` whenever `self.path` is anchored/absolute (`is_absolute()` or non-empty `.anchor`) or contains `..` in `.parts`, or whenever `folder` contains `..` in `.parts`, ensuring relative paths MUST NOT escape `course_dir`. Verified at the `Content.in_folder` seam.

#### Scenario: Rejecting anchored paths and parent traversal segments
- **WHEN** `Content(Path("/etc/passwd"), 0).in_folder("Course")`, `Content(Path("../secret.txt"), 0).in_folder("")`, or `Content(Path("file.pdf"), 0).in_folder("../outside")` is called
- **THEN** each call raises `ValueError` instead of discarding `folder` or resolving above `course_dir`

#### Scenario: Allowing an absolute base course directory with safe relative content paths
- **WHEN** `Content(Path("file.pdf"), 0).in_folder(Path("/tmp/Course"))` is called
- **THEN** it returns a `Content` whose `path` equals `Path("/tmp/Course/file.pdf")`

### Requirement: Credentials expiration mapping in Moodle, SharePoint, and recording layers
`MoodleClient.call_ws`, `MoodleClient.download_file`, `SharepointClient`, and `RecordingDownloader.download` SHALL map HTTP `401`, HTTP `403`, Moodle `errorcode == "invalidtoken"`, and empty `personal_site.url` into `CredentialsExpiredError`, while preserving non-auth exceptions unchanged. Verified at the `MoodleClient`, `SharepointClient`, and `RecordingDownloader` seams.

#### Scenario: Mapping Moodle invalidtoken and HTTP 401 or 403 to CredentialsExpiredError
- **WHEN** `MoodleClient.call_ws` receives `{"exception": "moodle_exception", "errorcode": "invalidtoken", ...}` or HTTP `401`/`403`, or `MoodleClient.download_file` receives HTTP `401`/`403`
- **THEN** `MoodleClient` raises `CredentialsExpiredError` (which is also a `RuntimeError`), while HTTP `500` or non-auth Moodle exceptions raise `requests.HTTPError` or `RuntimeError`

#### Scenario: Mapping SharePoint and recording auth failures to CredentialsExpiredError
- **WHEN** `SharepointClient` observes empty `personal_site.url` or an SDK HTTP `401`/`403` response, or `RecordingDownloader.download` fails with an HTTP `401`/`403` download error
- **THEN** both raise `CredentialsExpiredError`, while non-auth `404`/`500` errors propagate without masking

### Requirement: Per-item failure isolation and early credential abort in TaskOrchestrator
`TaskOrchestrator` SHALL preserve sequential task execution order within each downloader group, log and skip individual `DownloadTask` failures other than `CredentialsExpiredError`, and immediately abort remaining queued tasks across all downloader groups and propagate `CredentialsExpiredError`. Verified at the `TaskOrchestrator` seam.

#### Scenario: Continuing remaining tasks after a single-item download failure
- **WHEN** a `DownloadTask` raises a non-credential `Exception` (such as `requests.HTTPError` `404` or `OSError`) during `TaskOrchestrator.run`
- **THEN** the orchestrator logs the failure, executes all subsequent tasks in that downloader group and sibling groups in order, and returns without raising

#### Scenario: Aborting all remaining tasks on CredentialsExpiredError
- **WHEN** any `DownloadTask` raises `CredentialsExpiredError` during `TaskOrchestrator.run`
- **THEN** remaining unstarted tasks in that group and sibling groups MUST NOT run, and `CredentialsExpiredError` propagates to the caller

### Requirement: Per-course failure isolation and early credential abort in IDATSync
`IDATSync.sync_courses(root_folder)` SHALL compute each `Course` folder via `Path(root_folder) / safe_filename(course.name)`, log and skip single-`Course` failures other than `CredentialsExpiredError` so later courses still sync, and abort remaining courses immediately on `CredentialsExpiredError`. Verified at the `IDATSync` seam.

#### Scenario: Continuing remaining courses when one course fails
- **WHEN** `sync_course` raises a non-credential error on the first `Course` while a second `Course` succeeds
- **THEN** `IDATSync.sync_courses` logs the first `Course` error, still synchronizes the second `Course`, and finishes without raising

#### Scenario: Aborting remaining courses on CredentialsExpiredError
- **WHEN** the first `Course` raises `CredentialsExpiredError` inside `IDATSync.sync_courses`
- **THEN** subsequent courses MUST NOT be synchronized and `CredentialsExpiredError` propagates immediately

## MODIFIED Requirements

### Requirement: Moodle adapter translates vendor curriculum into domain models
`MoodleAdapter` SHALL map Moodle sections, modules, and assignments into `Course`, `CourseSection`, and `CourseModule` with `inline_contents: list[StringContent]` and `contents: list[ModuleContent]`, sanitizing generated `.html` filenames and resolved `MoodleContent` filenames with `safe_path_segment`. `domain/course.py` MUST NOT define `Assignment`, `Course.assignments`, `CourseSection.summary`, `CourseModule.modname`, or `CourseModule.description`. Verified at the `MoodleAdapter` public seam.

#### Scenario: Generating section summary inline content
- **WHEN** `MoodleAdapter` converts a raw section with `id` and `name` and `summary`
- **THEN** the resulting `CourseSection` has `inline_contents` set to `[StringContent(Path(safe_path_segment(f"{section_name}-[{section_id}]-resumen.html")), 0, [summary])]`

#### Scenario: Mapping label modules with non-empty and empty descriptions
- **WHEN** `MoodleAdapter` converts a raw module with `modname == "label"`
- **THEN** `CourseModule.inline_contents` is `[StringContent(Path(safe_path_segment(f"{module_name}.html")), 0, [description])]` with `contents = []` when `description` is non-empty, and both `inline_contents` and `contents` are `[]` when `description` is empty

#### Scenario: Merging assignment intro and attachments by cmid
- **WHEN** `MoodleAdapter` converts a raw module with `modname == "assign"` and `id` matching an assignment's `cmid`
- **THEN** `CourseModule.inline_contents` is `[StringContent(Path(safe_path_segment(f"{assign_name}-[{assign_id}]-intro.html")), 0, [assign_intro])]` and `CourseModule.contents` contains the mapped `ModuleContent` items from `introattachments` (or both are `[]` when no matching assignment exists)

#### Scenario: Sanitizing unsafe resource and inline filenames while preserving accents
- **WHEN** `MoodleAdapter.list_url_content` or inline content mapping receives `"Guía: Tema 1/2?.pdf"` or label `"Aviso: CON/1"`
- **THEN** the resulting `MoodleContent.path` is `Path("Guía Tema 1 2.pdf")` and the label `StringContent.path` is `Path("Aviso CON 1.html")`

### Requirement: SharePoint adapter projects recordings into weekly domain content
`SharepointApiAdapter` SHALL accept `(client: SharepointClient, semester_start: date, sharepoint_host: str, sharepoint_personal_host: str, recordings_folder_name: str, week_folder_prefix: str)` without `request_delay`, without hardcoded defaults, and without instantiating `SharepointClient` in `__init__`. `SharepointApiAdapter` MUST NOT store `request_delay`, `properties_to_retrieve`, `client`, or `search_client`, and MUST NOT import `time` or `ClientContext`. `fetch_recordings(course_name)` SHALL compute `week = 1 + (file.time_created.date() - self.semester_start).days // 7`, sanitize folder/file path segments with `safe_path_segment`, and return `list[RecordingContent]`. Verified at `SharepointApiAdapter` seam.

#### Scenario: Computing weekly folder and SharePoint stream URL per recording
- **WHEN** `SharepointApiAdapter.fetch_recordings(course_name)` finds recording files with `time_created` and `serverRelativeUrl`
- **THEN** it returns `RecordingContent(Path(safe_path_segment(f"{self.week_folder_prefix} {week}")) / safe_path_segment(name), last_mod, f"https://{self.sharepoint_personal_host}/:v:/r{quote(file.serverRelativeUrl)}?csf=1&web=1")` for each recording inside `self.recordings_folder_name` (matched case-insensitively)

#### Scenario: Skipping recordings missing creation timestamp without shifting subsequent weeks
- **WHEN** `SharepointApiAdapter.fetch_recordings(course_name)` encounters a recording file with `time_created = None` before a valid recording file
- **THEN** the file missing `time_created` is excluded and the subsequent file's `week` is still computed from its own `time_created.date()`

#### Scenario: Customizing SharePoint hosts, recordings folder name, and week prefix
- **WHEN** `SharepointApiAdapter` is initialized with custom `sharepoint_host`, `sharepoint_personal_host`, `recordings_folder_name="Videos"`, and `week_folder_prefix="Week"`
- **THEN** `can_handle` matches `sharepoint_host`, and `fetch_recordings` matches parent folders named `"videos"` and returns `RecordingContent` paths under `Path(f"Week {week}")` with stream URLs on `sharepoint_personal_host`

#### Scenario: Sanitizing unsafe SharePoint folder and file names without losing accents
- **WHEN** `SharepointApiAdapter` maps a recording, file, or subfolder named `"Sesión: 01/Parte?.mp4"` or `"CON"`
- **THEN** emitted `SharepointContent` and `RecordingContent` path segments become `"Sesión 01 Parte.mp4"` and `"_CON"`

### Requirement: CourseRepository orchestrates gateways and URL strategies
`CourseRepository` SHALL accept `(course_gateway: CourseGateway, recording_gateway: RecordingGateway, url_strategies: Sequence[UrlContentStrategy])`. `get_course_content(course, course_dir)` SHALL skip sections and modules with empty `name`, place each module's `inline_contents` before resolved `contents` in `safe_path_segment(module.name)`, prepend `section.inline_contents` before module items in `safe_path_segment(section.name)`, and wrap items in `course_dir`. Verified at the `CourseRepository` public seam.

#### Scenario: Composing course content hierarchy and ordering
- **WHEN** `CourseRepository.get_course_content(course, course_dir)` is called on a `Course` with sections and modules (including unsafe characters such as `:` or `/` in `section.name` or `module.name`)
- **THEN** it emits each section's `inline_contents` followed by each named module's `inline_contents` and URL-resolved `contents`, composed bottom-up via `.in_folder(safe_path_segment(module.name))`, `.in_folder(safe_path_segment(section.name))`, and `.in_folder(course_dir)`

#### Scenario: Wrapping recordings in target folder
- **WHEN** `CourseRepository.get_recordings(course_name, recordings_folder)` is called
- **THEN** it delegates to `recording_gateway.fetch_recordings(course_name)` and returns `[r.in_folder(recordings_folder) for r in recordings]`

### Requirement: Client-owned file download streaming and SharePoint SDK query execution
`MoodleClient` and `SharepointClient` SHALL provide `download_file(..., dest: BinaryIO) -> None` to stream remote files into an open binary stream, sleep `self.request_delay`, and raise `CredentialsExpiredError` on authentication failure. `MoodleDownloader` and `SharepointDownloader` SHALL accept their `*Client`, create parent directories, open `content.path` as `"wb"`, and delegate to `client.download_file`. `SharepointClient` SHALL execute all guest URL, file-by-ID, and folder SDK queries. Verified at client, adapter, and downloader seams.

#### Scenario: Streaming a Moodle file through MoodleClient
- **WHEN** `MoodleClient.download_file(url, dest)` is called with a target URL and writable binary stream
- **THEN** it performs `requests.get(url, params={"wstoken": self.token, "token": self.token}, stream=True, allow_redirects=True, timeout=60, headers=REQUEST_HEADER)`, raises `CredentialsExpiredError("403 Forbidden: your session cookie is missing/expired.")` on HTTP `401` or `403`, calls `raise_for_status()`, writes non-empty `1024 * 256`-byte chunks into `dest`, and calls `time.sleep(self.request_delay)`

#### Scenario: Delegating MoodleDownloader and SharepointDownloader file downloads to clients
- **WHEN** `MoodleDownloader(moodle_client).download(moodle_content)` and `SharepointDownloader(sharepoint_client).download(sp_content)` are called
- **THEN** each stores `self.moodle_client` or `self.sharepoint_client` without accepting `request_delay`, creates `Path(content.path).parent`, opens `content.path` as `"wb"`, and calls `moodle_client.download_file(moodle_content.url, f)` and `sharepoint_client.download_file(sp_content.file, local_file)` respectively (where `SharepointClient.download_file` runs `file.download_session(dest).execute_query_with_incremental_retry()` and sleeps `self.request_delay`)

#### Scenario: Delegating SharePoint guest URLs, direct links, and folder traversal to SharepointClient
- **WHEN** `SharepointApiAdapter.list_url_content`, `fetch_folder_contents`, and `fetch_file_from_direct_link` resolve guest folder URLs, guest file URLs, direct links with `uniqueid`/`sourcedoc`, and folder hierarchies
- **THEN** `SharepointApiAdapter` calls `sp_client.fetch_folder_by_guest_url`, `sp_client.fetch_file_by_guest_url`, `sp_client.fetch_file_by_id(link, file_id)` (skipping the client call and returning `None` when neither `uniqueid` nor `sourcedoc` is present), and calls `sp_client.fetch_folder_files(folder)` before `sp_client.fetch_subfolders(folder)`, while `SharepointClient` executes all `ClientContext` / `execute_query_retry` calls and `time.sleep(self.request_delay)`
