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
- **`application/`**: Use-case orchestration (`AuthService`, `CourseRepository`, `DownloadManager`, `TaskOrchestrator`, `IDATSync`) and driven ports (`application/ports.py`: `CourseGateway`, `RecordingGateway`, `UrlContentStrategy`, `Downloader[C]`, `DownloadTask[C]`, `FileSystemPort`, `MoodleTokenPort`, `SharepointAuthPort`, `CredentialStorePort`, and typed credential shapes `Credentials`, `StorageStateData`, `CookieData`). Depends strictly on `domain/`; zero imports from `infrastructure/` or `ui/`.
- **`infrastructure/`**: Adapters and clients (`MoodleClient`, `MoodleTokenClient`, `MoodleAdapter`, `MoodleTokenAdapter`, `SharepointClient`, `SharepointApiAdapter`, `SharepointBrowserAuth`, `JsonCredentialStore`, `LocalFileSystemAdapter`, `*Downloader`) implementing application ports. All network/API transport, vendor SDKs (`requests`, `office365`, `yt_dlp`, `playwright` confined to `infrastructure/auth.py`), auth persistence, and filesystem I/O.
- **`ui/`**: Tkinter views and event-loop glue. Calls `bootstrap.create_sync_service` and `bootstrap.create_auth_service`, and uses `AuthService` and `Credentials` from `application/`; zero imports from `infrastructure/` (never calls clients, adapters, or downloaders directly).
- **Filesystem boundary**: `application/` has zero direct filesystem I/O (`os`, `stat`, `rglob`, `mkdir`); all local file scanning and directory creation are delegated to `FileSystemPort` (`LocalFileSystemAdapter`).
