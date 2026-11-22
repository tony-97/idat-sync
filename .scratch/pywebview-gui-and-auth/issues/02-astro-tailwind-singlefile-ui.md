# 02: Static Single-File Astro + Tailwind Frontend Bundle

**OpenSpec Change:** `openspec/changes/pywebview-gui-and-auth`

**What to build:** Build the application's single-page user interface using modular Astro and Tailwind CSS components (`LoginForm`, `MfaModal`, `FolderBar`, `CourseToolbar`, `CourseTree`, `ActionFooter`) that compile at build time via `vite-plugin-singlefile` into a single self-contained HTML bundle (`src/idat_sync/ui/index.html`) ready to be loaded directly into `pywebview` via `html=...` with zero Node.js runtime and zero local HTTP server.

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] The Astro + Tailwind CSS frontend is organized into modular components for login (`LoginForm` with `<Enter>` submit, error banner, and disabled state while blank or logging in), MFA (`MfaModal` overlay with 6-digit input, `<Enter>` submit, and cancel/dismiss), folder selection (`FolderBar` with path display and `Browse...` trigger), course filtering (`CourseToolbar` with `In Progress`, `Select All`, and `Clear` buttons), hierarchical course/folder/file progress (`CourseTree` with checkboxes, collapsible folders, status badges, and percentage progress), and actions/status (`ActionFooter` with `Logout`, `Sync`, and status bar).
- [x] The frontend communicates with Python exclusively through `window.pywebview.api` action calls and reactive `window.__onPythonState(state)` state updates without requiring any external or localhost HTTP server at runtime.
- [x] Running the frontend build produces a non-empty, self-contained single-file HTML bundle at `src/idat_sync/ui/index.html` containing all compiled HTML, Tailwind CSS, and client JavaScript inline.
