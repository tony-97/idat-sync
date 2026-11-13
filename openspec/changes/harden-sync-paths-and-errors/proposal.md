# Proposal

## Why

Remote Moodle and SharePoint names currently pass unsanitized into nested directory and file paths, risking OS write errors and path traversal outside the `Course` directory. Meanwhile, expired `Credentials` and single-item transport failures raise untyped exceptions that abort remaining `Course` downloads or stall recovery in `SyncFrame`.

## What Changes

- Keep ASCII normalization in `safe_filename` for top-level `Course` directories ([ADR-0002](../../../docs/adr/0002-path-sanitization-compatibility.md)) while fixing post-truncation trailing dots/spaces and Windows reserved stems with extensions (e.g., `CON.txt` -> `_CON.txt`).
- Add an OS-safe, UTF-8-preserving, extension-preserving segment sanitizer in `domain/path_utils.py` for `CourseSection`, `CourseModule`, and file names, and fail closed in `Content.in_folder` when `self.path` is anchored/absolute or `..` traversal segments appear.
- Define `CredentialsExpiredError(RuntimeError)` in `application/ports.py` and map HTTP 401/403, Moodle `invalidtoken`, and empty `personal_site.url` in `MoodleClient`, `SharepointClient`, and `RecordingDownloader` to it.
- Isolate single-item errors in `TaskOrchestrator` and single-`Course` errors in `IDATSync` by logging and continuing, while aborting early and propagating `CredentialsExpiredError`.
- Harden `SyncFrame` error recovery so startup (`load_idat_sync`) and sync (`do_sync`) failures display errors in `Progress:`, never silently wipe stored `Credentials`, re-enable UI controls in `finally:`, and retry `create_sync_service(credentials, config)` inside `do_sync` whenever `self.idat_sync is None`.

## Non-Goals

- Migrating `colab.ipynb` (explicitly deferred for a later pass).
- Pure bugfixes and cleanups that do not change spec contracts (atomic `.part` download writes, SharePoint `datetime.min` and `timezone.utc` timestamp handling without adding timezone configuration to `SyncConfig`, Moodle `None` summary/intro coercion, macOS `p.webkit.launch` and `sys.stderr` logging in `SharepointBrowserAuth`, `LoginFrame` `<Return>` binding and MFA popup close cancellation, `SyncFrame` `\r` tag guard, `os.devnull` stdout redirection, redundant sleeps/dead code cleanup, and bumping `requires-python` to `>=3.12`), which follow direct TDD outside OpenSpec.

## Capabilities

### New Capabilities

<!-- None: all changes refine existing authentication and course-synchronization contracts. -->

### Modified Capabilities

- `authentication`: Export `CredentialsExpiredError(RuntimeError)` in `application/ports.py`, and specify `SyncFrame` failure recovery without wiping `Credentials`, re-enabling controls, and lazily retrying `create_sync_service` in `do_sync`.
- `course-synchronization`: Specify the split path-sanitization compatibility rule ([ADR-0002](../../../docs/adr/0002-path-sanitization-compatibility.md)) and `Content.in_folder` traversal guards, map expired `Credentials` failures to `CredentialsExpiredError`, and isolate per-item and per-`Course` errors while stopping early on `CredentialsExpiredError`.

## Impact

- **Domain**: `src/idat_sync/domain/path_utils.py`, `src/idat_sync/domain/content.py`
- **Application**: `src/idat_sync/application/ports.py`, `course_repository.py`, `sync_service.py`, `task_orchestrator.py`
- **Infrastructure & UI**: `src/idat_sync/infrastructure/moodle_client.py`, `moodle_adapter.py`, `sharepoint_client.py`, `sharepoint_adapter.py`, `downloaders.py`, `src/idat_sync/ui/sync_frame.py`
- **Docs & Tests**: `docs/adr/0002-path-sanitization-compatibility.md`, `tests/`
- **Applicable Agent-OS Standards** (`agent-os/standards/index.yml`): `backend/client-adapter-acl`, `backend/content-strategy-dispatch`, `global/layer-boundaries`, `testing/composition-and-seams`
