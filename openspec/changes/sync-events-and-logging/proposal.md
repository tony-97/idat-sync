# Proposal

## Why

`IDATSync` and `TaskOrchestrator` currently thread a 6-method `SyncProgressPort` listener and three task callbacks (`on_task_start`, `on_task_complete`, `on_task_error`) through sync execution, cluttering use-case logic with `if progress_listener is not None:` branches and lambda adapters while `SyncFrame._TreeProgressListener` repacks those calls into untyped queue tuples. At the same time, `ui/app.py:main()` hijacks `sys.stdout = io.StringIO()` and `sys.stderr = open(config.log_file, ...)`, silencing terminal output while `print()` statements remain scattered across `application/`, `infrastructure/`, and `ui/`. Replacing imperative callback threading with constructor-injected immutable `SyncEvent`s and standard library `logging` cleanly separates business progress events from diagnostic logging before we introduce async client transport.

## What Changes

- **BREAKING**: Add `src/idat_sync/application/events.py` defining frozen event dataclasses (`CourseSyncStarted`, `CourseFilesDiscovered`, `TaskDownloadStarted`, `TaskDownloadCompleted`, `TaskDownloadFailed`, `CourseSyncCompleted`), `SyncEvent`, `EventPublisher = Callable[[SyncEvent], None]`, and `log_sync_event(event: SyncEvent) -> None` (`INFO` for progress/completion, `ERROR` for task/course failures on `logging.getLogger("idat_sync")`), and remove `SyncProgressPort` from `src/idat_sync/application/ports.py`.
- **BREAKING**: Inject `publish: EventPublisher = _noop` in `TaskOrchestrator.__init__` and `IDATSync.__init__`. Remove `on_task_start`, `on_task_complete`, and `on_task_error` parameters from `TaskOrchestrator.run`, `run_async`, and `_run_group`, returning `list[Exception]` of non-credential task errors from `run(tasks)` and `run_async(tasks)`. Remove `progress_listener` from `IDATSync.sync_course` and `IDATSync.sync_courses`.
- Update `create_sync_service(credentials: Credentials, config: SyncConfig, publish: EventPublisher = _noop) -> IDATSync` in `src/idat_sync/bootstrap.py` to fan out every `SyncEvent` to `log_sync_event` and the caller's `publish` callback, and add `configure_logging(log_file: Path) -> logging.Logger` attaching `StreamHandler()` and `FileHandler(log_file, mode="w", encoding="utf-8")` at `logging.INFO` (`propagate = False`) on `"idat_sync"`.
- Remove `_TreeProgressListener` from `src/idat_sync/ui/sync_frame.py`; wire `SyncFrame._ensure_sync_service` to pass `publish=lambda event: loop.call_soon_threadsafe(self.progress_queue.put_nowait, event)` into `create_sync_service`, consume typed `SyncEvent` instances directly in `SyncFrame.update_progress`, and index `self._file_iids` by `Path -> (course_id, child_iid)`.
- Remove `sys.stdout = io.StringIO()` and `sys.stderr = open(...)` in `src/idat_sync/ui/app.py:main()` in favor of `configure_logging(config.log_file)`, and eliminate all `print()` calls across `src/idat_sync/` (`auth_service.py`, `sync_service.py`, `task_orchestrator.py`, `credential_store.py`, `moodle_adapter.py`, `downloaders.py`, `login_frame.py`) in favor of `logging.getLogger(__name__)`.

## Non-Goals

- Upgrading `office365-rest-python-client` or converting `SharepointClient`, `MoodleClient`, adapters, or downloaders to `async` (deferred to the next change).
- Adding new external logging dependencies or CLI argument parsers.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `course-synchronization`: Replace `SyncProgressPort` and task callbacks with constructor-injected `SyncEvent` publication (`application/events.py`), return `list[Exception]` from `TaskOrchestrator.run`/`run_async`, add `configure_logging` and event fan-out in `bootstrap.py`, update `SyncFrame` and `ui.app.main()`, and ban `print()` across `src/idat_sync/`.
- `authentication`: Replace `print()` calls in `MoodleTokenAdapter`, `JsonCredentialStore`, `AuthService`, and `LoginFrame` with `logging.getLogger(__name__)`, and update `SyncFrame` `create_sync_service` wiring with `publish`.

## Impact

- **Modules touched**: `src/idat_sync/application/events.py`, `src/idat_sync/application/ports.py`, `src/idat_sync/application/task_orchestrator.py`, `src/idat_sync/application/sync_service.py`, `src/idat_sync/application/auth_service.py`, `src/idat_sync/infrastructure/credential_store.py`, `src/idat_sync/infrastructure/moodle_adapter.py`, `src/idat_sync/infrastructure/downloaders.py`, `src/idat_sync/bootstrap.py`, `src/idat_sync/ui/app.py`, `src/idat_sync/ui/login_frame.py`, `src/idat_sync/ui/sync_frame.py`, `docs/adr/0001-hexagonal-ports-and-adapters.md`.
- **Applicable `agent-os` standards**: `global/layer-boundaries`, `testing/composition-and-seams`, `backend/client-adapter-acl`.
