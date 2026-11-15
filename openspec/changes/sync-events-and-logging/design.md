# Design

## Context

See `proposal.md` for motivation and [0001-hexagonal-ports-and-adapters.md](../../../docs/adr/0001-hexagonal-ports-and-adapters.md) for the package dependency boundary. Currently, `IDATSync.sync_course` and `TaskOrchestrator.run` thread a 6-method `SyncProgressPort` listener and three task callbacks (`on_task_start`, `on_task_complete`, `on_task_error`) through sync execution, while `ui/app.py:main()` replaces `sys.stdout` with an unread `io.StringIO()` and `sys.stderr` with `open(config.log_file, ...)`.

## Goals / Non-Goals

**Goals:**
- Replace imperative progress callbacks with immutable `SyncEvent` dataclasses published through a constructor-injected `EventPublisher`.
- Decouple `TaskOrchestrator` from `Course` while returning `list[Exception]` from `TaskOrchestrator.run`/`run_async` so `IDATSync.sync_course` needs no callback closures.
- Route all diagnostic and sync event logging through standard library `logging` (`StreamHandler` + `FileHandler`) configured in `bootstrap.configure_logging`, banning `print()` across `src/idat_sync/`.

**Non-Goals:**
- Converting HTTP/SDK clients or orchestrators to `async` (handled in a subsequent change).

## Decisions

1. **Immutable `SyncEvent` dataclasses and `log_sync_event` in `src/idat_sync/application/events.py`**
   - Define frozen dataclasses `CourseSyncStarted`, `CourseFilesDiscovered`, `TaskDownloadStarted`, `TaskDownloadCompleted`, `TaskDownloadFailed`, and `CourseSyncCompleted`, plus `SyncEvent`, `EventPublisher = Callable[[SyncEvent], None]`, and `log_sync_event(event: SyncEvent) -> None`.
   - *Rationale*: Keeps `application/events.py` pure Python (importing only stdlib and `domain.course.Course`) while allowing both `IDATSync` and `TaskOrchestrator` to publish events without branching on `if listener is not None:`.
   - *Alternatives considered*: Keeping a multi-method `SyncProgressPort` injected into `__init__`. Rejected because `TaskOrchestrator` only emits task-level events while `IDATSync` emits course-level events, and `SyncFrame` still had to convert method calls into queue payloads.

2. **Path-scoped task events and `list[Exception]` return from `TaskOrchestrator`**
   - `TaskDownloadStarted(path)`, `TaskDownloadCompleted(path)`, and `TaskDownloadFailed(path, error)` carry only `path: Path` (not `Course`), and `TaskOrchestrator.run(tasks)` / `run_async(tasks)` return `list[Exception]` of non-credential task errors in execution order.
   - `SyncFrame` indexes `self._file_iids` as `dict[Path, tuple[int, str]]` (`path -> (course.id, child_iid)`) when handling `CourseFilesDiscovered`.
   - *Rationale*: `task.path` is already scoped under `course_dir`, so `TaskOrchestrator` never needs to know about `Course`, and `IDATSync.sync_course` no longer wraps task callbacks in lambdas or `nonlocal first_task_error` closures.

3. **Composite publisher and `configure_logging` in `src/idat_sync/bootstrap.py`**
   - `create_sync_service(credentials, config, publish=_noop)` composes `_publish(event)` to invoke `log_sync_event(event)` followed by `publish(event)` and injects it into both `TaskOrchestrator` and `IDATSync`.
   - `configure_logging(log_file: Path) -> logging.Logger` configures `logging.getLogger("idat_sync")` at `logging.INFO` (`propagate = False`), closes and clears any existing handlers on `"idat_sync"`, and attaches `logging.StreamHandler()` and `logging.FileHandler(log_file, mode="w", encoding="utf-8")`.
   - *Rationale*: Running from a terminal outputs live `INFO`/`WARNING`/`ERROR` logs while `./log.err` still records file logs, without mutating `sys.stdout` or `sys.stderr`.

## Modules Touched & Public Test Seams

- **Modules touched**: `src/idat_sync/application/events.py`, `src/idat_sync/application/ports.py`, `src/idat_sync/application/task_orchestrator.py`, `src/idat_sync/application/sync_service.py`, `src/idat_sync/application/auth_service.py`, `src/idat_sync/infrastructure/credential_store.py`, `src/idat_sync/infrastructure/moodle_adapter.py`, `src/idat_sync/infrastructure/downloaders.py`, `src/idat_sync/bootstrap.py`, `src/idat_sync/ui/app.py`, `src/idat_sync/ui/login_frame.py`, `src/idat_sync/ui/sync_frame.py`, `docs/adr/0001-hexagonal-ports-and-adapters.md`.
- **Public test seams**: `idat_sync.application.events`, `TaskOrchestrator`, `IDATSync`, `bootstrap.create_sync_service`, `bootstrap.configure_logging`, `SyncFrame`, `ui.app.main`, `MoodleTokenAdapter`, and the AST architecture boundary seam in `tests/test_architecture.py`.

## Risks / Trade-offs

- **[Risk] Thread-safe ordering of `TaskOrchestrator` returned errors across downloader groups** -> Mitigation: Each `_run_group` returns its own `list[Exception]` and `run_async` flattens the results from `await asyncio.gather(...)` in group order.
- **[Risk] Duplicate log handlers when `configure_logging` is called multiple times in tests** -> Mitigation: `configure_logging` closes and removes existing handlers on `logging.getLogger("idat_sync")` before attaching new handlers.
