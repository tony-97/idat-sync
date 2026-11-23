# Tasks

## 1. Full-Width SyncBar, Zero-Shift Sidebar Header, and Toggleable Panels

- [x] 1.1 Write a failing test in `tests/test_auth.py` at the bundled `src/idat_sync/ui/index.html` (`load_ui_html()`) seam asserting zero `.selbar` or `.ctog` selectors/elements, full-width `.pathbar` and `.syncbar` desktop grid rules (`grid-column:1/-1` or `1 / -1`), single-line ellipsis truncation on `.sbt>b` / `.sbt > b`, two-row mobile `.pathbar` wrapping (`@media(max-width:639px)` or `@media (max-width: 639px)`), fixed-height `.chd` selection controls (`Active term` and `Clear`), and toggleable Course Sidebar and `ActivityPanel` controls across desktop and mobile viewports; then update `frontend/src/App.tsx`, `frontend/src/components/Sidebar.tsx`, `frontend/src/components/SyncBar.tsx`, `frontend/src/components/ActivityPanel.tsx`, `frontend/src/components/Icons.tsx`, and `frontend/src/styles/global.css`, rebuild `src/idat_sync/ui/index.html` via `npm run build` in `frontend/`, and verify the test passes.

## 2. Full Verification Suite

- [x] 2.1 Run the frontend TypeScript check and build (`npm run build` in `frontend/`), the full Python test suite (`pytest`), typecheck (`mypy`), and linters (`ruff check`, `pylint`) and verify all checks pass with zero errors.
