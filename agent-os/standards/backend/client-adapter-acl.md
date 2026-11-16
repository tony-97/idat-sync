# Two-Tier Client & Anti-Corruption Adapter

Split every external service integration in `infrastructure/` into a low-level `*Client` and domain-facing `*Adapter` / `*Downloader` classes implementing application ports.

```python
# 1. *Client: Raw transport, auth, rate-limiting (request_delay), raw payloads & file streams
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

- **`*Client` responsibilities**: Async HTTP/SDK transport (`httpx.AsyncClient`, `office365` with `HttpxTransport` and `.with_auto_retry()`), auth tokens/cookies, file download streaming (`async def download_file(..., dest: BinaryIO)`), `await asyncio.sleep(self.request_delay)` rate-limiting via injected `request_delay` on `MoodleClient` and `SharepointClient` (single-call pre-auth `MoodleTokenClient(base_url)` omits `request_delay`), and raising transport/vendor exceptions. Returns raw vendor dicts or SDK objects; zero `domain` imports. `SharepointClient.__init__` is non-blocking and `@classmethod async def create(...)` resolves `personal_site`.
- **`*Adapter` & `*Downloader` responsibilities**: Wrap `*Client` via constructor injection (never another `*Adapter`), implement driven ports (`CourseGateway`, `RecordingGateway`, `UrlContentStrategy`, `MoodleTokenPort`, `Downloader[C]`), and delegate all HTTP/SDK queries and `request_delay` throttling to `*Client`. `*Downloader` classes handle local path creation (`mkdir`) and opening the target file stream before calling `*Client.download_file`.
- **Transformations within Adapters**:
  - `MoodleAdapter` converts section summaries, label module descriptions (plus `modname == "url"` entries in `"bibliografia"` sections into `bibliografia.html`), and assignment intros into `inline_contents: list[StringContent]` and merges `introattachments` into `CourseModule.contents: list[ModuleContent]`. It does not expose raw auth tokens (`token`). `MoodleTokenAdapter` wraps `MoodleTokenClient` and translates raw login JSON into `str`, raising `AuthenticationError` with `[!]` diagnostics when login fails.
  - `SharepointApiAdapter` accepts `(client: SharepointClient, semester_start: date, sharepoint_host: str, sharepoint_personal_host: str, recordings_folder_name: str, week_folder_prefix: str, timezone: str)` without `request_delay`, without instantiating clients or reading cookies in `__init__`, and delegates guest URL, direct-link file ID, and folder/subfolder SDK queries to `SharepointClient`. `fetch_recordings` returns `list[RecordingContent]` with computed `{week_folder_prefix} {week}` paths.
- **No vendor leakage**: Adapter public methods must return `domain/` models (`Course`, `CourseSection`, `CourseModule`, `ModuleContent`, `Content`), never raw dicts or SDK entities. Domain classes must never declare vendor-specific fields (`modname`, `description`, `summary`, or `Assignment`).
