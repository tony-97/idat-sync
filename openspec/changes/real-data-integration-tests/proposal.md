# Proposal

## Why

Testing only against small synthetic mocks misses real-world Moodle and SharePoint payload quirks—such as personal OneDrive links (`sharepoint_personal_host` under `/personal/<user>`), dead or expired SharePoint guest/direct links (`odata.null`), and multi-course assignment `introattachments` across 35 enrolled courses. Providing developer CLI scripts to generate local, gitignored `./all_courses_contents.json` and `./session.json` artifacts enables deterministic offline fixture testing and fast live `Credentials` smoke testing without leaking private curriculum data or tokens into git.

## What Changes

- Move developer extraction scripts to `scripts/login_cli.py` and `scripts/moodle_json.py` (removing root `login_cli.py` and `moodle_json.py`), updating `scripts/moodle_json.py` to fetch `fetch_enrolled_courses()`, `fetch_course_contents(id)`, and `fetch_assignments()` and write UTF-8 JSON (`[{"course": ..., "contents": [...], "assignments": [...]}]`) directly to `./all_courses_contents.json`.
- Add `all_courses_contents.json` to `.gitignore` alongside `session*.json`.
- Update `SharepointApiAdapter.can_handle(url)` to match `(urlparse(url).hostname or "").lower() in {self.sharepoint_host, self.sharepoint_personal_host}`.
- Simplify `SharepointApiAdapter.list_url_content(url, filename, last_modified)` to inspect `urlparse(url).path` (`^/:f:/` for folders, `^/:(\w+):/` for files, otherwise `fetch_file_from_direct_link`) without repeating the hostname check, and return `[]` when a resolved `Folder` or `File` has both falsy `name` and falsy `serverRelativeUrl`.
- Update `SharepointClient.get_client_from_url(url)` to match `r"/(sites|teams|s|personal)/([^/]+)"` case-insensitively on `parsed_url.path`, and update `SharepointClient.load_entity(entity, ctx)` to catch `TypeError` when an `odata.null` guest URL response leaves `entity.resource_url` as `None` while still sleeping `self.request_delay` and propagating `CredentialsExpiredError`.
- Add unit tests in `tests/test_sharepoint_adapter.py` and conditional real-data offline and live `Credentials` smoke tests in `tests/test_real_data.py`.

## Non-Goals

- Committing private student course payloads (`all_courses_contents.json`) or live `Credentials` (`session.json`) to version control.
- Modifying `src/idat_sync/domain/`, `src/idat_sync/application/`, `src/idat_sync/ui/`, `src/idat_sync/bootstrap.py`, or `src/idat_sync/config.py`.
- Running full multi-course video or file downloads in the automated test suite.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `course-synchronization`: Support `sharepoint_personal_host` and `/personal/<user>` SharePoint links, safely skip dead/expired `odata.null` SharePoint links, provide `scripts/login_cli.py` and `scripts/moodle_json.py` developer scripts, and verify real-data offline fixtures and live `Credentials` smoke tests at public seams.

## Applicable Standards

From `agent-os/standards/index.yml`:
- `backend/client-adapter-acl`
- `backend/content-strategy-dispatch`
- `global/layer-boundaries`
- `testing/composition-and-seams`

## Impact

- **Production modules**: `src/idat_sync/infrastructure/sharepoint_adapter.py`, `src/idat_sync/infrastructure/sharepoint_client.py`
- **Developer scripts & config**: `scripts/login_cli.py`, `scripts/moodle_json.py`, `.gitignore` (root `login_cli.py` and `moodle_json.py` removed)
- **Tests**: `tests/test_sharepoint_adapter.py`, `tests/test_real_data.py`
