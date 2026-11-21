# Proposal

## Why

Bundling Playwright forces ~50 MB of Node.js driver binaries plus ~150 MB of Chromium/WebKit browser builds into release artifacts, requires brittle macOS `.framework` symlink repair and codesigning surgery in CI, and cannot run on Android, while Tkinter (`async-tkinter-loop`) prevents mobile portability and conflicts with native WebView main-thread event loops. Replacing both Playwright and Tkinter with `pywebview` and a build-time-compiled single-file Astro + Tailwind CSS frontend shrinks desktop binaries, removes external browser downloads, and enables a unified codebase across Windows, macOS, Linux, and Android.

## What Changes

- **BREAKING**: Replace `playwright` and `async-tkinter-loop` in `pyproject.toml` with `pywebview >= 5.0` (plus `pyobjc-framework-WebKit >= 9.0; sys_platform == 'darwin'` and optional `linux = ["pywebview[gtk]"]`, `android = ["pywebview[android]"]` extras), and remove all `playwright`, `tkinter`, and `async_tkinter_loop` imports across `src/idat_sync/`.
- **BREAKING**: Refactor `SharepointBrowserAuth` in `src/idat_sync/infrastructure/sharepoint_auth.py` to drive Microsoft SSO and MFA through an injectable `WebViewDriver` protocol (`PyWebviewDriver` on Windows/macOS/Linux using a hidden `pywebview` window and `AndroidWebViewDriver` on Android using a `1x1` `View.INVISIBLE` `android.webkit.WebView` with a standard `WebViewClient` and `CookieManager` via `pyjnius`), polling with Knockout-compatible JS `fill`/`click` helpers and merging `HttpOnly` cookies captured at both `sharepoint_host` and `sharepoint_personal_host`.
- **BREAKING**: Replace Tkinter frames and dialogs in `src/idat_sync/ui/` with pure-Python UI component controllers (`MainApp` in `ui/app.py`, `AppFrame` in `ui/base.py`, `LoginFrame` in `ui/login_frame.py`, `SyncFrame` in `ui/sync_frame.py`, and `CourseTree`/`RowStatus`/`Progress` in `ui/course_tree.py`) driving a single-file static Astro + Tailwind CSS bundle (`frontend/` compiled via `vite-plugin-singlefile` into `src/idat_sync/ui/index.html` and loaded via `html=...` with zero runtime HTTP server); remove `src/idat_sync/ui/icons.py` and `src/idat_sync/ui/mfa_dialog.py`.
- Add root `buildozer.spec` for Android packaging, update `.github/workflows/build_and_deploy.yml` to build `frontend/` and drop Playwright browser installation and macOS `.framework` symlink surgery, and update `README.md` and `colab.ipynb` for WebKitGTK.

## Non-Goals

- Changing `domain/` models, `application/` use-case services (`AuthService`, `IDATSync`, `CourseRepository`, `DownloadManager`, `TaskOrchestrator`), or `GLOSSARY.md` vocabulary.
- Running a Node.js runtime or local HTTP server at application runtime.
- Supporting third-party MFA flows beyond the scripted 6-digit OTP callback (`get_mfa_code`).

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `authentication`: Replace Playwright browser automation and Tkinter login/MFA frames with `WebViewDriver`-backed `SharepointBrowserAuth` (`PyWebviewDriver` and `AndroidWebViewDriver`), pure-Python `MainApp`/`AppFrame`/`LoginFrame`/`SyncFrame` component controllers, and updated AST import boundaries forbidding `playwright`, `tkinter`, `webview`, and `jnius` in `application/`.
- `course-synchronization`: Update AST dependency boundaries (`playwright`, `tkinter`, `async_tkinter_loop` forbidden across `src/idat_sync/`; `webview` and `jnius` forbidden in `domain/` and `application/`) and replace `ttk.Treeview`/`Icons` in `CourseTree` and `SyncFrame` with pure-Python state serialization (`CourseTree.to_dict()`) driving the bundled Astro + Tailwind UI (`src/idat_sync/ui/index.html`).

## Impact

- **Code**: `src/idat_sync/config.py`, `src/idat_sync/bootstrap.py`, `src/idat_sync/infrastructure/sharepoint_auth.py`, `src/idat_sync/ui/{app.py,base.py,login_frame.py,sync_frame.py,course_tree.py,__init__.py}` (deleting `icons.py` and `mfa_dialog.py`), `frontend/`, `tests/{test_auth.py,test_sharepoint_auth.py,test_architecture.py}`.
- **Packaging & Docs**: `pyproject.toml`, `buildozer.spec`, `.github/workflows/build_and_deploy.yml`, `README.md`, `colab.ipynb`, `docs/adr/0006-pywebview-and-static-astro-ui.md`, `agent-os/standards/{backend/client-adapter-acl.md,global/layer-boundaries.md,testing/composition-and-seams.md}`.
- **Applicable Agent-OS Standards (`agent-os/standards/index.yml`)**:
  - `global/layer-boundaries`
  - `testing/composition-and-seams`
