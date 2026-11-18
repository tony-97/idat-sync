# Two-Tier Client & Anti-Corruption Adapter

Split every external service integration in `infrastructure/` into a low-level `*Client` and domain-facing `*Adapter` / `*Downloader` classes implementing application ports.

```python
# 1. *Client: Raw transport, auth, rate-limiting (AsyncRateLimiter), raw payloads & file streams
class MoodleClient:
    async def fetch_enrolled_courses(self) -> list[dict]: ...
    async def download_file(self, url: str, dest: BinaryIO) -> None: ...


# 2. *Adapter / *Downloader: Anti-Corruption Layer & port implementations
class MoodleAdapter(CourseGateway, UrlContentStrategy):
    def __init__(self, client: MoodleClient, moodle_base_url: str) -> None:
        self.client = client
        ...


class MoodleDownloader(Downloader[MoodleContent]):
    def __init__(self, client: MoodleClient) -> None:
        self.moodle_client = client
```

- **`*Client` responsibilities**: Async HTTP/SDK transport (`httpx.AsyncClient`, `office365` with `HttpxTransport` and `.with_auto_retry()`), auth tokens/cookies, file download streaming (`async def download_file(..., dest: BinaryIO)`), end-to-start `AsyncRateLimiter` (`async with self.rate_limiter:`) via injected `rate_limiter: AsyncRateLimiter` on `MoodleClient` and `SharepointClient` (single-call pre-auth `MoodleTokenClient(base_url)` omits `rate_limiter`), and translating transport/SDK errors at the boundary: `MoodleClient` and `SharepointClient` map auth failures (`401`/`403`, `invalidtoken`, empty `personal_site.url`, uninitialized `search_client`) to `CredentialsExpiredError` and all non-auth HTTP/WS/SDK/JSON errors to `RemoteServiceError` (with `MoodleClient.download_file` stripping `wstoken`/`token` and raising `RemoteServiceError(...) from None`), while `MoodleTokenClient` maps HTTP/request/JSON failures to `RemoteServiceError`. Returns raw vendor dicts or SDK objects; zero `domain` imports. `SharepointClient.__init__` is non-blocking and `@classmethod async def create(...)` resolves `personal_site`.
- **`*Adapter`, `*Downloader`, & Store responsibilities**: Wrap `*Client` via constructor injection (never another `*Adapter`), implement driven ports (`CourseGateway`, `RecordingGateway`, `UrlContentStrategy`, `MoodleTokenPort`, `SharepointAuthPort`, `CredentialStorePort`, `FileSystemPort`, `Downloader[C]`), and delegate all HTTP/SDK queries and rate-limiting to `*Client` (while `RecordingDownloader` shares `SharepointClient`'s `AsyncRateLimiter`). `MoodleTokenAdapter` and `SharepointBrowserAuth` raise `AuthenticationError` on rejected credentials, blank MFA, Playwright `TimeoutError`, or browser failures; `RecordingDownloader` maps `401`/`403` `DownloadError` to `CredentialsExpiredError`, other `DownloadError` to `RemoteServiceError`, and `OSError` to `FileSystemError`; `JsonCredentialStore.save`/`.clear` raise `CredentialStoreError` on `OSError` (while `load()` absorbs `(OSError, json.JSONDecodeError)` into `None`); and `LocalFileSystemAdapter.scan_mtimes`/`.ensure_dir` and `_atomic_download` map `OSError` to `FileSystemError` (while `LocalFileSystemAdapter.read_bytes()` returns `None`). `*Downloader` classes handle local path creation (`mkdir`) and opening the target file stream before calling `*Client.download_file`.
- **Transformations within Adapters**:
  - `MoodleAdapter` converts section summaries, label module descriptions (plus `modname == "url"` entries in `"bibliografia"` sections into `bibliografia.html`), and assignment intros into `inline_contents: list[StringContent]` and merges `introattachments` into `CourseModule.contents: list[ModuleContent]`. It does not expose raw auth tokens (`token`). `MoodleTokenAdapter` wraps `MoodleTokenClient` and translates raw login JSON into `str`, raising `AuthenticationError` with `[!]` diagnostics when login fails and letting `RemoteServiceError` propagate.
  - `SharepointApiAdapter` accepts `(client: SharepointClient, semester_start: date, sharepoint_host: str, sharepoint_personal_host: str, recordings_folder_name: str, week_folder_prefix: str, timezone: str)` without `request_delay`, without instantiating clients or reading cookies in `__init__`, and delegates guest URL, direct-link file ID, and folder/subfolder SDK queries to `SharepointClient`. `fetch_recordings` returns `list[RecordingContent]` with computed `{week_folder_prefix} {week}` paths.
- **No vendor leakage**: Adapter public methods must return `domain/` models (`Course`, `CourseSection`, `CourseModule`, `ModuleContent`, `Content`), never raw dicts or SDK entities. Domain classes must never declare vendor-specific fields (`modname`, `description`, `summary`, or `Assignment`).
