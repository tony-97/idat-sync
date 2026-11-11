# Two-Tier Client & Anti-Corruption Adapter

Split every external service integration in `infrastructure/` into a low-level `*Client` and a domain-facing `*Adapter`.

```python
# 1. *Client: Raw transport, auth, rate-limiting (REQUEST_DELAY), raw payloads
class MoodleClient:
    def fetch_enrolled_courses(self) -> list[dict]: ...

# 2. *Adapter: Anti-Corruption Layer mapping raw vendor data -> domain models
class MoodleAdapter:
    def __init__(self, client: MoodleClient) -> None:
        self.client = client

    def list_courses(self) -> list[Course]:
        return [self._to_course(raw) for raw in self.client.fetch_enrolled_courses()]
```

- **`*Client` responsibilities**: HTTP/SDK transport, auth tokens/cookies, `time.sleep(REQUEST_DELAY)` rate-limiting, and raising transport/vendor exceptions. Returns raw vendor dicts or SDK objects; zero `domain` imports.
- **`*Adapter` responsibilities**: Wraps `*Client` via constructor injection, exposes `can_handle(url)` for routing, and translates raw payloads into `domain/` dataclasses via private `_to_*` mappers.
- **No vendor leakage**: Adapter public methods must return `domain/` models (`Course`, `CourseSection`, `Assignment`, `Content`), never raw dicts or SDK entities (`SharepointApiAdapter.fetch_recordings` returning SDK `File` objects is legacy debt — do not repeat).
