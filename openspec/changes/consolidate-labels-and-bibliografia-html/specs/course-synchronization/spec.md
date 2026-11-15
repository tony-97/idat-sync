# Spec Delta

## MODIFIED Requirements

### Requirement: Moodle adapter translates vendor curriculum into domain models
`MoodleAdapter` SHALL map Moodle sections, modules, and assignments into `Course`, `CourseSection`, and `CourseModule` with `inline_contents: list[StringContent]` and `contents: list[ModuleContent]`, sanitizing generated `.html` filenames and resolved `MoodleContent` filenames with `safe_path_segment`. For non-`Bibliografía` sections, `MoodleAdapter` SHALL consolidate all `modname == "label"` modules with non-empty `description` into a single section-level `StringContent` named `info.html` rather than per-label `CourseModule` items; for sections whose normalized name is `"bibliografia"`, `MoodleAdapter` SHALL collect all non-empty `label` descriptions and `url` entries (`name` and `fileurl`) in module order into a single section-level `StringContent` named `bibliografia.html` without emitting `info.html` or `ModuleContent` items for those URLs. `domain/course.py` MUST NOT define `Assignment`, `Course.assignments`, `CourseSection.summary`, `CourseModule.modname`, or `CourseModule.description`. Verified at the `MoodleAdapter` public seam.

#### Scenario: Generating section summary inline content
- **WHEN** `MoodleAdapter` converts a raw section with `id`, `name`, and `summary` (and no non-empty labels or `Bibliografía` entries)
- **THEN** the resulting `CourseSection` has `inline_contents` set to `[StringContent(Path(safe_path_segment(f"{section_name}-[{section_id}]-resumen.html")), 0, [summary])]` (and when labels or `Bibliografía` entries are also present, `resumen.html` precedes `info.html` or `bibliografia.html`)

#### Scenario: Mapping label modules with non-empty and empty descriptions
- **WHEN** `MoodleAdapter` converts a non-`Bibliografía` section containing `modname == "label"` modules (with non-empty or empty `description`)
- **THEN** all non-empty `label` `description` strings are appended in module order into a single `StringContent(Path("info.html"), 0, ...)` on `CourseSection.inline_contents`, empty-description labels are ignored, no per-label `<module_name>.html` `CourseModule` items are emitted, and no `info.html` is emitted when a section has no non-empty `label` descriptions

#### Scenario: Dumping Bibliografía labels and URLs into bibliografia.html
- **WHEN** `MoodleAdapter` converts a section whose trimmed, lowercase, accent-stripped name equals `"bibliografia"` containing `modname == "label"` modules and `modname == "url"` modules (each with `name` and `contents[0]["fileurl"]`)
- **THEN** non-empty `label` `description` HTML and HTML-escaped `url` entries (`name` + `fileurl`) are collected in module order into a single `StringContent(Path("bibliografia.html"), 0, ...)` on `CourseSection.inline_contents`, no `info.html` is emitted, no `ModuleContent` items are emitted for those `url` modules (so `UrlContentStrategy` is never invoked for them), and any non-`label`/non-`url` modules in the section still map into `CourseSection.modules`

#### Scenario: Merging assignment intro and attachments by cmid
- **WHEN** `MoodleAdapter` converts a raw module with `modname == "assign"` and `id` matching an assignment's `cmid`
- **THEN** `CourseModule.inline_contents` is `[StringContent(Path(safe_path_segment(f"{assign_name}-[{assign_id}]-intro.html")), 0, [assign_intro])]` and `CourseModule.contents` contains the mapped `ModuleContent` items from `introattachments` (or both are `[]` when no matching assignment exists)

#### Scenario: Sanitizing unsafe resource and inline filenames while preserving accents
- **WHEN** `MoodleAdapter.list_url_content` or inline content mapping receives resource `"Guía: Tema 1/2?.pdf"`, `"CON.txt"`, or assignment `"Aviso: CON/1"`
- **THEN** the resulting `MoodleContent.path` values are `Path("Guía Tema 1 2.pdf")` and `Path("_CON.txt")`, and the assignment `StringContent.path` is sanitized with `safe_path_segment` while preserving the `-[<assign_id>]-intro.html` suffix
