# Tasks

## 1. SharePoint Personal Host Routing & Dead-Link Handling

- [x] 1.1 At the `SharepointClient` seam (`tests/test_sharepoint_adapter.py` and `src/idat_sync/infrastructure/sharepoint_client.py`), write a failing test and update `get_client_from_url(url)` to match `r"/(sites|teams|s|personal)/([^/]+)"` and `load_entity(entity, ctx)` to catch `TypeError` on `odata.null` while still sleeping `self.request_delay` and propagating `CredentialsExpiredError`; run `python -m unittest tests/test_sharepoint_adapter.py` to verify it passes.
- [x] 1.2 At the `SharepointApiAdapter` seam (`tests/test_sharepoint_adapter.py` and `src/idat_sync/infrastructure/sharepoint_adapter.py`), write a failing test and update `can_handle(url)` to match `{self.sharepoint_host, self.sharepoint_personal_host}` and `list_url_content(url, filename, last_modified)` to dispatch by URL path (`^/:f:/`, `^/:(\w+):/`, direct link) and return `[]` when both `name` and `serverRelativeUrl` are falsy; run `python -m unittest tests/test_sharepoint_adapter.py` to verify it passes.

## 2. Developer Extraction Scripts & Gitignore

- [x] 2.1 At the `scripts/login_cli.py`, `scripts/moodle_json.py`, and `.gitignore` seams (`tests/test_real_data.py`), write a failing test and move root `login_cli.py` and `moodle_json.py` to `scripts/` (removing root copies), add `all_courses_contents.json` to `.gitignore`, update `scripts/login_cli.py` docstring to use `Credentials`, and update `scripts/moodle_json.py` to fetch `fetch_enrolled_courses()`, `fetch_assignments()`, and `fetch_course_contents(id)` and write UTF-8 JSON to `Path("./all_courses_contents.json")`; run `python -m unittest tests/test_real_data.py` to verify it passes.

## 3. Real-Data Offline Fixture & Live Credentials Smoke Tests

- [x] 3.1 At the `MoodleAdapter`, `CourseRepository`, and `SyncDiff.from_trees` seams (`tests/test_real_data.py`), implement `TestRealCourseContentsFixture` guarded by `@unittest.skipUnless(Path("./all_courses_contents.json").is_file(), "Requires ./all_courses_contents.json")` to verify domain `Content` outputs, assignment `-[id]-intro.html` and `introattachments` mapping, URL strategy routing across both SharePoint hosts, and `SyncDiff.from_trees(remote_contents, {})`; run `python -m unittest tests/test_real_data.py` to verify it passes.
- [x] 3.2 At the `JsonCredentialStore`, `MoodleDownloader`, and `SharepointApiAdapter` seams (`tests/test_real_data.py`), implement `TestLiveCredentialsIntegration` guarded by `@unittest.skipUnless(Path("./all_courses_contents.json").is_file() and Path("./session.json").is_file(), "Requires ./all_courses_contents.json and ./session.json")` with `request_delay=0.0` and dynamic URL discovery across Moodle and SharePoint URL patterns; run `python -m unittest tests/test_real_data.py` to verify it passes.

## 4. Final Verification

- [x] 4.1 Run the full test suite (`python -m unittest discover -s tests`), typecheck, and lint, and verify all checks pass.
