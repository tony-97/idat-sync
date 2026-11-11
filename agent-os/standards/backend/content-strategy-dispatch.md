# Content Strategy & Downloader Dispatch

Extend URL resolution and file downloading via strategy dispatch and immutable path composition without modifying `CourseRepository`, `DownloadManager`, or `TaskOrchestrator`.

```python
class CustomDownloader(Downloader[RecordingContent]):
    content_type = RecordingContent

    def can_handle(self, content: RecordingContent) -> bool:
        return "example.com" in content.url

    def download(self, content: RecordingContent) -> None: ...
```

- **Driven Port definitions**: `UrlContentStrategy`, `Downloader[C: Content]`, and `DownloadTask[C: Content]` are defined in `src/idat_sync/application/ports.py`. Adapters implement `UrlContentStrategy` (`can_handle`, `list_url_content`).
- **Bottom-up path composition**: Adapters return `Content` with paths relative to the remote resource root (including recursive subfolders). `CourseRepository` prepends `module.name`, `section.name`, and `course_dir` via `content.in_folder(...)`. Never pass course/section paths into adapters.
- **Generic `Downloader[C: Content]`**: Subclass `Downloader[C]`, set `content_type = C`, override `can_handle(content: C)` when filtering within a content type, and implement `download(content: C)`. `DownloadManager` matches tasks using `isinstance(content, self.content_type) and self.can_handle(content)`.
- **Rate-limit grouping in `TaskOrchestrator`**: Configure `list[list[Downloader]]` by upstream rate-limit boundary. Downloaders hitting the same host/tenant (e.g., `SharepointDownloader` and `RecordingDownloader`) share an inner sequential list to prevent throttling; independent sources go in separate outer lists to run concurrently via `asyncio.to_thread`.
