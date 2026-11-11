# Composition Root & Testable Seams

Wire the production object graph solely in `IDATSync` (`application/sync_service.py`). All other application and adapter classes must receive collaborators via constructor injection.

```python
# Production wiring in IDATSync.__init__
self.moodle_client = MoodleClient(credentials.get("token"))
self.moodle_adapter = MoodleAdapter(self.moodle_client)
self.course_repository = CourseRepository(self.moodle_adapter, self.sharepoint_client)

# Seam testing with spec-constrained mocks + real domain objects
mock_moodle = MagicMock(spec=MoodleAdapter)
repo = CourseRepository(moodle_adapter=mock_moodle, sharepoint_adapter=mock_sp)
```

- **Single composition root**: Only `IDATSync` instantiates concrete clients, adapters, and downloader groups from `Credentials`.
- **Pure constructors**: `CourseRepository`, `DownloadManager`, `TaskOrchestrator`, and `*Adapter` classes must only store injected dependencies in `__init__` — never instantiate clients from raw tokens/cookies or perform network I/O in `__init__`.
- **Confirmed test seams**:
  - `*Client` tests: patch HTTP/SDK transport (`requests.post`, `time.sleep`).
  - `*Adapter` tests: inject `MagicMock(spec=*Client)` and assert outputs are `domain/` dataclasses (not `dict`).
  - `CourseRepository` tests: inject `MagicMock(spec=*Adapter)` and pass real `Course` trees.
  - Sync pipeline tests (`DownloadManager`, `TaskOrchestrator`): use real instances, real `Content` objects, and `tempfile.TemporaryDirectory()` — never mock internal application logic or domain models.
