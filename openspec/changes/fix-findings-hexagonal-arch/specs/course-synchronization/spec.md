# Spec Delta

## Purpose

Enforces strict hexagonal port delegation for filesystem interactions, seals adapter boundaries against vendor data leakage, and defines specialized content types in the domain model.

## ADDED Requirements

### Requirement: Mandatory FileSystemPort delegation in DownloadManager
`DownloadManager` SHALL require `FileSystemPort` as a non-nullable constructor dependency and delegate all local file existence checks, file modification timestamp inspections, and directory tree scans to `FileSystemPort`. `DownloadManager` MUST NOT perform direct filesystem I/O (`os`, `stat`, `rglob`, `mkdir`). Verified at the `DownloadManager` public seam.

#### Scenario: Scanning local tree modification times
- **WHEN** `DownloadManager.diff_trees(root_dir, remote_contents)` is called
- **THEN** it delegates scanning to `file_system.scan_mtimes(Path(root_dir))` without calling `Path.rglob` or `Path.stat` directly

#### Scenario: Determining whether a task needs download
- **WHEN** `DownloadManager.should_download(task)` evaluates a download task
- **THEN** it checks existence via `file_system.exists(local_path)` and compares timestamps via `file_system.get_mtime(local_path)` without direct OS file queries

### Requirement: Sealed Anti-Corruption Layer on Moodle adapter
`MoodleAdapter` public methods SHALL return only `domain/` models (`Course`, `CourseSection`, `CourseModule`, `ModuleContent`, `Content`). Any helper that retrieves or processes raw vendor structures (`dict`, raw JSON payloads) without converting to domain entities SHALL be private. Verified at the `MoodleAdapter` public seam.

#### Scenario: Inspecting MoodleAdapter public method return types
- **WHEN** all public methods of `MoodleAdapter` are invoked or inspected
- **THEN** none of them return raw vendor dictionaries or SDK objects

### Requirement: Ubiquitous domain language for specialized content types
The domain model and `GLOSSARY.md` SHALL define canonical terms for in-memory text artifacts (`StringContent`) and remote class video recordings (`RecordingContent`) as distinct subtypes of `Content`. Verified at the domain vocabulary seam.

#### Scenario: Consulting GLOSSARY.md for specialized content terms
- **WHEN** `GLOSSARY.md` is inspected under `Synchronization & Files`
- **THEN** both `StringContent` and `RecordingContent` are documented with their domain responsibilities and forbidden synonyms
