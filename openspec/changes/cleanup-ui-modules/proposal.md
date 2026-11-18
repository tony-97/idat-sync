# Proposal

## Why

`src/idat_sync/ui/sync_frame.py` (587 lines) mixes pixel-art icon grids, `ttk.Treeview` node bookkeeping, widget layout, async busy-state boilerplate, event dispatching, and domain mutation (`course.sections.clear()`). `LoginFrame` and `MainApp` also embed an inline MFA dialog, name-mangled attributes, `cast(MainApp, ...)`, and the `main()` entrypoint. Splitting these responsibilities into focused UI modules improves readability, testability, and layer boundaries.

## What Changes

- **Icons (`src/idat_sync/ui/icons.py`)**: Extract `_draw`, the four pixel-art grids (keeping `_UNCHECKED` as an explicit grid without `.replace()`), and a frozen `Icons` dataclass (`Icons.create()`).
- **Busy helper (`src/idat_sync/ui/base.py`)**: Add `AppFrame._busy(flag: tk.BooleanVar)` async context manager to replace duplicated `_active_task` / `flag` `try/finally` blocks in `SyncFrame` and `LoginFrame`.
- **CourseTree & status types (`src/idat_sync/ui/course_tree.py`)**: Extract `CourseTree(ttk.Frame)`, `RowStatus(StrEnum)`, `Progress`, and per-course `CourseNodes` tracking (`pop(course_id)` instead of dict scans), with `set_interactive(enabled: bool)`.
- **SyncFrame cleanup (`src/idat_sync/ui/sync_frame.py`)**: Split `_build_widgets` into `_build_*` helpers, `_button()`, `_lockable`, and `_update_buttons()`; extract `_run_sync()`; convert `_handle_event()` into a pure `match` dispatcher with `_on_*` methods delegating to `self.course_tree`; store `self.app: MainApp`; keep `create_sync_service` imported.
- **MFA dialog & LoginFrame (`src/idat_sync/ui/mfa_dialog.py`, `src/idat_sync/ui/login_frame.py`)**: Extract `ask_mfa_code(parent)` (keeping `LoginFrame.get_mfa_code` delegating to it), add named layout constants, use `_busy()`, store `self.app: MainApp`, and keep `ExceptionGroup` unwrapping in `LoginFrame.do_login`.
- **MainApp & entrypoint (`src/idat_sync/ui/app.py`, `src/idat_sync/main.py`)**: Rename `MainApp.__frame`/`__change` to `_frame`/`_show`, keep `AppFrame` re-exported in `ui/app.py`, and move `main()` to `src/idat_sync/main.py`.
- **Layer boundary fix (`src/idat_sync/application/sync_service.py`)**: Add `refresh: bool = False` to `IDATSync.sync_courses` (clearing `course.sections` when `refresh=True`, passed by `SyncFrame`) instead of mutating `Course.sections` in the UI.
- **Tests (`tests/test_auth.py`)**: Update `_MainApp__frame` to `_frame`, update `TestSyncFrame` to assert against `frame.course_tree` and `Icons`, and add direct unit tests for `CourseTree` and `_busy`.

### Non-Goals

- Changing `AuthService.login` or its `ExceptionGroup` propagation contract.
- Deriving `_UNCHECKED` from `_CHECKED` via string replacement.
- Adding UI-only terms like `RowStatus` to `GLOSSARY.md`.
- Changing user-facing UI behavior, layout appearance, or sync semantics.

## Capabilities

### New Capabilities

<!-- None -->

### Modified Capabilities

- `course-synchronization`: Update the Treeview UI requirement to reflect `idat_sync.main.main()`, `CourseTree` owning tree state, and `IDATSync.sync_courses(..., refresh=True)` clearing `course.sections`.
- `authentication`: Update UI decoupling/resilience requirements to reflect `MainApp._frame`, `ui/mfa_dialog.py`, `AppFrame._busy`, and `SyncFrame._update_buttons`.

## Applicable Agent-OS Standards

- `global/layer-boundaries`: Move `course.sections.clear()` out of `ui/` into `IDATSync.sync_courses`, keep `ui/` isolated from `infrastructure/`.
- `testing/composition-and-seams`: Keep `create_sync_service` in `sync_frame.py` and test `CourseTree`, `SyncFrame`, `LoginFrame`, and `MainApp` at their public seams.

## Impact

- Modified: `src/idat_sync/main.py`, `src/idat_sync/application/sync_service.py`, `src/idat_sync/ui/base.py`, `src/idat_sync/ui/app.py`, `src/idat_sync/ui/login_frame.py`, `src/idat_sync/ui/sync_frame.py`, `tests/test_auth.py`.
- Added: `src/idat_sync/ui/icons.py`, `src/idat_sync/ui/course_tree.py`, `src/idat_sync/ui/mfa_dialog.py`.
