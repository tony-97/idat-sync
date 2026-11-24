# Changelog

## v1.0.0

### Added
- **Desktop UI & Auth:** Migrated GUI to `pywebview` with a new Astro + SolidJS + Tailwind frontend, replacing Playwright SharePoint SSO with native WebView authentication.
- **Streaming Sync & Explorer:** Per-course selection, lazy loading, live tree updates, streaming download progress, and opening downloaded files in the native OS app.
- **Android Build:** Added Buildozer Android APK build to CI releases.
- **Colab Notebook:** Updated `colab.ipynb` to use `ipywidgets` and the current API.

### Changed
- **Architecture:** Reorganized into hexagonal architecture (ports & adapters) with async `httpx` clients, `AsyncRateLimiter`, and typed error summaries.
- **Paths & Storage:** Preserved module subfolders, consolidated section labels/bibliography into HTML, and moved runtime files to `platformdirs`.

### Fixed
- **SharePoint & Recordings:** Redeemed SharePoint guest URLs, fixed `Files(<key>)` 400 errors, handled personal SharePoint routing, and skipped audio extraction when no audio track is present.
- **Auth & Resilience:** Hardened credential expiry, token redaction, Windows long paths, and mutual auth cancellation.
- **CI/CD:** Fixed duplicated release notes by moving release creation out of the OS build matrix, and fixed macOS ad-hoc codesigning.
