# Two-Tier Client & Anti-Corruption Adapter

Split every external service integration in `infrastructure/` into a low-level `*Client` and a domain-facing `*Adapter` implementing application ports.

```python
# 1. *Client: Raw transport, auth, rate-limiting (REQUEST_DELAY), raw payloads
class MoodleClient:
    def fetch_enrolled_courses(self) -> list[dict]: ...

# 2. *Adapter: Anti-Corruption Layer mapping raw vendor data -> domain models
class MoodleAdapter(CourseGateway, UrlContentStrategy):
    def __init__(self, client: MoodleClient) -> None:
        self.client = client

    def list_courses(self) -> list[Course]:
        return [self._to_course(raw) for raw in self.client.fetch_enrolled_courses()]
```

- **`*Client` responsibilities**: HTTP/SDK transport, auth tokens/cookies, `time.sleep(self.request_delay)` rate-limiting via injected `request_delay`, and raising transport/vendor exceptions. Returns raw vendor dicts or SDK objects; zero `domain` imports.
- **`*Adapter` responsibilities**: Wraps `*Client` via constructor injection, implements driven ports (`CourseGateway`, `RecordingGateway`, `UrlContentStrategy`), and translates raw payloads into `domain/` dataclasses via private `_to_*` mappers.
- **Transformations within Adapters**:
  - `MoodleAdapter` converts section summaries, label module descriptions, and assignment intros into `inline_contents: list[StringContent]` and merges `introattachments` into `CourseModule.contents: list[ModuleContent]`.
  - `SharepointApiAdapter` accepts `(client: SharepointClient, semester_start: date, sharepoint_host: str, sharepoint_personal_host: str, recordings_folder_name: str, week_folder_prefix: str, request_delay: float)` without instantiating clients or reading cookies in `__init__`, and `fetch_recordings` returns `list[RecordingContent]` with computed `{week_folder_prefix} {week}` paths.
- **No vendor leakage**: Adapter public methods must return `domain/` models (`Course`, `CourseSection`, `CourseModule`, `ModuleContent`, `Content`), never raw dicts or SDK entities. Domain classes must never declare vendor-specific fields (`modname`, `description`, `summary`, or `Assignment`).
