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

- **`domain/`**: Pure dataclasses, enums, and value helpers (e.g., `Content.in_folder`, `safe_filename`, `PathDisambiguator`, `is_content_pending`). Zero imports from `application/`, `infrastructure/`, `ui/`, or third-party SDKs (`httpx`, `requests`, `office365`, `yt_dlp`, `playwright`, `webview`, `tkinter`).
- **`application/`**: Use-case orchestration (`AuthService`, `CourseRepository`, `DownloadManager`, `TaskOrchestrator`, `IDATSync`), driven ports (`application/ports.py`: `CourseGateway`, `RecordingGateway`, `UrlContentStrategy`, `Downloader[C]`, `DownloadTask[C]`, `FileSystemPort`, `MoodleTokenPort`, `SharepointAuthPort`, `CredentialStorePort`, and typed credential shapes `Credentials`, `StorageStateData`, `CookieData`), and port-boundary exceptions (`application/exceptions.py`: `IDATSyncError`, `AuthenticationError`, `CredentialsExpiredError`, `CredentialStoreError`, `RemoteServiceError`, `FileSystemError`, re-exported from `application/ports.py`). Depends strictly on `domain/`; zero imports from `infrastructure/`, `ui/`, or third-party SDKs (`httpx`, `requests`, `office365`, `yt_dlp`, `playwright`, `webview`, `tkinter`). `TaskOrchestrator` has zero imports from `application/events.py` or `threading`.
- **`infrastructure/`**: Adapters and clients (`MoodleClient`, `MoodleTokenClient`, `MoodleAdapter`, `MoodleTokenAdapter`, `SharepointClient`, `SharepointApiAdapter`, `SharepointBrowserAuth`, `JsonCredentialStore`, `LocalFileSystemAdapter`, `*Downloader`) implementing application ports and translating vendor/transport/OS failures into `IDATSyncError` subclasses. All network/API transport (`httpx`, `office365` with `HttpxTransport`), vendor SDKs (`yt_dlp`, native `WebViewDriver` / `pywebview`, with `webview` isolated to `ui/app.py` and `infrastructure/sharepoint_auth.py`), auth persistence, and filesystem I/O; zero `requests`, `playwright`, or `tkinter` imports across the package.
- **`ui/`**: Pure-Python UI controllers and `pywebview` + bundled Astro (`src/idat_sync/ui/index.html`) event-loop glue (`MainApp`). Calls `bootstrap.create_sync_service` and `bootstrap.create_auth_service`, and uses `AuthService`, `Credentials`, and `IDATSyncError` (`CredentialsExpiredError`) from `application/`; zero imports from `infrastructure/` (never calls clients, adapters, or downloaders directly, and never catches vendor exceptions).
- **Filesystem boundary**: `application/` has zero direct filesystem I/O (`os`, `stat`, `rglob`, `mkdir`); all local file scanning and directory creation are delegated to `FileSystemPort` (`LocalFileSystemAdapter`).
