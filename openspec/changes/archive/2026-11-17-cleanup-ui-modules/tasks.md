# Tasks

## 1. Icons and AppFrame Busy Helper

- [x] 1.1 Extract `Icons` (`checked`, `unchecked`, `folder`, `file`, `Icons.create()`) with explicit `_CHECKED` and `_UNCHECKED` grids (no `.replace()`) into `src/idat_sync/ui/icons.py`, add a unit test in `tests/test_auth.py` verifying `Icons.create()`, and verify `pytest tests/test_auth.py` passes.
- [x] 1.2 Add `@contextlib.asynccontextmanager async def _busy(self, flag: tk.BooleanVar)` to `AppFrame` in `src/idat_sync/ui/base.py`, add unit tests in `tests/test_auth.py` covering normal reset and destroyed-widget safety, and verify `pytest tests/test_auth.py` passes.

## 2. MFA Dialog and LoginFrame Cleanup

- [x] 2.1 Extract `async def ask_mfa_code(parent: tk.Misc) -> str` with named geometry constants into `src/idat_sync/ui/mfa_dialog.py`, update `LoginFrame` in `src/idat_sync/ui/login_frame.py` to delegate `get_mfa_code` to `ask_mfa_code(self.master)`, use `_busy(self.is_login)`, store `self.app = master`, use named constants, and accept `*_: object` in `do_login`, and verify `pytest tests/test_auth.py -k "TestLoginFrame or mfa"` passes.

## 3. SyncService Refresh Boundary and CourseTree Extraction

- [x] 3.1 Add `refresh: bool = False` to `IDATSync.sync_courses` in `src/idat_sync/application/sync_service.py` (clearing `course.sections.clear()` when `refresh=True`), add a unit test in `tests/test_sync_pipeline.py` covering `refresh=True` vs `refresh=False`, and verify `pytest tests/test_sync_pipeline.py` passes.
- [x] 3.2 Create `src/idat_sync/ui/course_tree.py` with `RowStatus(StrEnum)`, `Progress`, `CourseNodes`, and `CourseTree(ttk.Frame)` (`set_courses`, `refresh_selection`, `reset_for_sync`, `show_files`, `set_file_status`, `set_course_status`, `set_interactive`), add direct unit tests for `CourseTree` in `tests/test_auth.py`, and verify `pytest tests/test_auth.py -k CourseTree` passes.

## 4. SyncFrame Refactor and MainApp/Entrypoint Wiring

- [x] 4.1 Refactor `SyncFrame` in `src/idat_sync/ui/sync_frame.py` to use `self.course_tree`, `self.app`, `_busy(self.is_syncing)`, `_build_*` layout helpers, `_button()`, `_lockable`, `_update_buttons()`, `_run_sync()` (passing `refresh=True`), and pure-dispatcher `_handle_event()` with `_on_*` methods (keeping `create_sync_service` imported), update `TestSyncFrame` in `tests/test_auth.py` for `frame.course_tree`, and verify `pytest tests/test_auth.py -k TestSyncFrame` passes.
- [x] 4.2 Rename `MainApp.__frame`/`__change` to `_frame`/`_show` in `src/idat_sync/ui/app.py` (keeping `AppFrame` re-exported), move `main()` to `src/idat_sync/main.py`, update `_MainApp__frame` references to `_frame` in `tests/test_auth.py`, and verify `pytest tests/test_auth.py` passes.

## 5. Final Verification

- [x] 5.1 Run the full test suite (`pytest`), typecheck, and lint across the repository and confirm all checks pass with zero errors.
