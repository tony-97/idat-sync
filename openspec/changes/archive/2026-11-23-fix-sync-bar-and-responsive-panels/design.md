# Design

## Context

See [proposal.md](./proposal.md) for motivation and capability impact. Long-lived frontend packaging and reactive store architecture are recorded in [ADR-0007](../../../docs/adr/0007-pywebview-and-static-astro-ui.md) and [ADR-0008](../../../docs/adr/0008-vite-solidjs-single-file-ui.md).

Key codebase constraints:
- All backend and UI controller actions (`select_in_progress`, `select_all`, `clear_selection`, `toggle_course`, `toggle_term`, `sync`, `sync_course`, `cancel_sync`, `retry_load`, `browse_folder`, `logout`) are already exposed by `_JsApi` in `src/idat_sync/ui/app.py` and `SyncFrame` in `src/idat_sync/ui/sync_frame.py`.
- `src/idat_sync/ui/index.html` is a single self-contained asset compiled from `frontend/` via `npm run build` (`tsc -b && vite build` with `vite-plugin-singlefile`).

## Goals / Non-Goals

**Goals:**
- Give `.pathbar` (top toolbar) and `.syncbar` (bottom sync/status bar) 100% of the window width (`grid-column: 1 / -1` on `>= 960px`) and prevent multi-line text wrapping in `.sbt`.
- Eliminate the 47px vertical layout shift when checking/unchecking courses and eliminate duplicate mobile headers/sync buttons by removing `.selbar` and `.ctog`.
- Make both the Course Sidebar (`<nav class="courses">`) and `ActivityPanel` (`<aside class="act">`) toggleable across desktop (`>= 960px`) and mobile (`< 960px`) viewports in all sync states.

**Non-Goals:**
- Modifying any Python module under `src/idat_sync/`.
- Adding new `_JsApi` methods or changing the `SyncFrame.to_dict()` payload schema.

## Decisions

1. **Full-Width Top `.pathbar` and Bottom `.syncbar` + Flex Center Workspace**:
   - On desktop (`>= 960px`), `.body` remains a 3-row layout (`grid-template-rows: auto 1fr auto`), with `.pathbar` occupying row 1 across all columns (`grid-column: 1 / -1`) and `.syncbar` occupying row 3 across all columns (`grid-column: 1 / -1`).
   - Instead of fixed 3-column grid tracks that reserve blank space when a side panel is collapsed, either `.body` uses CSS grid columns driven by panel visibility or a middle flex row (or `grid-template-columns: auto minmax(0, 1fr) auto`) where `<nav class="courses">` is `280px` when open (`display: none` when collapsed on desktop), `<main class="main">` is `1fr` (`minmax(0, 1fr)`), and `<aside class="act">` is `330px` when open (`display: none` when collapsed on desktop). Using `grid-template-columns: auto minmax(0, 1fr) auto` on `.body` at `>= 960px` with `.pathbar { grid-column: 1 / -1; grid-row: 1; }`, `.courses { grid-column: 1; grid-row: 2; width: 280px; }`, `.main { grid-column: 2; grid-row: 2; }`, `.act { grid-column: 3; grid-row: 2; width: 330px; }`, and `.syncbar { grid-column: 1 / -1; grid-row: 3; }` allows collapsing `.courses` or `.act` (`display: none` when closed on desktop) to automatically collapse their `auto` track to `0px` so `.main` expands seamlessly without wrapper DOM churn.
   - *Alternative rejected*: Keeping `.syncbar` in `grid-area: 3 / 1` and widening the sidebar (still breaks if the Course Sidebar is collapsed and wastes horizontal space).

2. **Two-Row Responsive `.pathbar` on Narrow Screens (`< 640px`)**:
   - `.pathbar` holds:
     - Left: `Courses` toggle button (`Courses` label + `selectedCount/totalCount` badge pill, e.g., `1/7`, active style when the sidebar is open).
     - Middle: folder icon + `Local folder` path (`.pp`) + `Change` button.
     - Right (`.pactions`): `Activity` toggle button (with live count badge when active/pending/failed `> 0`, active style when `ActivityPanel` is open), Theme toggle (`moon`), and Sign out (`logout`).
   - On `< 640px`, `.pathbar` wraps (`flex-wrap: wrap`): the folder path container (`.pathfolder` / `.pp` + `Change`) takes a full-width second row (`order: 3; flex: 1 0 100%`), while Row 1 keeps `[Courses 1/7]` on the left and `[Activity] [Theme] [Sign out]` on the right. On `>= 640px`, all controls sit on a single horizontal row.
   - *Alternative rejected*: Single non-wrapping row on `< 640px` (crushes `.pp` to `0px` on `360px` mobile screens).

3. **Zero-Shift Course Sidebar Header (`.chd`) and Removal of `.selbar` & `.ctog`**:
   - Delete `.selbar` and `.ctog` from `Sidebar.tsx` and `global.css`.
   - In `.chd`, render a permanent fixed-height row:
     - Left: `<b>Courses by term</b>` + `<span class="bdg">{selectedCount()}/{appState.courses.length}</span>`.
     - Right: `<button class="lnk" onClick={() => callApi("select_in_progress")}>Active term</button>`, separator `·`, `<button class="lnk" disabled={selectedCount() === 0 || appState.is_syncing} onClick={() => callApi("clear_selection")}>Clear</button>`, and the mobile `[x]` drawer close button (`class="btn ico mo"`).
   - Because `.chd` height never changes and no element mounts above `.cl`, checking/unchecking the first course shifts the course list by `0px`.

4. **Viewport-Aware Course Sidebar & `ActivityPanel` Toggling (Option A)**:
   - Track panel states in `App.tsx`:
     - `desktopSidebarOpen` (default `true`) and `mobileSidebarOpen` (default `false`).
     - `activityOpen` (default `false`).
   - **Auto-open `ActivityPanel` on desktop sync start**: A `createEffect` in `App.tsx` tracks the previous `appState.is_syncing` boolean (`wasSyncing`). Only when `!wasSyncing && appState.is_syncing` transitions `false -> true` AND `window.matchMedia("(min-width: 960px)").matches` is `true`, call `setActivityOpen(true)`. If the user closes `ActivityPanel` mid-sync, `wasSyncing` is already `true`, so subsequent state pushes during that sync run do not reopen it.
   - **Mutual exclusion on mobile (`< 960px`)**:
     - Toggling `Courses` on `< 960px` flips `mobileSidebarOpen` and, when opening, sets `setActivityOpen(false)`. Selecting a course in `Sidebar` sets `setMobileSidebarOpen(false)`.
     - Toggling `Activity` (via `.pathbar` `Activity` button, clicking `.syncbar`'s status area/chevron in **any** sync state, or clicking `ActivityPanel`'s `[x]` button) flips `activityOpen` and, when opening on `< 960px`, sets `setMobileSidebarOpen(false)`.
   - **`ActivityPanel` close button & `jumpToFile`**:
     - Remove the `.mo` class from `ActivityPanel`'s `[x]` close button so desktop users can also close the right-hand `ActivityPanel` column.
     - In `jumpToFile(courseId, fileId)`, close `ActivityPanel` only when on mobile (`if (!window.matchMedia("(min-width: 960px)").matches) setActivityOpen(false)`), keeping `ActivityPanel` open on desktop.

5. **Single-Line Truncation and Always-Clickable Status Area in `.syncbar`**:
   - Style `.sbt > b` with `min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;` and `.sbt > span` with `flex: none; white-space: nowrap;`.
   - Attach `onClick={() => props.onToggleActivity()}` to `.syncbar` (with `e.stopPropagation()` on the `Sync`, `Cancel`, `Retry`, and `Sign out` action buttons) and render the chevron/activity indicator in all states so users on mobile and desktop can always open or reopen `ActivityPanel` from `.syncbar` even after sync completes or fails.

## Risks / Trade-offs

- [Window resize across the `960px` breakpoint while a mobile drawer is open] → Because `desktopSidebarOpen` (`>= 960px`) and `mobileSidebarOpen` (`< 960px`) are tracked cleanly (and mobile scrims are hidden via `@media (min-width: 960px) { .scrim, .mo { display: none; } }`), resizing across `960px` never leaves a stuck scrim on desktop.
- [Clicking `Sync` / `Cancel` / `Retry` inside `.syncbar` accidentally toggling `ActivityPanel`] → Stop event propagation (`e.stopPropagation()`) on every action `<button>` inside `.syncbar` so only clicks on the status body/chevron toggle `ActivityPanel`.

## Modules Touched & Confirmed Public Test Seams

- **Frontend modules (6)**:
  - `frontend/src/App.tsx`
  - `frontend/src/components/Sidebar.tsx`
  - `frontend/src/components/SyncBar.tsx`
  - `frontend/src/components/ActivityPanel.tsx`
  - `frontend/src/components/Icons.tsx`
  - `frontend/src/styles/global.css`
- **Bundled single-file UI asset (1)**:
  - `src/idat_sync/ui/index.html`
- **Python modules touched**: None (`0`).
- **Confirmed public test seams (2)**:
  1. `src/idat_sync/ui/index.html` & `MainApp` (`tests/test_auth.py`): Verifies the compiled single-file bundle contains the full-width grid layout rules, truncation rules, toggleable panel selectors, and zero `.selbar` or `.ctog` artifacts.
  2. `frontend/` TypeScript & Vite build (`npm run build` in `frontend/`): Verifies zero TypeScript errors (`tsc -b`) and clean single-file bundle generation.
