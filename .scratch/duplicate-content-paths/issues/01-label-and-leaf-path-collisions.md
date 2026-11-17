# 01 - Duplicate `Content.path` collisions (Moodle `'label'` and leaf path collisions)

Status: ready-for-agent
Source: `/ponytail-audit` Finding `#8` (resolved during `/grill-with-docs`)

## Summary

`SyncDiff.from_trees` (`src/idat_sync/domain/sync.py`) keys remote items by `Content.path`:

```python
remote_map: dict[Path, Content] = {c.path: c for c in remote_contents}
```

When two distinct items inside the same `Course` resolve to the same local `Path`, the later item silently overwrites the earlier one. In the local `all_courses_contents.json` snapshot (35 courses), this happens **23 times** (all `'label'` pairs inside the same `Course` and `CourseSection`; 21 pairs have distinct HTML bodies, 2 pairs have identical HTML bodies).

## How Paths Are Built

For Moodle course materials (`IDATSync.sync_courses` -> `CourseRepository.get_course_content` -> `MoodleAdapter._to_course_module`):

```text
root_path / safe_filename(course.name) / safe_path_segment(section.name) / safe_path_segment(module.name) / <item_path>
```

Each segment passes through `_sanitize_segment(..., max_len=100)` (`src/idat_sync/domain/path_utils.py`):
- `course.name` uses `safe_filename` (ASCII-only, `max_len=100`).
- `section.name`, `module.name`, and item filenames use `safe_path_segment` (Unicode preserved, extension preserved, `max_len=100`).
- Inside `_to_course_module`:
  - `'label'` -> `StringContent` named `safe_path_segment(f"{module_name}.html")` (no `module.id`).
  - `'assign'` -> `<assign_name>-[<assign_id>]-intro.html` plus attachment `filename`s (attachments have no ID).
  - `'resource'`, `'folder'`, `'url'` -> `UrlContentStrategy` items (`filename` / SharePoint subtree); `MoodleAdapter._to_module_content` ignores Moodle's `filepath`.
  - Section summary (`section.inline_contents`) -> `<section_name>-[<sec_id>]-resumen.html` directly under the section folder.
- Recordings (`SharepointApiAdapter.fetch_recordings`):
  `root_path / safe_filename(course.name) / recordings_folder_name / safe_path_segment(f"{week_prefix} {week}") / safe_path_segment(file_name)`.

## Why Different Moodle Modules Collide

Moodle's JSON tree is unique by **numeric ID** (`section.id`, `module.id`), not display name (`module.name`). For `'label'` modules, Moodle either truncates long HTML text (~50 chars + `...`) or assigns a generic name (`Etiqueta` / `Área de texto (Etiqueta)`), and both folder and filename omit `module.id`.

Example in `Course(id=26550)` / `CourseSection(id=451861, name='Evaluación Final')`:

```python
# Module #1 (evaluación preliminar, desc len=442)
{
    "id": 1229747,
    "modname": "label",
    "name": "Aquí encontrarás la rúbrica que se empleará para t...",
}

# Module #2 (evaluación definitiva, desc len=429)
{
    "id": 1229750,
    "modname": "label",
    "name": "Aquí encontrarás la rúbrica que se empleará para t...",
}
```

Both produce the identical full leaf path under `course_dir`:

```text
I.01.2025-IIA LECTURA COMPRENSIVA Y ESCRITURA/Evaluación Final/Aquí encontrarás la rúbrica que se empleará para t/Aquí encontrarás la rúbrica que se empleará para t.html
```

Module `1229750` silently overwrites `1229747` in `remote_map`, dropping `"evaluación preliminar"`.

## Observed Cases in `all_courses_contents.json`

- `safe_filename(course.name)` duplicates across the 35 courses: **0**
- `safe_path_segment(section.name)` duplicates within any course: **0**
- Duplicate `safe_path_segment(module.name)` groups within a section: **25**
  - **23 `'label'` leaf-path collisions** (listed below)
  - **1 `'resource'` + `'folder'` shared folder (NOT a leaf collision):** `Course(46824)` / `Section(735224, 'Fase 2')` has `mod=2135696` (`resource`, `'Rup'`, `UML_RUP_Analisis_y_diseno.pptx`) and `mod=2135705` (`folder`, `'Rup'`, `Manual de RUP y Modelado de Software - Tomo I..V.pdf`). They share `.../Fase 2/Rup/` cleanly with 6 distinct filenames.
  - **1 `'url'` duplicate (`elibro.net`, currently skipped):** `Course(31566)` / `Section(558064, 'Bibliografía')` has `mod=1505461` and `mod=1505466` sharing identical 167-char `name` and `filename` (`Gamero Burón, C. (2017). Estadística I...`) and different `elibro.net` URLs. Currently neither emits a `Content` item because no `UrlContentStrategy` handles `elibro.net`.

### All 23 `'label'` Collision Groups

| # | Course (`id`) & `course_dir` | Section (`id`) | Colliding `module.id`s | Leaf filename (under `<course_dir>/<section>/<stem>/`) | HTML diff? |
|---|---|---|---|---|---|
| 1 | `26550` `I.01.2025-IIA LECTURA COMPRENSIVA Y ESCRITURA` | `Evaluación Final` (`451861`) | `1229747`, `1229750` | `Aquí encontrarás la rúbrica que se empleará para t.html` | Yes (442 vs 429) |
| 2 | `26548` `I.01.2025-IIA DESARROLLO WEB` | `Evaluación Parcial` (`451704`) | `1229304`, `1229307` | `Aquí encontrarás la rúbrica que se empleará para t.html` | Yes (442 vs 429) |
| 3 | `26548` `I.01.2025-IIA DESARROLLO WEB` | `Evaluación Final` (`451708`) | `1229316`, `1229319` | `Aquí encontrarás la rúbrica que se empleará para t.html` | Yes (442 vs 429) |
| 4 | `46823` `IV.01.2026-III PATRONES DE DISENO DE SOFTWARE` | `Bibliografía` (`735318`) | `2045249`, `2045255` | `Bibliografía virtual básica.html` | Yes (E-Libro vs Macro) |
| 5 | `46822` `IV.01.2026-III DESARROLLO DE INTERFACES 3` | `Evaluación Parcial` (`735280`) | `2045139`, `2045142` | `Aquí encontrarás la rúbrica que se empleará para t.html` | Yes (442 vs 429) |
| 6 | `46821` `IV.01.2026-III DESARROLLO DE LOS COMPONENTES DEL NEGOCIO` | `Evaluación Parcial` (`735255`) | `2045061`, `2045064` | `Aquí encontrarás la rúbrica que se empleará para t.html` | Yes (442 vs 429) |
| 7 | `46821` `IV.01.2026-III DESARROLLO DE LOS COMPONENTES DEL NEGOCIO` | `Bibliografía` (`735260`) | `2045077`, `2045082` | `Bibliografía virtual básica.html` | Yes (E-Libro vs Macro) |
| 8 | `46819` `IV.01.2026-III INNOVACION CENTRADA EN PERSONAS` | `Bibliografía` (`735344`) | `2045331`, `2045343` | `Bibliografía virtual básica.html` | Yes (E-Libro vs Macro) |
| 9 | `40841` `III.01.2026-II ANALISIS Y DISENO DE SISTEMAS ORIENTADOS A OBJETOS` | `Bibliografía` (`679376`) | `1865111`, `1865116` | `Bibliografía virtual básica.html` | Yes (E-Libro vs Macro) |
| 10 | `40839` `III.01.2026-II PROGRAMACION DE BASE DE DATOS` | `Bibliografía` (`679484`) | `1865455`, `1865460` | `Bibliografía virtual básica.html` | Identical (80 vs 80) |
| 11 | `40838` `III.01.2026-II INVESTIGACION ETICA Y RESPONSABILIDAD CIUDADANA` | `Evaluación Final` (`679459`) | `1865369`, `1865372` | `Aquí encontrarás la rúbrica que se empleará para t.html` | Yes (442 vs 429) |
| 12 | `40836` `III.01.2026-II SEMINARIO SOBRE CREATIVIDAD E INNOVACION` | `Presentación` (`683832`) | `1877820`, `1877824` | `Área de texto (Etiqueta).html` | Yes (`<img>` vs `Guía virtual`) |
| 13 | `40836` `III.01.2026-II SEMINARIO SOBRE CREATIVIDAD E INNOVACION` | `Bibliografía` (`683842`) | `1877841`, `1877848` | `Bibliografía virtual básica.html` | Yes (210 vs 148) |
| 14 | `35379` `III.01.2026-I SEMINARIO SOBRE CREATIVIDAD E INNOVACION` | `Presentación` (`616060`) | `1673227`, `1673231` | `Área de texto (Etiqueta).html` | Yes (`<img>` vs `Navegar...`) |
| 15 | `35379` `III.01.2026-I SEMINARIO SOBRE CREATIVIDAD E INNOVACION` | `Bibliografía` (`617348`) | `1673248`, `1673255` | `Bibliografía virtual básica.html` | Yes (E-Libro vs Macro) |
| 16 | `33929` `III.01.2026-I ANALISIS Y DISENO DE SISTEMAS ORIENTADOS A OBJETOS` | `Evaluación Final` (`594306`) | `1613457`, `1613460` | `Aquí encontrarás la rúbrica que se empleará para t.html` | Yes (442 vs 429) |
| 17 | `33929` `III.01.2026-I ANALISIS Y DISENO DE SISTEMAS ORIENTADOS A OBJETOS` | `Bibliografía` (`594307`) | `1613461`, `1613466` | `Bibliografía virtual básica.html` | Yes (E-Libro vs Macro) |
| 18 | `33927` `III.01.2026-I PROGRAMACION DE BASE DE DATOS` | `Evaluación Final` (`594468`) | `1613973`, `1613976` | `Aquí encontrarás la rúbrica que se empleará para t.html` | Yes (442 vs 429) |
| 19 | `33927` `III.01.2026-I PROGRAMACION DE BASE DE DATOS` | `Bibliografía` (`594469`) | `1613977`, `1613982` | `Bibliografía virtual básica.html` | Identical (80 vs 80) |
| 20 | `33926` `III.01.2026-I INVESTIGACION ETICA Y RESPONSABILIDAD CIUDADANA` | `Evaluación Final` (`594431`) | `1613848`, `1613851` | `Aquí encontrarás la rúbrica que se empleará para t.html` | Yes (442 vs 429) |
| 21 | `32500` `II.01.2025-III SEMINARIO SOBRE TECNOLOGIA E INTELIGENCIA ARTIFICIAL` | `Presentación` (`560639`) | `1518070`, `1518074` | `Etiqueta.html` | Yes (`<img>` vs `Guía virtual`) |
| 22 | `31565` `II.01.2025-III DISENO DE BASE DATOS RELACIONALES` | `Evaluación Parcial` (`557977`) | `1505206`, `1505209` | `Aquí encontrarás la rúbrica que se empleará para t.html` | Yes (442 vs 429) |
| 23 | `31565` `II.01.2025-III DISENO DE BASE DATOS RELACIONALES` | `Evaluación Final` (`557980`) | `1505215`, `1505218` | `Aquí encontrarás la rúbrica que se empleará para t.html` | Yes (442 vs 429) |

## Candidate Directions When Re-Visiting

- **Leaf-only disambiguation** (`CourseRepository.get_course_content`, `CourseRepository.get_recordings`, `IDATSync.get_course_remote_tree`): keep shared folder paths like `'Rup'` untouched, leave the 1st occurrence unchanged for ADR-0002 incremental-sync compatibility, suffix the 2nd+ collision deterministically (`stem (2).ext` within `max_len=100` — or `-[<module_id>]` on colliding leaves), and have `SyncDiff.from_trees` raise `ValueError` on unexpected duplicate paths.
- Also evaluate non-label collision vectors: `assign` attachments, ignored Moodle `filepath` subdirectories, same-week `RecordingContent` filenames, and section names colliding with `recordings_folder_name`.

## Comments

- **2026-11-15 (`/ponytail-audit` & triage feedback):**
  - Commit `969e8ee` eliminated the `'label'` collision cases listed above by consolidating section labels into per-section `info.html` and `bibliografia.html`.
  - Remaining collision vectors tracked under `/ponytail-audit` Finding `#8`: `MoodleAdapter._to_module_content` ignoring non-root `filepath` subdirectories, duplicate `CourseModule` / `CourseSection` directory names containing same-named files, filename collisions after `max_len=100` truncation (`safe_path_segment`), and same-week `RecordingContent` filename collisions.
  - **Maintainer direction:** concatenate the ID onto the last folder preceding the filename (i.e., `CourseModule.id` on the module directory `<module_name>-[<module_id>]`).
  - **Design points to settle before implementation (`/grill-with-docs` / `/opsx:propose`):**
    1. **Scope where the last folder has no Moodle `module.id`:** Moodle `folder` `filepath` subdirs, SharePoint `:f:` subfolders, `CourseSection` inline files (`info.html`, `bibliografia.html`), and `RecordingContent` week folders (`Semana N/`) cannot rely solely on `CourseModule.id` (`filepath` preservation + leaf fallback still needed for intra-folder or truncated-filename collisions).
    2. **ADR-0002 backward compatibility:** appending `-[<module_id>]` to every module folder renames all existing synced module directories, causing `SyncDiff` to mark previously downloaded files as `MISSING` and re-download them (leaving old module folders as `LOCAL_ONLY`) unless migrated or scoped only to future syncs.
  - **Settled `/grill-with-docs` decisions (`ADR-0004`, ready for `/opsx:propose`):**
    1. Preserve non-root Moodle `filepath` subfolders via `ModuleContent.filepath: str = "/"` applied in `CourseRepository.list_url_content` (keeping `UrlContentStrategy` unchanged; `GLOSSARY.md` updated).
    2. Unconditionally format every `CourseModule` folder as `<bounded_name>-[<module.id>]` (`<= 100` chars), keeping `Course` and `CourseSection` names clean and reopening `ADR-0002` via `docs/adr/0004-module-id-folders-and-filepath-preservation.md` (since no users/synced trees exist yet; migration script skipped under YAGNI).
    3. Disambiguate remaining case-insensitive leaf path collisions (`<stem> (2)<ext>`) in `IDATSync.get_course_remote_tree` and raise `ValueError` on unexpected duplicate `Path` keys in `SyncDiff.from_trees`.
