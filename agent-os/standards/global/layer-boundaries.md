# Layer Boundaries

Organize code into four packages under `src/idat_sync/`: `domain/`, `application/`, `infrastructure/`, and `ui/`, with `bootstrap.py` as the composition root.

```
 ui/ ──► application/ ◄── infrastructure/
            │
            ▼
         domain/
            ▲
            │
   bootstrap (Composition Root) ──► wires all layers
```

- **`domain/`**: Pure dataclasses, enums, and value helpers (e.g., `Content.in_folder`, `safe_filename`, `SyncDiff.from_trees`). Zero imports from `application/`, `infrastructure/`, `ui/`, or third-party SDKs (`requests`, `office365`, `yt_dlp`, `playwright`).
- **`application/`**: Use-case orchestration (`CourseRepository`, `DownloadManager`, `TaskOrchestrator`, `IDATSync`) and driven ports (`application/ports.py`: `CourseGateway`, `RecordingGateway`, `UrlContentStrategy`, `Downloader[C]`, `DownloadTask[C]`, `FileSystemPort`). Depends strictly on `domain/`; zero imports from `infrastructure/` or `ui/`.
- **`infrastructure/`**: Adapters and clients (`MoodleAdapter`, `SharepointApiAdapter`, `LocalFileSystemAdapter`, `*Downloader`) implementing application ports. All network/API transport, vendor SDKs (`requests`, `office365`, `yt_dlp`, `playwright`), auth persistence, and filesystem I/O.
- **`ui/`**: Tkinter views and event-loop glue. Calls `bootstrap.create_sync_service` and `AuthProvider`; never calls clients, adapters, or downloaders directly.
- **Filesystem boundary**: `application/` has zero direct filesystem I/O (`os`, `stat`, `rglob`, `mkdir`); all local file scanning and directory creation are delegated to `FileSystemPort` (`LocalFileSystemAdapter`).
