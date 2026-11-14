# course-synchronization Specification Delta

## MODIFIED Requirements

### Requirement: Configurable tenant endpoints and throttling across clients, adapters, and downloaders
`MoodleClient`, `MoodleTokenClient`, `MoodleAdapter`, `SharepointClient`, `RecordingDownloader`, and `SharepointBrowserAuth` SHALL require injected tenant URL/host parameters without hardcoded defaults, and `MoodleClient` and `SharepointClient` SHALL require `request_delay` without hardcoded defaults as the sole owners of `time.sleep(self.request_delay)`. `MoodleTokenClient`, `MoodleDownloader`, `SharepointDownloader`, and `SharepointApiAdapter` MUST NOT accept `request_delay`. `src/idat_sync/infrastructure/downloaders.py` MUST NOT import `requests`, `REQUEST_HEADER`, `RQ_HEADER`, or `time`, `REQUEST_HEADER` SHALL reside in `src/idat_sync/infrastructure/moodle_client.py`, and `src/idat_sync/utils.py` MUST NOT exist. Verified at client, adapter, downloader, and AST seams.

#### Scenario: Respecting injected Moodle and SharePoint hosts and request delays
- **WHEN** `MoodleClient`, `MoodleAdapter`, `SharepointClient`, and `RecordingDownloader` are constructed with custom `base_url`, `moodle_base_url`, `sharepoint_personal_host`, and `request_delay`
- **THEN** `MoodleAdapter.can_handle` matches the hostname of `moodle_base_url`, `RecordingDownloader.can_handle` matches `f"{sharepoint_personal_host}/:v:"`, and `MoodleClient.call_ws`, `MoodleClient.download_file`, `SharepointClient.download_file`, `SharepointClient.fetch_document_library_items`, `SharepointClient.fetch_folder_files`, and `SharepointClient.fetch_subfolders` each sleep once per query for the injected `request_delay`

#### Scenario: Inspecting infrastructure imports and utils cleanup
- **WHEN** all Python modules under `src/idat_sync/infrastructure/` and `src/idat_sync/utils.py` are inspected
- **THEN** `src/idat_sync/utils.py` does not exist, no `infrastructure/` module imports `idat_sync.utils`, `downloaders.py` does not import `requests`, `REQUEST_HEADER`, `RQ_HEADER`, or `time`, `sharepoint_adapter.py` does not import `time` or `ClientContext`, and `REQUEST_HEADER` is defined in `src/idat_sync/infrastructure/moodle_client.py`

### Requirement: Client-owned file download streaming and SharePoint SDK query execution
`MoodleClient` and `SharepointClient` SHALL provide `download_file(..., dest: BinaryIO) -> None`, sleep `self.request_delay`, and raise `CredentialsExpiredError` on auth failure. `MoodleDownloader` and `SharepointDownloader` SHALL delegate to `client.download_file`. `SharepointClient` SHALL execute SDK queries via `.execute_query()` through `_run_query` (retrying HTTP `429` and `503` up to 5 attempts using `Retry-After` or `self.request_delay` backoff and re-raising on exhaustion, while failing immediately without retry on HTTP `401`/`403` or non-throttling errors), execute guest URL, file-by-ID, and folder SDK queries (including `/personal/<user>` sites and `odata.null` `TypeError` recovery), and `SharepointApiAdapter.list_url_content` SHALL dispatch by URL path and skip empty entities. Verified at client, adapter, and downloader seams.

#### Scenario: Streaming a Moodle file through MoodleClient
- **WHEN** `MoodleClient.download_file(url, dest)` is called with a target URL and writable binary stream
- **THEN** it performs `requests.get(url, params={"wstoken": self.token, "token": self.token}, stream=True, allow_redirects=True, timeout=60, headers=REQUEST_HEADER)`, raises `CredentialsExpiredError("403 Forbidden: your session cookie is missing/expired.")` on HTTP `401` or `403`, calls `raise_for_status()`, writes non-empty `1024 * 256`-byte chunks into `dest`, and calls `time.sleep(self.request_delay)`

#### Scenario: Delegating MoodleDownloader and SharepointDownloader file downloads to clients
- **WHEN** `MoodleDownloader(moodle_client).download(moodle_content)` and `SharepointDownloader(sharepoint_client).download(sp_content)` are called
- **THEN** each stores `self.moodle_client` or `self.sharepoint_client` without accepting `request_delay`, creates `Path(content.path).parent`, opens `content.path` as `"wb"`, and calls `moodle_client.download_file(moodle_content.url, f)` and `sharepoint_client.download_file(sp_content.file, local_file)` respectively (where `SharepointClient.download_file` runs `file.download_session(dest, use_path=False).execute_query()` and sleeps `self.request_delay`)

#### Scenario: Delegating SharePoint guest URLs, direct links, and folder traversal to SharepointClient
- **WHEN** `SharepointApiAdapter.list_url_content`, `fetch_folder_contents`, and `fetch_file_from_direct_link` resolve guest folder URLs (`^/:f:/`), guest file URLs (`^/:(\w+):/`) across `sharepoint_host` and `sharepoint_personal_host`, direct links with `uniqueid`/`sourcedoc`, and folder hierarchies
- **THEN** `SharepointApiAdapter` inspects `urlparse(url).path` without repeating the hostname check, calls `sp_client.fetch_folder_by_guest_url`, `sp_client.fetch_file_by_guest_url`, and `sp_client.fetch_file_by_id(link, file_id)` (skipping the client call and returning `None` when neither `uniqueid` nor `sourcedoc` is present), and calls `sp_client.fetch_folder_files(folder)` before `sp_client.fetch_subfolders(folder)`, while `SharepointClient.get_client_from_url` matches `r"/(sites|teams|s|personal)/([^/]+)"` case-insensitively (mapping `/s/<name>` to `{base_url}/sites/<name}` and `/(sites|teams|personal)/<name>` to `{base_url}{path_match.group(0)}`) and sleeps `self.request_delay`

#### Scenario: Skipping dead or expired SharePoint guest and direct links returning odata.null
- **WHEN** `SharepointClient.load_entity(entity, ctx)` encounters `TypeError` because an `odata.null` guest URL response left `entity.resource_url` as `None`, or `SharepointApiAdapter.list_url_content` receives a `Folder` or `File` where `not entity.name and not entity.serverRelativeUrl`
- **THEN** `SharepointClient.load_entity` catches `TypeError` and still sleeps `self.request_delay` (while propagating `CredentialsExpiredError` and non-`TypeError` exceptions unchanged), and `SharepointApiAdapter.list_url_content` returns `[]`

#### Scenario: Retrying SharePoint HTTP 429 and 503 with Retry-After backoff and re-raising on exhaustion
- **WHEN** a SharePoint SDK query inside `SharepointClient._run_query` raises `ClientRequestException` or `requests.HTTPError` with HTTP `429` or `503`
- **THEN** `SharepointClient._run_query` sleeps `int(Retry-After)` when a valid `Retry-After` response header is present (or `self.request_delay` otherwise), retries the query up to 5 total attempts, and re-raises the exception if all 5 attempts fail without retrying HTTP `401`, `403`, or `404`
