# Design

## Context

See [proposal.md](proposal.md) for motivation. Currently `ui/sync_frame.py` is 587 lines, `ui/login_frame.py` embeds the MFA `Toplevel` dialog, `ui/app.py` defines `main()` and uses `__frame` / `__change` name mangling, and `SyncFrame.do_sync()` mutates `course.sections.clear()`. Meanwhile `ui/base.py` (`AppFrame`) and top-level frame imports in `ui/app.py` are already in place, and existing architectural boundaries are recorded in [ADR 0001](../../../docs/adr/0001-hexagonal-ports-and-adapters.md).

## Goals / Non-Goals

**Goals:**
- Separate UI concerns into focused modules: `ui/icons.py`, `ui/base.py`, `ui/mfa_dialog.py`, `ui/course_tree.py`, `ui/login_frame.py`, `ui/sync_frame.py`, `ui/app.py`, and `main.py`.
- Replace repeated `_active_task` + `BooleanVar` `try/finally` boilerplate with `AppFrame._busy`.
- Replace `list[int]` progress pairs and string literals with `Progress` and `RowStatus(StrEnum)`, and store tree node IIDs per course (`CourseNodes`) so `pop(course_id)` replaces full-dict scans.
- Move `course.sections.clear()` out of `ui/` into `IDATSync.sync_courses(..., refresh=True)` to respect `global/layer-boundaries`.

**Non-Goals:**
- Changing `AuthService.login` or its `ExceptionGroup` propagation.
- Generating `_UNCHECKED` via `.replace(...)` on `_CHECKED`.
- Adding UI-only terms (`RowStatus`, `Progress`) to `GLOSSARY.md`.

## Decisions

1. **Explicit `_UNCHECKED` grid in `Icons` (`ui/icons.py`)**: Keep both `_CHECKED` and `_UNCHECKED` as explicit 16x16 ASCII grids inside `Icons` (`Icons.create()`), rather than deriving `_UNCHECKED` via `.replace("W", "F")`. `CourseTree` holds `self.icons: Icons` (defaulting to `Icons.create()` if not injected) so Tkinter does not garbage-collect the `PhotoImage` instances and `CourseTree` is testable standalone.
2. **`AppFrame._busy(flag: tk.BooleanVar)` (`ui/base.py`)**: An `@contextlib.asynccontextmanager` sets `self._active_task = asyncio.current_task()`, `flag.set(True)`, and in `finally:` clears `self._active_task = None` and resets `flag.set(False)` only if `self.winfo_exists()`. `LoginFrame.do_login` awaits `self.auth.login(...)` directly inside `_busy` instead of wrapping it in an extra `asyncio.create_task`, because `async_handler` already schedules `do_login` as a task and `aclose()` cancels `self._active_task`.
3. **`CourseTree(ttk.Frame)` (`ui/course_tree.py`) & `RowStatus`**:
   - Name the UI enum `RowStatus(StrEnum)` (not `Status`) so it does not collide with `GLOSSARY.md`'s `SyncStatus`.
   - Track per-course nodes in `_nodes: dict[int, CourseNodes]` (`iid`, `folder_iids`, `file_iids`, `progress: Progress`) and `_file_to_course: dict[Path, tuple[int, str]]` (or per-course cleanup on `show_files` via `pop`), eliminating O(N) scans.
   - Expose `set_interactive(enabled: bool)` driven by `SyncFrame._update_buttons` rather than passing `is_syncing` into `CourseTree`.
   - `SyncFrame` exposes `self.course_tree: CourseTree` without legacy `frame.tree` / `toggle_course_row` pass-throughs.
4. **MFA popup (`ui/mfa_dialog.py`) & `LoginFrame` (`ui/login_frame.py`)**: Extract `async def ask_mfa_code(parent: tk.Misc) -> str` into `ui/mfa_dialog.py` with named geometry/dimension constants; keep `LoginFrame.get_mfa_code` as a one-line delegator so `AuthService.login` callback wiring and existing tests remain clean. Store `self.app: MainApp = master` instead of `cast("MainApp", self.master)`.
5. **`MainApp` (`ui/app.py`) & `main()` (`main.py`)**: Rename `__frame` / `__change` to `_frame` / `_show` (no subclass collision exists), keep `AppFrame` re-exported in `ui/app.py`, and move `main()` into `src/idat_sync/main.py`.
6. **`IDATSync.sync_courses(..., refresh: bool = False)` (`application/sync_service.py`)**: Add `refresh: bool = False` (default `False` so existing `sync_courses` callers and tests passing pre-populated `Course.sections` are unaffected). `SyncFrame._run_sync` passes `refresh=True`, and `sync_courses` clears `course.sections.clear()` when `refresh=True`.

## Risks / Trade-offs

- **Test coupling in `tests/test_auth.py`**: Existing tests reference `app._MainApp__frame`, `frame.tree`, `frame._checked_icon`, and `frame.toggle_course_row`. → Update those assertions in `tests/test_auth.py` to use `app._frame`, `frame.course_tree.tree`, `frame.course_tree.icons.checked`, and `CourseTree` / `on_toggle` directly, and keep `create_sync_service` imported in `ui/sync_frame.py`.
- **`course-synchronization` / `authentication` spec drift**: Existing specs referenced `ui.app.main()`, `SyncFrame._file_iids`, `MainApp.__frame`, and `validate_buttons`. → Captured via `MODIFIED` delta specs in this change.
