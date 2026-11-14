# Design

## Context

See `proposal.md` for motivation. Under [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) and [ADR-0002](../../../docs/adr/0002-path-sanitization-compatibility.md), `MoodleAdapter` and `SharepointApiAdapter` act as Anti-Corruption Layers over `MoodleClient` and `SharepointClient`, while `CourseRepository` dispatches `ModuleContent` URLs through `UrlContentStrategy` (`can_handle` and `list_url_content`).

Inspecting a real 35-course extraction (`./all_courses_contents.json`) and live `Credentials` (`./session.json`) exposed three concrete gaps in the SharePoint infrastructure layer and developer workflow:
1. Teachers share personal OneDrive links on `sharepoint_personal_host` (`https://idat628-my.sharepoint.com/:b:/g/personal/<user>/...`), which `SharepointApiAdapter.can_handle`, `SharepointApiAdapter.list_url_content`, and `SharepointClient.get_client_from_url` ignored or routed to the academic site instead of `/personal/<user>`.
2. Expired or deleted SharePoint guest links (`GetFileByGuestUrl` / `GetFolderByGuestUrl`) return `odata.null`, leaving `entity.resource_url = None` and crashing `SharepointClient.load_entity` with `TypeError` when `ReadEntityQuery.url` concatenates `None + "?"`; expired direct links (`GetFileById`) return `entity.name = None` and `entity.serverRelativeUrl = None`.
3. `moodle_json.py` omitted `client.fetch_assignments()` (leaving 124 `"modname": "assign"` modules without their `intro` and 21 `introattachments`) and printed to stdout instead of writing `./all_courses_contents.json`.

## Goals / Non-Goals

**Goals:**
- Confine production code edits strictly to `src/idat_sync/infrastructure/sharepoint_adapter.py` and `src/idat_sync/infrastructure/sharepoint_client.py`.
- Keep `./all_courses_contents.json` and `./session.json` gitignored while providing committed developer scripts `scripts/login_cli.py` and `scripts/moodle_json.py`.
- Verify both offline 35-course fixture processing and live `Credentials` smoke tests at confirmed public seams (`SharepointApiAdapter`, `SharepointClient`, `MoodleAdapter`, `CourseRepository`, `SyncDiff.from_trees`, `JsonCredentialStore`, `MoodleDownloader`).

**Non-Goals:**
- No new ADRs or `GLOSSARY.md` entries are needed; existing `Credentials`, `Course`, `CourseSection`, `CourseModule`, `ModuleContent`, `Content`, `StringContent`, and `SyncDiff` terms and [ADR-0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) / [ADR-0002](../../../docs/adr/0002-path-sanitization-compatibility.md) govern this work.
- No changes to `domain/`, `application/`, `ui/`, `bootstrap.py`, or `config.py`.

## Decisions

### 1. Path-based dispatch in `SharepointApiAdapter` and `/personal/` site routing in `SharepointClient`
- **Choice**:
  - `SharepointApiAdapter.can_handle(url)` checks `(urlparse(url).hostname or "").lower() in {self.sharepoint_host, self.sharepoint_personal_host}`.
  - `SharepointApiAdapter.list_url_content(url, filename, last_modified)` inspects `urlparse(url).path`: `re.search(r"^/:f:/", path, re.IGNORECASE)` routes to `fetch_folder_by_guest_url(url)`, `re.search(r"^/:(\w+):/", path, re.IGNORECASE)` routes to `fetch_file_by_guest_url(url)`, and all other paths route to `fetch_file_from_direct_link(url)`.
  - `SharepointClient.get_client_from_url(url)` matches `r"/(sites|teams|s|personal)/([^/]+)"` case-insensitively on `parsed_url.path`, mapping `/s/<name>` to `{base_url}/sites/<name}` and `/(sites|teams|personal)/<name>` to `{base_url}{path_match.group(0)}`.
- **Rationale**: `CourseRepository` only calls `list_url_content` after `can_handle(url)` succeeds, so repeating the host regex in `list_url_content` duplicated host matching and blocked `sharepoint_personal_host` links. Adding `personal` to `get_client_from_url` reuses the existing `{base_url}{path_match.group(0)}` branch.
- **Alternatives considered**: Repeating both hostnames in `list_url_content` regexes (rejected as redundant).

### 2. Two-stage `odata.null` guard across `SharepointClient.load_entity` and `SharepointApiAdapter.list_url_content`
- **Choice**:
  - In `SharepointClient.load_entity(entity, ctx)`, wrap `self._run_query(ctx.execute_query_with_incremental_retry)` to catch `TypeError` (caused by `ReadEntityQuery.url` when `entity.resource_url is None` after an `odata.null` guest URL response) and still call `time.sleep(self.request_delay)`, while letting `CredentialsExpiredError` and all other exceptions propagate unchanged.
  - In `SharepointApiAdapter.list_url_content`, check `if not entity or (not entity.name and not entity.serverRelativeUrl): return []` for resolved folders and files before building `SharepointContent` or traversing subfolders.
- **Rationale**: Keeps the single batch `ctx.load(entity, self.properties_to_retrieve)` + `execute_query_with_incremental_retry()` call structure expected by existing `SharepointClient` unit tests while cleanly dropping dead guest and direct-ID links.
- **Alternatives considered**: Executing two separate `execute_query` calls per entity (rejected because it doubles HTTP round-trips and `request_delay` sleeps).

### 3. Dynamic URL discovery and `@unittest.skipUnless` in `tests/test_real_data.py`
- **Choice**: Guard `TestRealCourseContentsFixture` on `Path("./all_courses_contents.json").is_file()` and `TestLiveCredentialsIntegration` on both `Path("./all_courses_contents.json").is_file()` and `Path("./session.json").is_file()`, and discover sample URLs by URL pattern at runtime with `request_delay=0.0`.
- **Rationale**: CI and developers without local extraction files skip cleanly; developers with generated files test against their own enrolled courses without hardcoded course IDs, teacher names, or share tokens in git.

## Risks / Trade-offs

- **[Risk] Developer runs live tests with expired `./session.json` cookies/token** → Mitigation: `MoodleClient` and `SharepointClient` raise `CredentialsExpiredError`, failing the live smoke test with a clear message to re-run `python scripts/login_cli.py`.
- **[Risk] Legacy `./all_courses_contents.json` generated before `"assignments"` key was added** → Mitigation: `TestRealCourseContentsFixture` reads `item.get("assignments", [])` so existing files still pass structural tests, while `scripts/moodle_json.py` writes `"assignments"` for full assignment coverage.
