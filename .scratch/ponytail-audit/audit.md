# Ponytail Audit Report

**What this repo does:** `idat-sync` is a desktop app (and Google Colab notebook) for IDAT students that signs into Moodle and Microsoft SharePoint/OneDrive (including 2-factor authentication), then downloads course documents, HTML summaries, and class video recordings into organized local folders. It checks file modification times (timestamps showing when a file was last changed) so it only downloads new or updated material. **Assumed load:** one student running a desktop window or Colab session at a time to sync 4–10 courses (dozens of documents and several large video recordings per course).

### Must fix

1. **Recording video downloads can leave broken files marked as synced or re-sync forever** (`src/idat_sync/infrastructure/downloaders.py:L183-206`)
   - **What this is:** `RecordingDownloader` runs `yt-dlp` and `ffmpeg` (a tool that combines separate audio and video streams into one `.mp4` file) to save class recordings to disk.
   - **Problem:** Unlike document downloads, it writes directly to the final filename and leaves `yt-dlp`'s `updatetime` setting enabled, which sets the local file's timestamp from the video stream header. If the app closes while `ffmpeg` is combining streams, a broken file stays at the final path with today's timestamp and is marked `"Synced"` forever; conversely, if SharePoint updated the item's metadata (such as auto-captions) 1 second after the video stream was created, the local file timestamp stays older than SharePoint's and shows up as `"Pending"` on every sync.
   - **Fix:** Pass `"updatetime": False` and `"overwrites": True` in `ydl_opts`, and write to a temporary `.part` sibling path before renaming to `os_target` on success (and deleting it on failure).
   - **If we skip it:** Interrupted video downloads stay corrupted on disk without being retried, and recordings with newer SharePoint metadata stay stuck as pending on every run.

2. **SharePoint login hardcodes the second MFA option and fails when options are ordered differently** (`src/idat_sync/infrastructure/sharepoint_auth.py:L94-105`)
   - **What this is:** `_complete_login` drives Microsoft's login page in a browser window and enters the student's 6-digit multi-factor authentication (MFA, a second login check on your phone) code.
   - **Problem:** Line 98 unconditionally clicks `#idDiv_SAOTCS_Proofs > div:nth-child(2)` before filling the code box `#idTxtBx_SAOTCC_OTC`. If a student's Microsoft account opens directly on the code entry box, or lists SMS/Authenticator code in the first or third row instead of the second, clicking `nth-child(2)` either times out after 3 minutes or picks an approval method with no text box.
   - **Fix:** Wait for `#idTxtBx_SAOTCC_OTC, #idDiv_SAOTCS_Proofs`, and only click a proof row (matched by `data-value` for OTP/SMS rather than `nth-child(2)`) when `#idTxtBx_SAOTCC_OTC` is not already visible.
   - **If we skip it:** Students whose Microsoft account defaults directly to a code prompt or orders MFA methods differently cannot log in and fail after a 3-minute wait.

3. **Google Colab setup cell hangs on `apt` prompts and misses the Python `weasyprint` package** (`colab.ipynb:L26-30`)
   - **What this is:** The first code cell in the Google Colab notebook installs the packages needed to sync courses and convert HTML notes to PDF.
   - **Problem:** `!apt install weasyprint` and `!apt-get install xattr` omit `-y`, which makes `apt` pause and wait for a keyboard confirmation (`Do you want to continue? [Y/n]`) that cannot be typed in a non-interactive cell. In addition, `apt install weasyprint` installs the Ubuntu CLI package rather than installing the `weasyprint` Python library into the `%pip` environment used by `from weasyprint import HTML` in cell 2.
   - **Fix:** Move `weasyprint` to the `%pip install -U` line and change the apt command to `!apt-get install -y xattr`.
   - **If we skip it:** Running the notebook from scratch in Google Colab hangs on cell 1 or fails with `ModuleNotFoundError: No module named 'weasyprint'` in cell 2.

4. **Colab post-processing prints duplicate PDF links and skips updated recordings** (`colab.ipynb:L183-204`)
   - **What this is:** After syncing a course to Google Drive, this loop converts `.html` files to `.pdf` and video recordings to `.mp3`, then prints Google Drive share links for NotebookLM.
   - **Problem:** When an `.html` file is updated on a later sync and its old `.pdf` already exists in the folder, `os.walk` visits both files: the `.html` branch regenerates the `.pdf` and adds its link, and then the loop visits the `.pdf` file and adds the same link a second time. For videos, `if os.path.exists(f"{filename}.mp3"): continue` never checks whether the video file is newer than the `.mp3`, so an updated recording never updates its `.mp3`.
   - **Fix:** Compare `os.path.getmtime(path) <= os.path.getmtime(mp3_path)` for videos just like PDFs, run conversions first, and collect share links in a separate pass that skips `.html` and `video/*` source files.
   - **If we skip it:** Re-syncing a course in Colab prints duplicate Drive links and keeps stale `.mp3` audio when a recording is updated.

### Should fix

5. **Release workflow builds and publishes binaries without running tests or linters** (`.github/workflows/build_and_deploy.yml:L36-50`)
   - **What this is:** This GitHub Actions workflow builds the Windows, Linux, and macOS executables and uploads them to GitHub Releases when a `v*` tag is pushed.
   - **Problem:** It never runs the test suite, `ruff`, or `pylint`, and no workflow runs on pushes or pull requests to `main`. A tag with a broken sync pipeline or lint error still builds and publishes release binaries to users.
   - **Fix:** Add a step in `.github/workflows/build_and_deploy.yml` (and trigger on `push` / `pull_request`) that runs `pytest`, `ruff check .`, and `pylint src tests` before building executables.
   - **If we skip it:** Regressions that the 180+ existing tests already catch can ship directly into a public GitHub Release.

6. **Default 30-second rate-limit delay makes a normal course sync sleep for 20+ minutes** (`src/idat_sync/config.py:L89`, `src/idat_sync/application/course_repository.py:L152-165`)
   - **What this is:** `SyncConfig.request_delay` defaults to `30.0` seconds, and `AsyncRateLimiter` forces a 30-second cooldown after every single Moodle or SharePoint API call.
   - **Problem:** Even when every file is already downloaded, checking one course that has a few SharePoint folder links and recordings takes 10–15 sequential SharePoint metadata requests (5–7.5 minutes of sleeping per course, or 25+ minutes across 4 courses). Spawning module tasks concurrently in `stream_course_content` does not speed this up because all tasks queue behind the same `AsyncRateLimiter` lock.
   - **Fix:** Lower the default `request_delay` in `SyncConfig` to `1.0`–`2.0` seconds for metadata and file requests (or apply the 30-second delay only to `RecordingDownloader`).
   - **If we skip it:** Syncing a normal 4–6 course semester spends 20–45 minutes waiting on `asyncio.sleep(30.0)` just to check folder listings.

7. **Tests use `pytest` without listing it in `pyproject.toml`, breaking `unittest` and fresh dev installs** (`pyproject.toml:L35-36`, `tests/test_auth.py:L16`)
   - **What this is:** All 9 test files use Python's standard `unittest.TestCase`, and `pyproject.toml` lists the development dependencies under `[dependency-groups].dev`.
   - **Problem:** Eight of the nine test files import `pytest` only for `with pytest.raises(...)`, but `pytest` is not declared in `[dependency-groups].dev`. Running `python -m unittest` without an undeclared `pytest` package installed fails immediately with `ModuleNotFoundError: No module named 'pytest'` on 8 test files.
   - **Fix:** Replace `pytest.raises` with standard library `self.assertRaises` / `self.assertRaisesRegex` and remove `import pytest` (-1 dependency), or add `"pytest>=8.0"` to `[dependency-groups].dev`.
   - **If we skip it:** A developer who installs `[dependency-groups].dev` from `pyproject.toml` cannot run the test suite.

8. **Unused package `__init__.py` re-exports eagerly load heavy third-party libraries** (`src/idat_sync/application/__init__.py:L1-15`, `src/idat_sync/domain/__init__.py:L1-37`, `src/idat_sync/infrastructure/__init__.py:L1-29`)
   - **What this is:** These three `__init__.py` files re-export classes from every submodule in `application`, `domain`, and `infrastructure`.
   - **Problem:** No file in `src/`, `scripts/`, `tests/`, or `colab.ipynb` imports from these package roots—every caller imports from the concrete submodule. Because `infrastructure/__init__.py` imports every adapter and downloader at the top level, importing any single lightweight submodule (like `idat_sync.infrastructure.credential_store` in `scripts/moodle_json.py`) forces Python to load `playwright`, `yt_dlp`, `office365`, and `httpx`.
   - **Fix:** Keep only the module docstrings in `application/__init__.py`, `domain/__init__.py`, and `infrastructure/__init__.py` (-78 lines).
   - **If we skip it:** Every CLI script, test, and startup path pays the import cost of `playwright`, `yt_dlp`, and `office365` even when only touching `credential_store` or `rate_limiter`.

### Nice to have

9. **`AuthService.authenticate` is a single-caller wrapper that duplicates username splitting** (`src/idat_sync/application/auth_service.py:L38-41`, `src/idat_sync/application/auth_service.py:L79`)
   - **What this is:** `AuthService.authenticate(username, password)` strips `@domain` from `username` and calls `self.moodle_auth.fetch_token(...)`.
   - **Problem:** Nothing outside `AuthService.login` calls `authenticate`, and line 79 of `login` repeats `username.split("@", 1)[0]` a second time for logging.
   - **Fix:** Compute `moodle_username = username.split("@", 1)[0]` once inside `login`, call `self.moodle_auth.fetch_token(moodle_username, password)` directly, and remove `authenticate` (-5 lines).
   - **If we skip it:** An extra public method and test remain for a one-line call used only inside `login`.

10. **`TaskOrchestrator` is a pass-through factory with one product** (`src/idat_sync/application/task_orchestrator.py:L9-10`, `src/idat_sync/application/task_orchestrator.py:L61-70`)
    - **What this is:** `TaskOrchestrator` stores `task_group: list[list[Downloader]]` and exposes one method, `create_channel()`, which returns `TaskChannel(self.task_group)`.
    - **Problem:** `TaskOrchestrator` adds no logic beyond passing `self.task_group` to `TaskChannel`, and `_default_run_task` is never used in production because `_CourseStreamRun.run` always passes `self._run_single_task`.
    - **Fix:** Pass the downloader groups directly to `TaskChannel` from `IDATSync` (making `run_task` required on `create_workers`) and delete `TaskOrchestrator` and `_default_run_task` (-15 lines).
    - **If we skip it:** Readers trace an extra wrapper class that only forwards one constructor argument.

---

**Verdict:** Well-structured and heavily tested core; fix **1** (atomic video downloads + timestamp handling) and **2** (hardcoded MFA row click) first, then **3–4** if you use the Colab notebook.
**Lean:** -98 lines, -1 dependency possible.
**Not checked:** Live Moodle/SharePoint servers and live fixture tests in `tests/test_real_data.py:L292-551` (`all_courses_contents.json` requires live credentials in `session.json` for the live test class).
