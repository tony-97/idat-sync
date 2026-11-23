# Proposal

## Why

The desktop (`>= 960px`) layout traps `.syncbar` inside the bottom cell of the 280px left sidebar column (`grid-area: 3 / 1`), leaving only ~170px for status text and causing long course names and queue percentages to wrap onto 3–4 lines. Additionally, conditionally mounting `.selbar` when checking the first `Course` shifts the entire course tree down by 47px, and on mobile (`< 960px`) `.ctog` and `.selbar` duplicate the course header and sync buttons while `ActivityPanel` cannot be reopened once `is_syncing` becomes `False`.

## What Changes

- Span `.pathbar` (`grid-column: 1 / -1` at the top) and `.syncbar` (`grid-column: 1 / -1` at the bottom) across the full window width on desktop (`>= 960px`), applying single-line ellipsis truncation to `.sbt > b` and `white-space: nowrap` to `.sbt > span` (`N queued · M%`).
- Wrap `.pathbar` into two compact rows on narrow screens (`< 640px`) so the `Courses` toggle (`selected/total` pill), `Activity` toggle, theme toggle, sign-out button, and `Local folder` path with `Change` never crush the path text to `0px`.
- Remove `.selbar` and `.ctog` from `Sidebar.tsx`, eliminating the 47px vertical layout shift when checking/unchecking courses and removing duplicate course headers and duplicate `Sync` bars on mobile.
- Keep a permanent fixed-height sidebar header (`.chd`) with `Courses by term`, the `selected/total` pill badge, `Active term` · `Clear` (`disabled` when `0` courses are selected or while syncing), and a mobile `[x]` close button.
- Make the Course Sidebar toggleable across viewports: starts open as a collapsible `280px` left column on desktop (`>= 960px`) and closed as a mutually exclusive slide-over left drawer (`width: min(85vw, 300px)`) with a backdrop scrim on mobile (`< 960px`) that auto-closes when selecting a `Course`.
- Make `ActivityPanel` toggleable across viewports: starts closed on launch, auto-opens once on the `false -> true` transition of `is_syncing` on desktop (`>= 960px`) while respecting manual dismissal during that run, stays user-triggered on mobile (`< 960px`), can be toggled anytime in any state via the `.pathbar` `Activity` button, `.syncbar` status area, or `ActivityPanel` `[x]` close button, and keeps `ActivityPanel` open on desktop when jumping to a file (`onJumpToFile`) while closing the mobile sheet.

## Non-Goals

- Changing any Python modules under `src/idat_sync/domain/`, `src/idat_sync/application/`, `src/idat_sync/infrastructure/`, or `src/idat_sync/ui/*.py`.
- Replacing `ActivityPanel` with a bottom tabbed transfer popover or adding Pause/Resume controls.
- Persisting `folder_path` or panel open/closed state to disk across application restarts.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `course-synchronization`: Update the `Treeview course selection UI and ProgressInterceptor removal` requirement to reflect the full-width top `.pathbar` and bottom `.syncbar`, removal of `.selbar` and `.ctog`, and toggleable Course Sidebar and `ActivityPanel` across desktop and mobile viewports.

## Applicable Standards

From `agent-os/standards/index.yml`:
- `global/layer-boundaries`: Preserves strict `ui/` isolation and the single-file Vite/SolidJS bundle in `src/idat_sync/ui/index.html`.
- `testing/composition-and-seams`: Verifies bundled UI output at the `MainApp` and `src/idat_sync/ui/index.html` seams.

## Impact

- **Frontend source**: `frontend/src/App.tsx`, `frontend/src/components/Sidebar.tsx`, `frontend/src/components/SyncBar.tsx`, `frontend/src/components/ActivityPanel.tsx`, `frontend/src/components/Icons.tsx`, `frontend/src/styles/global.css`.
- **Bundled asset**: `src/idat_sync/ui/index.html`.
- **Tests**: `tests/test_auth.py` (bundled HTML and `MainApp` seam assertions).
