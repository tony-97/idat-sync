# Layer Boundaries

Organize code into four packages under `src/idat_sync/`: `domain/`, `application/`, `infrastructure/`, and `ui/`.

```
ui/ ──► application/ ──► infrastructure/
           │                    │
           ▼                    ▼
        domain/ ◄───────────────┘
```

- **`domain/`**: Pure dataclasses, enums, and value helpers (e.g., `Content.in_folder`). Zero imports from `application`, `infrastructure`, `ui`, or third-party SDKs. Type vendor handles as `Any` (e.g., `SharepointContent.file: Any`).
- **`application/`**: Use-case orchestration (`CourseRepository`, `DownloadManager`, `TaskOrchestrator`, `IDATSync`). May import adapter/downloader classes from `infrastructure/` directly without separate `Protocol` ports.
- **`infrastructure/`**: All network/API transport, vendor SDKs (`requests`, `office365`, `yt_dlp`, `playwright`), auth persistence, and file content writes.
- **`ui/`**: Tkinter views and event-loop glue. Calls `IDATSync` and `AuthProvider`; never calls clients, adapters, or downloaders directly.
- **Filesystem boundary**: Read-only path/stat checks (`rglob`, `st_mtime`) and directory creation (`os.makedirs`) are allowed in `application/`; writing file contents belongs strictly in `infrastructure/downloaders.py`.
