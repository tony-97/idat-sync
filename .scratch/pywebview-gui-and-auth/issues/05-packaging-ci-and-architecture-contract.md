# 05: Packaging, Buildozer, CI Workflow, Docs & AST Boundary Contract

**OpenSpec Change:** `openspec/changes/pywebview-gui-and-auth`

**What to build:** Update project dependencies, PyInstaller and Buildozer packaging specs, GitHub Actions CI, documentation (`README.md` and `colab.ipynb`), and AST architectural boundary tests to replace `playwright` and `async-tkinter-loop`/`tkinter` with `pywebview` across Windows, macOS, Linux, and Android, and verify the entire repository passes `pytest`, `ruff`, and `pylint` with a `10.00/10` score.

**Blocked by:** 04: `MainApp` WebView Coordinator & Headless UI Test Suite

**Status:** resolved

- [x] `pyproject.toml` replaces `playwright` and `async-tkinter-loop` with `pywebview >= 5.0` (plus `pyobjc-framework-WebKit >= 9.0; sys_platform == 'darwin'` and optional `linux = ["pywebview[gtk]"]`, `android = ["pywebview[android]"]` extras) and declares `src/idat_sync/ui/index.html` in package data.
- [x] Root `buildozer.spec` is added for Android packaging (`requirements = python3,android,pyjnius,bottle,typing_extensions,pywebview,httpx,office365-rest-python-client,platformdirs,yt-dlp`, `source.include_exts = py,png,jpg,html,jar,js,css`, `pywebview-android.jar`, and `INTERNET`/`READ_EXTERNAL_STORAGE`/`WRITE_EXTERNAL_STORAGE` permissions).
- [x] `.github/workflows/build_and_deploy.yml` and `idat_sync.spec` build `frontend/` with Node (`npm --prefix frontend ci && npm --prefix frontend run build`), install WebKitGTK packages on Ubuntu, remove `playwright install` on Linux/macOS, remove the macOS Playwright `.framework` symlink repair script, and bundle `src/idat_sync/ui/index.html` in PyInstaller.
- [x] `README.md` and `colab.ipynb` replace Playwright installation steps with WebKitGTK (`python3-gi`, `gir1.2-webkit2-4.1`, and `xvfb` in Colab).
- [x] AST boundary tests in `tests/test_architecture.py` and `tests/test_auth.py` verify that `domain/` and `application/` (`ports.py`, `exceptions.py`) have zero imports from `idat_sync.config`, `idat_sync.infrastructure`, `idat_sync.ui`, `requests`, `httpx`, `office365`, `yt_dlp`, `playwright`, `tkinter`, `async_tkinter_loop`, `webview`, or `jnius`; no module under `src/idat_sync/` imports `playwright`, `tkinter`, `async_tkinter_loop`, or `requests` or calls `print(...)`; and `src/idat_sync/ui/` never imports `idat_sync.infrastructure`.
- [x] Full test suite (`pytest`), Ruff (`ruff check .` and `ruff format --check .`), and Pylint (`pylint src tests` with score `10.00/10`) pass cleanly.
