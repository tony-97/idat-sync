# SharePoint Guest URL Redemption, Form Digest Caching, and Single-Query Rate Limiting

## Context & Originating Bug

During synchronization with a fresh login (`session.json`), `SharepointClient._load_entity_at_url` raised `CredentialsExpiredError: HTTP 403: SharePoint credentials expired.` inside `load_entity` -> `_await_query` when resolving guest sharing URLs such as:
- Guest URL: `https://idat628.sharepoint.com/:b:/s/MATERIALESTED-ACADEMICOIDAT/Efab5aontu5Jj1GKPiGf8DsBr1FpKyMlPlJJ_TSR6L-ypA?e=MnLmPH`
- Site `ClientContext.base_url`: `https://idat628.sharepoint.com/sites/MATERIALESTED-ACADEMICOIDAT`
- Resolved entity `ServerRelativeUrl`: `/sites/MATERIALESTED-ACADEMICOIDAT/Documentos_2025/IDAT/ESCUELA/VERSION_2024/TECNOLOGIAS/4_AÑOS/Desarrollo de Sistemas Front-End y Back-End/IV CICLO/Patrones de Diseño de Software/1. Sílabo/1. Sílabo_Idat_Patrones de Diseño de Software_08092025.pdf`

At the time of the exception in the debugger, `entity` already had `Name`, `ServerRelativeUrl`, `TimeLastModified`, and `UniqueId` populated from the guest URL response, yet `load_entity` failed with HTTP 403 (`office365.runtime.exceptions.AccessDeniedException: System.UnauthorizedAccessException`).

---

## Primary-Source Findings (`office365-rest-python-client >= 3.3.1` & SharePoint REST API)

### 1. Why `GetFileByGuestUrl` Succeeded While `GetFileById` Failed with HTTP 403 (`ensureAccess=True`)

1. **Two queries queued in one `execute_query_with_incremental_retry_async()` call**:
   - `ctx.web.get_file_by_guest_url(url)` (`office365/sharepoint/webs/web.py:1331`) queues **Query 0**: `ServiceOperationQuery(self, "GetFileByGuestUrl", None, {"guestUrl": guest_url}, None, return_type)` (`POST .../_api/Web/GetFileByGuestUrl`).
   - `ctx.load(entity, self.properties_to_retrieve)` (`office365/runtime/client_runtime_context.py:486`) queues **Query 1**: `ReadEntityQuery(entity, ["Name", "ServerRelativeUrl", "TimeLastModified"])`.
2. **Resource path rewrite on `UniqueId` hydration**:
   - When Query 0 completes, `File.set_property("UniqueId", value)` (`office365/sharepoint/files/file.py:1413–1414`) sets:
     ```python
     self._resource_path = self.context.web.get_file_by_id(value).resource_path
     ```
   - Consequently, Query 1 (`ReadEntityQuery`) and any subsequent `file.download_session_async(dest, use_path=False)` (`GET .../_api/Web/GetFileById('<UniqueId>')/$value`) address the file via `/Web/GetFileById('<UniqueId>')` **without** the sharing link token.
3. **SharePoint sharing link redemption (`ensureAccess`)**:
   - On `/sites/MATERIALESTED-ACADEMICOIDAT`, older course files reside in `Documentos compartidos`, where students have library-wide Read permissions (so `GetFileById` works even if the sharing link was never redeemed).
   - Newer 2025 course files reside in `Documentos_2025` (and some teachers share files from their personal OneDrive `/personal/<teacher>_idat_pe/Documents/...`), where students do **not** have library-wide Read permissions and only gain item-level `LimitedAccess` once the tokenized sharing link is redeemed.
   - `GetFileByGuestUrl` / `GetFolderByGuestUrl` resolve metadata using the link token **without** granting `LimitedAccess` to the calling user (`ensureAccess` defaults to `false`).
   - `GetFileByGuestUrlExtended` (`ctx.web.get_file_by_guest_url_extended(url, ensure_access=True)`, `office365/sharepoint/webs/web.py:1344`) and `GetFolderByGuestUrlExtended` (`ctx.web.get_folder_by_guest_url_extended(url, ensure_access=True)`, `office365/sharepoint/webs/web.py:1439`) send:
     ```json
     {
       "guestUrl": "<url>",
       "requestSettings": { "EnsureAccess": true, "Password": null }
     }
     ```
     which redeems the sharing link and grants the signed-in user `LimitedAccess` to the underlying item so subsequent `GetFileById('<UniqueId>')/$value` downloads and folder listings (`get_files`, `get_folders`) succeed with HTTP 200.
   - Note: SharePoint REST API has `GetFileByGuestUrlEnsureAccess`, but does **not** have `GetFolderByGuestUrlEnsureAccess` (returns `404 ResourceNotFoundException`); `GetFileByGuestUrlExtended` and `GetFolderByGuestUrlExtended` both exist and accept `ensure_access=True`.

---

### 2. Historical Comparison with Commit `5d06c81d2ecc8222fcbf46ba1969ee5aed843c8a`

Inspecting `5d06c81d2ecc8222fcbf46ba1969ee5aed843c8a:src/idat_sync/idat_sync.py` (lines 184–246) and `5d06c81d2ecc8222fcbf46ba1969ee5aed843c8a:pyproject.toml`:
1. **Folder guest URLs (`:f:`)**:
   - In `5d06c81`, folder guest URLs ran `folder = ctx.web.get_folder_by_guest_url(share_url).execute_query_with_incremental_retry()` (1 query, no `ctx.load`).
   - `ctx.load` was only added to folder guest URLs when `__load_entity` / `load_entity` was unified in commit `2a1d9ec`.
2. **File guest URLs (`:b:`, etc.)**:
   - In `5d06c81`, file guest URLs already called `file = ctx.web.get_file_by_guest_url(share_url); ctx.load(file, ["Name"]); ctx.execute_query_with_incremental_retry()`.
3. **Why `5d06c81` did not abort discovery with `CredentialsExpiredError`**:
   - `5d06c81` checked `if Path(os.path.join(folder, file_name)).exists(): continue` using Moodle's filename before calling SharePoint, whereas `CourseRepository.stream_course_content` resolves all SharePoint URLs during discovery to obtain the real SharePoint filename and timestamp before `is_content_pending`.
   - `5d06c81` did not map HTTP 403 `ClientRequestException` to `CredentialsExpiredError` (introduced in commits `044a70d` and `ea4c259`).
   - `5d06c81` used `office365-rest-python-client @ 8268769c` (v2.x), whereas current `HEAD` uses `office365-rest-python-client[httpx] >= 3.3.1`.

---

### 3. Three Sources of Extra Un-Rate-Limited HTTP Requests in `office365-rest-python-client >= 3.3.1`

Because `SharepointClient._await_query` acquires `async with self.rate_limiter:` once per `execute_query*` / `download_session_async` call, any operation that triggers multiple HTTP requests internally sends back-to-back requests to SharePoint without rate-limiter spacing:

#### A. Redundant `ctx.load(entity, self.properties_to_retrieve)` on Guest URLs
- `ctx.web.get_file_by_id(file_id)` does **not** queue a query on `ctx` (it only returns a `File` with `ServiceOperationPath("GetFileById", [file_id])`), so `fetch_file_by_id` must call `ctx.load(file, self.properties_to_retrieve)` to queue a `ReadEntityQuery`.
- By contrast, `get_file_by_guest_url_extended` and `get_folder_by_guest_url_extended` **already** queue a `ServiceOperationQuery` whose single response populates `Name`, `ServerRelativeUrl`, `TimeLastModified`, and `UniqueId`.
- Calling `ctx.load(entity, self.properties_to_retrieve)` inside `load_entity` for guest URLs queued a **2nd redundant HTTP request** (`GET .../_api/Web/GetFileById('...')?$select=Name,ServerRelativeUrl,TimeLastModified`) in the same `_await_query` slot.
- Fix: `load_entity` calls `entity.select(self.properties_to_retrieve)` (which sets query select options without queuing a `ReadEntityQuery`), while only `fetch_file_by_id` calls `ctx.load(file, self.properties_to_retrieve)`.

#### B. Unconditional `POST _api/contextInfo` (`SharePointRequest.ensure_form_digest`) on Every New `ClientContext`
- In `office365/sharepoint/request.py:73`, `SharePointRequest.__init__` registers `self.beforeExecute += self.ensure_form_digest`.
- `ensure_form_digest` (`office365/sharepoint/request.py:188–191`) does not check `request.method`, so it fetches `POST {site_url}/_api/contextInfo` even on `HttpMethod.Get` requests (which never require `X-RequestDigest`).
- Furthermore, because `SharepointClient.get_client(site_url)` creates a fresh `ClientContext(site_url)` per call (to keep pending query queues isolated across concurrent tasks), `pending._ctx_web_info` started as `None` on every call — causing `POST {site_url}/_api/contextInfo` to be re-fetched on every single guest URL even though a SharePoint `ContextWebInformation` form digest is valid for 30 minutes (`FormDigestTimeoutSeconds = 1800`).
- Fix: In `SharepointClient.get_client(site_url)`:
  ```python
  pending = ctx.pending_request()
  pending.with_async_transport(self._transport)
  pending.beforeExecute -= pending.ensure_form_digest
  pending.beforeExecute += partial(self._ensure_form_digest, pending, site_url)
  ```
  where `_ensure_form_digest` returns immediately when `request.method == HttpMethod.Get`, and for non-`GET` requests caches and reuses valid `ContextWebInformation` instances in `self._web_infos[site_url]` across `ClientContext` instances.
- Gotcha: Do **not** call `pending.with_transport(self._transport)` on `ClientRequest` — in `office365/runtime/client_request.py`, `with_transport(proxies=..., verify=...)` configures proxy dicts on `RequestsTransport`, not a `BaseTransport` instance.

#### C. Extra `GET ...?$select=Id` in `File.download_session_async`
- In `office365/sharepoint/files/file.py:1037–1039`, `File.download_session_async(dest, use_path=False)` runs:
  ```python
  self.ensure_property("ServerRelativePath" if use_path else "ServerRelativeUrl")
  if self.context.has_pending_request:
      await self.context.execute_query_async()
  ```
- In `office365/runtime/client_object.py:251–254`, `ensure_properties` automatically appends `self.property_ref_name` (`"Id"`, inherited from `office365.sharepoint.entity.Entity`) to the list of properties to check:
  ```python
  if self.property_ref_name is not None and self.property_ref_name not in names:
      names.append(self.property_ref_name)
  names_to_include = [n for n in names if not self.is_property_available(n)]
  ```
- Because SharePoint `SP.File` payloads carry `"UniqueId"` and have no `"Id"` property, `file.is_property_available("Id")` is `False`, causing `download_session_async` to queue and execute a pointless `GET .../_api/Web/GetFileById('...')?$select=Id` before streaming `GET .../_api/Web/GetFileById('...')/$value`.
- When all checked properties are already in `file.properties` (`names_to_include == []`), `ensure_properties` instead queues a `DeferredOperationQuery` (`office365/runtime/queries/deferred.py`), which resolves in memory as a no-op with **0 HTTP requests**.
- Fix: In `SharepointClient.download_file`:
  ```python
  file.set_property("Id", file.unique_id, persist_changes=False)
  await self._await_query(file.download_session_async(dest, use_path=False))
  ```
  This marks `"Id"` as available in `file.properties` so `download_session_async` issues only the single `GET .../$value` stream request.
