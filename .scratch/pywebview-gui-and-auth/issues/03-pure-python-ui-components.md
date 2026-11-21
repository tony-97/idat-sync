# 03: Pure-Python UI Component Controllers (`AppFrame`, `LoginFrame`, `CourseTree`, `SyncFrame`)

**OpenSpec Change:** `openspec/changes/pywebview-gui-and-auth`

**What to build:** Refactor the UI components (`AppFrame`, `LoginFrame`, `CourseTree`/`RowStatus`/`Progress`, and `SyncFrame`) from Tkinter widgets into pure-Python component controllers that manage login validation, inline async MFA futures, hierarchical course/folder/file sync progress serialization (`to_dict()`), desktop/Android sync folder selection, and resilient error recovery without any `tkinter` or `async_tkinter_loop` dependencies, while removing the obsolete Tkinter `mfa_dialog.py` and `icons.py` modules.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] `AppFrame` tracks a single active `asyncio.Task`, provides the `_busy` async context manager, cancels and awaits the active task in `async def aclose()`, does not inherit from `tk.Frame`, and does not define `exit`.
- [x] `LoginFrame` logs out on initialization (catching `CredentialStoreError` into `error`), computes `can_login` (`bool(username.strip()) and bool(password) and not is_login`), manages MFA prompts via an internal `asyncio.Future[str]` in `await get_mfa_code()` (resolved by `submit_mfa(code)` or `cancel_mfa()` -> `""` and cleaned up on cancellation), guards against blank or concurrent `do_login()` submissions, catches `IDATSyncError` into `error`, and transitions to sync on success.
- [x] `CourseTree` (`RowStatus`, `Progress`) tracks course rows, nested folder nodes, and leaf file rows purely in memory without `tkinter` or `Icons`, appends discovered file batches additively in `show_files`, updates file and parent course completion percentages, and serializes tree state via `to_dict()`.
- [x] `SyncFrame` orders in-progress courses first (checked by default), supports `select_in_progress()`, `select_all()`, `clear_selection()`, `toggle_course(course_id)`, `set_folder_path(path)`, and `browse_folder()` (using the attached window's folder dialog on Desktop and falling back to the Android Downloads directory when unavailable), retries service creation on `do_sync()` after transient startup failures, preserves stored `Credentials` on `CredentialsExpiredError` or startup/sync errors, and closes transports cleanly in `aclose()` and `do_logout()`.
- [x] Legacy Tkinter modules `src/idat_sync/ui/mfa_dialog.py` and `src/idat_sync/ui/icons.py` are deleted.
