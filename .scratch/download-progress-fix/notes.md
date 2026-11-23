# Download Progress Fix & Waiting State Notes

Conversation: [download-progress-fix](conversation://161ff251-b8a8-48ba-9578-92b5a746ca65)

## Root Causes Found
1. **256 KB `httpx.ByteChunker` buffering** in `MoodleClient.download_file` and `SharepointClient.download_file`:
   - `httpx.Response.aiter_bytes(chunk_size=256 * 1024)` held all incoming bytes in memory until 256 KB accumulated or EOF (`flush()`).
   - Fixed by lowering `chunk_size` to `16 * 1024` (16 KB) in both clients. `IDATSync` still throttles UI progress events to `0.15s`.
   - 16 KB is safe on 4 GB SharePoint files (~0.5–0.8s total Python loop CPU over a multi-minute download; Python's `BufferedWriter` + OS page cache coalesce disk writes).
2. **30s `AsyncRateLimiter` cooldown appearing as frozen `0% · 0 B`**:
   - `TaskDownloadStarted` fires before `AsyncRateLimiter.__aenter__` sleeps (default `request_delay = 30.0s`), so small files (e.g., 3.96 KB) sat at `0%` for 30s and then jumped 0→100% in milliseconds.
   - Minimal fix applied in `frontend/src/components/FileProgressBar.tsx`: when `downloaded === 0 && total === null`, show `"Waiting…"` with the indeterminate bar (`.indet`) instead of `0% · 0 B`.

## Deferred / Future Ideas
- **Full backend `Waiting` state** (if a live countdown or distinct `Waiting` status pill is ever needed): wire a wait hook from `AsyncRateLimiter` → `application/ports.py` → `events.py` (`TaskDownloadWaiting`) → `IDATSync` → `CourseTree` (`RowStatus.WAITING`) → frontend.
- **Large SharePoint files (4 GB)**: if needed later, real improvements are HTTP `Range` resumable downloads and configurable read timeouts (rather than adaptive `chunk_size`).
- **Pre-download file sizes**: Moodle and SharePoint adapter responses include file size metadata if we ever want to display sizes for queued/synced rows before downloading.
