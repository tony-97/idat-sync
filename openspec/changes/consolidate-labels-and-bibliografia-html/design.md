# Design

## Context

`MoodleAdapter` (`src/idat_sync/infrastructure/moodle_adapter.py`) converts raw Moodle `core_course_get_contents` payloads into `CourseSection` and `CourseModule` domain models. `CourseRepository._get_section_content` places `CourseSection.inline_contents` directly under `<course_dir>/<safe_path_segment(section.name)>/`, and `StringDownloader` writes `"".join(content.contents).strip().encode("utf-8")` atomically via `.part` files. Because `CourseSection.inline_contents` and `StringDownloader` already support arbitrary section-level HTML artifacts, consolidating `label` modules into `info.html` and `Bibliografía` `label`/`url` modules into `bibliografia.html` can live entirely inside `MoodleAdapter` without touching `domain/`, `CourseRepository`, or `DownloadManager`. See `proposal.md` for problem background.

## Goals / Non-Goals

**Goals:**
- Consolidate non-empty `modname == "label"` descriptions in each regular section into one `<section>/info.html` (`StringContent`).
- Consolidate `label` descriptions and `url` module links (`name` + `fileurl`) in `Bibliografía` sections into one `<section>/bibliografia.html` (`StringContent`) without emitting `ModuleContent` items for `Bibliografía` URLs.
- Preserve existing section summary (`-[<sec_id>]-resumen.html`), assignment (`-[<assign_id>]-intro.html` + `introattachments`), `resource`, `folder`, and non-`Bibliografía` `url` behavior.

**Non-Goals:**
- PDF conversion (`xhtml2pdf`, `weasyprint`, or Playwright) or rewriting authenticated `pluginfile.php` image URLs inside label HTML.
- Changes to `domain/course.py`, `CourseRepository`, `UrlContentStrategy` implementations, `DownloadManager`, `StringDownloader`, or `ui/`.

## Decisions

### 1. Section-level `inline_contents` consolidation in `MoodleAdapter`
- **Choice**: Handle `label` aggregation (and `Bibliografía` `label` + `url` aggregation) inside `MoodleAdapter._to_course_section`, emitting `StringContent(Path("info.html"), 0, ...)` or `StringContent(Path("bibliografia.html"), 0, ...)` on `CourseSection.inline_contents` and omitting the consumed `label` (and `Bibliografía` `url`) modules from `CourseSection.modules`.
- **Rationale**: In `all_courses_contents.json`, all 472 `label` modules carry only `description` (0 have `contents` or `url`). Moving them onto `CourseSection.inline_contents` keeps vendor-specific Moodle layout rules sealed inside the infrastructure ACL (per `docs/adr/0001-hexagonal-ports-and-adapters.md`), avoids empty `CourseModule` folders, and requires zero changes to `CourseRepository`. Fixed filenames `info.html` and `bibliografia.html` are OS-safe ASCII segments under `docs/adr/0002-path-sanitization-compatibility.md`.
- **Alternative considered**: Creating a synthetic `CourseModule` or adding a section-level aggregation pass in `CourseRepository` — rejected because `CourseModule.name` creates an extra subdirectory and `CourseRepository` should remain agnostic of Moodle `modname` semantics.

### 2. Detecting `Bibliografía` sections and rendering `bibliografia.html`
- **Choice**: Identify `Bibliografía` sections via `unicodedata.normalize("NFKD", section_name)` with combining marks stripped, `.strip().lower() == "bibliografia"`. Walk `raw_section.get("modules", [])` in order: for `modname == "label"` with non-empty `description`, append the HTML fragment; for `modname == "url"`, extract `name` and each `fileurl` in `contents` (typically one entry) and render HTML-escaped link markup (`<p><a href="{escaped_url}">{escaped_name}</a></p>`, falling back to escaped `<p>{escaped_name}</p>` or `<p><a href="{escaped_url}">{escaped_url}</a></p>` if one field is missing). Keep non-`label`/non-`url` modules mapped into `CourseSection.modules` normally.
- **Rationale**: All 33 `Bibliografía` sections in `all_courses_contents.json` consist exclusively of `label` headings (e.g., `"Base de datos E-Libro:"`) interleaved with `url` citations to external library portals (`elibro.net`, `ebooks7-24.com`, `ebooks.editorialmacro.com`, `bc.vitalsource.com`). Rendering them into `bibliografia.html` preserves reading order and headings while ensuring `CourseRepository.list_url_content` never routes those external library URLs through `UrlContentStrategy`. Note that course `32500` uses `'Referencias bibliográficas'` with `modules: []` and HTML in `section.summary`, which continues to emit `Referencias bibliográficas-[id]-resumen.html`.
- **Alternative considered**: Adding a `UrlContentStrategy` for ebook domains — rejected because URLs are external login-walled portals rather than downloadable files, and per-URL handling loses `label` category headings.

### 3. Keep `.html` output via `StringDownloader`
- **Choice**: Keep `.html` rather than converting HTML to PDF with `xhtml2pdf`, `weasyprint`, or Playwright.
- **Rationale**: `StringDownloader` already writes `.html` atomically with zero extra dependencies. On Windows, `weasyprint` requires external GTK3/Pango/Cairo DLLs, `xhtml2pdf` adds ~6 dependencies and struggles with Moodle CSS/HTML quirks, and 47 labels embed `<img src="https://aulavirtual.idat.edu.pe/pluginfile.php/...">` links that require Moodle token auth. No new ADR is needed because this output format choice is localized to `MoodleAdapter` and easily reversible.

### 4. Test seams
- Verify behavior at the `MoodleAdapter` public seam (`populate_course_sections` / `get_course_sections` with `MagicMock(spec=MoodleClient)`) in `tests/test_moodle_adapter.py` and end-to-end section projection at `CourseRepository.get_course_content` in `tests/test_course_repository.py`. Remove direct per-label `_to_course_module` expectations that assumed `<module_name>.html` on `CourseModule`.

## Risks / Trade-offs

- **Previously synced local label files remain untouched** → `IDATSync` leaves `LOCAL_ONLY` files on disk, so old `<label>/<label>.html` files from earlier syncs will not be deleted automatically; no user data is lost and new syncs write `info.html` / `bibliografia.html`.
- **Special HTML characters in bibliography citations or URLs** → Mitigation: Escape `url` module `name` and `fileurl` with `html.escape(..., quote=True)` before interpolating into `<a>` tags, while preserving `label` `description` as raw HTML.
- **Non-`label`/non-`url` module inside a `Bibliografía` section** → Mitigation: Only consume `label` and `url` modules into `bibliografia.html`; delegate any other `modname` to `_to_course_module` so resources/assignments are never dropped.
