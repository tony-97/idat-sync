# Streaming Sync Pipeline — Exploration & Grilling Transcript

## Turn 1 — User

> `/opsx-explore` read `src/idat_sync/application/course_repository.py`. I had created a stream to get the courses contents so it's not blocking sequentially awaiting for the urls to be resolved sequentially instead urls are resolved in parallel as fast as they return. But as it's an architectural change returning an `AsyncIterator` it will propagate to the callers.

## Turn 1 — Assistant

### 1. What actually runs in parallel today?

Looking at the production wiring in `src/idat_sync/bootstrap.py`:

- `MoodleAdapter.list_url_content` does **zero network I/O** — it returns `[MoodleContent(...)]` immediately in memory.
- `SharepointApiAdapter.list_url_content` and `fetch_recordings` both go through one shared `sharepoint_limiter` (`AsyncRateLimiter`, backed by a single `asyncio.Lock` with a default `30.0s` cooldown).

That splits the benefit into two very different things:

1. **Parallelizing URL resolution alone (still collecting a `list[Content]` at the end):**
   When `request_delay > 0`, `sharepoint_limiter._lock` serializes every SharePoint call anyway, so running `list_url_content` tasks concurrently only speeds up resolution when `request_delay == 0` (or if another network-backed strategy with its own limiter is added later).
2. **Pipelining discovery into downloads + UI (`AsyncIterator`):**
   This unlocks a massive real-world win even at `request_delay = 30.0`:

```text
CURRENT (batch barrier before downloads start):
  Moodle limiter:     [populate] .................... IDLE (minutes!) .................... [DL M1][30s][DL M2]...
  Local disk (HTML):  ............................... IDLE (minutes!) .................... [write HTMLs]
  SharePoint limiter:            [resolve SP1][30s][resolve SP2][30s]...[recordings][30s] [DL SP1][30s][DL Rec1]...
  CourseTree UI:      [Fetching contents... stuck until ALL SP links & recordings finish] [Files appear -> Syncing]

STREAMING PIPELINE (overlap discovery with downloads + live UI):
  Moodle limiter:     [populate][DL M1][30s][DL M2][30s][DL M3]...  <-- starts immediately!
  Local disk (HTML):            [write inline HTMLs immediately!]
  SharePoint limiter:           [resolve SP1][30s][DL SP1][30s][resolve SP2][30s][recordings]...
  CourseTree UI:      [Inline + Moodle files appear immediately; SP/recordings stream in live]
```

Right now, inline HTML and Moodle files are **already known at `t = 0`**, yet they wait behind minutes of rate-limited SharePoint link and recording lookups before a single byte downloads or a single file row appears in the UI.

### 2. Why `AsyncIterator` propagates (the blast radius)

```text
CourseRepository.stream_course_content  --> AsyncIterator[Content]
  |
  v  [1. Disambiguation + merging get_recordings]
CourseRepository.iter_course_remote_tree
  |
  v  [2. SyncDiff.from_trees computes local_only_paths from full tree]
DownloadManager.diff_trees(course_dir, Sequence[Content]) -> SyncDiff
  |
  v  [3. TaskOrchestrator builds fixed group coroutines from Sequence[DownloadTask]]
TaskOrchestrator.create_coroutines(Sequence[DownloadTask]) -> list[Coroutine]
  |
  v  [4. Single batch event + CourseTree resets children & sets total%]
IDATSync.sync_course  -->  CourseFilesDiscovered  -->  CourseTree.show_files
```

Friction points & WIP catches:
1. **Non-deterministic path disambiguation (`_disambiguate_paths`)** under `asyncio.as_completed` if colliding URLs within the same module finish in network-race order. (Because ADR-0004 puts every `CourseModule` in its own `...-[<module.id>]` directory, cross-module collisions cannot happen; keeping intra-module order deterministic eliminates the race.)
2. **`SyncDiff` vs what `sync_course` actually needs:** `sync_course` never uses `local_only_paths` — it only needs `scan_mtimes(course_dir)` once up front and per-item classification.
3. **`TaskOrchestrator`:** `create_coroutines` builds fixed loops over a pre-collected `Sequence[DownloadTask]`; streaming needs per-group queues/workers.
4. **`CourseFilesDiscovered` & `CourseTree`:** `CourseTree.show_files` clears children and assumes a fixed `total` up front.
5. **Python 3.12 compatibility:** `pyproject.toml` targets `requires-python = ">=3.12"`, where `asyncio.as_completed(tasks)` is a synchronous iterator (`for coro in asyncio.as_completed(tasks):`), not `async for` (Python 3.13+).
6. **Durable spec:** `openspec/specs/course-synchronization/spec.md` currently pins the batch seams.

### 3. Main directions compared

- **Option A — Keep `list[Content]` at `CourseRepository`** (internal concurrency, zero caller propagation, keeps batch barrier).
- **Option B — Full streaming pipeline** (overlap discovery with downloads + live UI updates).

---

## Turn 2 — User

> Option A doesn't make sense why we are refactoring in the first point. I want Option B, STREAMING PIPELINE (overlap discovery with downloads + live UI). Nothing wants a full `SyncDiff`, maybe we just do a per item `is_pending`. If collision is a problem we can append the moodle json id to each file. This is a heavy refactor, we don't need backward compatibility.

## Turn 2 — Assistant

### Two Findings From the Codebase

1. **Moodle `ModuleContent` has no `id` field — and you don't need one for deterministic collisions.**
   Inspecting `all_courses_contents.json` across all 35 courses:
   - `module["contents"]` keys: `author`, `filename`, `filepath`, `filesize`, `fileurl`, `isexternalfile`, `license`, `mimetype`, `repositorytype`, `sortorder`, `timecreated`, `timemodified`, `type`, `userid` (**no `id`**).
   - `assign["introattachments"]` keys: `filename`, `filepath`, `filesize`, `fileurl`, `icon`, `isexternalfile`, `mimetype`, `timemodified` (**no `id`**).
   - Only `Course`, `CourseSection`, and `CourseModule` have IDs, and every `CourseModule` already gets a unique `...-[<module.id>]` directory.
   - Across all 35 courses: `0` duplicate section names, `15` multi-item modules (14 `folder`, 1 `page`, max 5 items), and `0` modules with `> 1` SharePoint URL.
   - Making each concurrent task resolve **one `CourseModule`** (`asyncio.gather` over `module.contents` in deterministic Moodle order) + 1 task for `get_recordings` gives 100% deterministic disambiguation with a course-level `seen_keys` set as a safety net.

2. **Hidden concurrency race in `SharepointClient.get_client_from_url`.**
   `get_client_from_url` falls back to returning the shared mutable `self.client` instance, and `load_entity` mutates `ctx.load(entity, ...)` **before** entering `self.rate_limiter`. With concurrent URL resolution tasks, fallback URLs would corrupt the same `ClientContext` pending queue. Returning a fresh `self.get_client(self._academic_site_url)` on fallback fixes it.

### Streaming Pipeline Blueprint

```text
+-------------------------------------------------------------------------+
| 1. CourseRepository.stream_course_content(course, course_dir)           |
|    + CourseRepository.get_recordings(course_name, recordings_folder)    |
|                                                                         |
|    * Yields section & module inline_contents immediately (t = 0)        |
|    * Spawns 1 task per CourseModule (asyncio.gather on module.contents) |
|    * Iterates via `for coro in asyncio.as_completed(tasks):` (py3.12!)  |
+-------------------------------------------------------------------------+
                                   |
                                   v  (runs concurrently inside TaskGroup)
+-------------------------------------------------------------------------+
| 2. Producer inside IDATSync.sync_course                                 |
|                                                                         |
|    local_mtimes = file_system.scan_mtimes(course_path)   # once/course  |
|                                                                         |
|    For each discovered Content:                                         |
|      disambiguated = disambiguator.disambiguate(content)                |
|      task = download_manager.create_download_task(disambiguated)        |
|      if task is None: continue            # drops empty StringContent   |
|      if download_manager.is_pending(disambiguated, local_mtimes):       |
|          emit CourseFileDiscovered(pending)                             |
|          channel.enqueue(task)            # non-blocking queue push     |
|      else:                                                              |
|          emit CourseFileDiscovered(synced)                              |
+-------------------------------------------------------------------------+
                                   |
         +-------------------------+-------------------------+
         v                         v                         v
  [Queue: Moodle]           [Queue: String]        [Queue: SP + Recording]
         |                         |                         |
         v                         v                         v
  Worker (moodle_limiter)   Worker (local disk)    Worker (sharepoint_limiter)
  starts at t = 0!          starts at t = 0!       interleaves via FIFO lock
         |                         |                         |
         +-------------------------+-------------------------+
                                   v
+-------------------------------------------------------------------------+
| 3. Unified event_queue --> SyncEvent stream --> SyncFrame / CourseTree  |
|    All download + discovery events flow out of sync_course in real time |
+-------------------------------------------------------------------------+
```

### Confirmed Decisions (Turn 3)

- **User:** `"yes both recommendations match."`
  1. `get_recordings` non-auth `RemoteServiceError` mid-sync is isolated (logged & recorded so already-discovered files finish syncing, then reported on `CourseSyncCompleted(course, error)`).
  2. Emit `CourseDiscoveryCompleted` when discovery finishes; `CourseTree` shows `"Fetching & Syncing"` + `"done/total+"` while discovering and switches to `"Syncing"` + exact `"pct%"` after discovery completes.

---

## Turn 4 — User

> `first save the full chat in a md` -> followed by `/grill-with-docs read the full conversation for the implementation some questions are already answered.`

## Turn 4–5 — Grilling Round 1 (All Confirmed by User: `"write it and continue. yes to all questions"`)

1. **Q1 (CourseRepository vs. IDATSync boundary & deterministic per-module streaming):**
   Keep `stream_course_content(course, course_dir) -> AsyncIterator[Content]` (1 task/module using `asyncio.gather` + py3.12 `for coro in asyncio.as_completed(tasks):`), `get_recordings(course_name, recordings_folder) -> list[RecordingContent]`, and single-item `list_url_content(content: ModuleContent) -> list[Content]` on `CourseRepository`. `IDATSync.sync_course` runs both streams concurrently, isolates `get_recordings`'s `RemoteServiceError`, applies a streaming `PathDisambiguator`, and deletes `get_course_content`, `get_course_remote_tree`, `check_course_sync`, and WIP `iter_course_remote_tree`.
2. **Q2 (Domain vocabulary & `domain/sync.py` cleanup):**
   Delete `SyncDiff`, `SyncEntry`, and `SyncStatus` from `domain/sync.py` and `GLOSSARY.md` (and `diff_trees`/`prepare_download_tasks` from `DownloadManager`). Replace `domain/sync.py` with pure `is_content_pending(content: Content, local_mtimes: Mapping[Path, int]) -> bool`, wrapped by `DownloadManager.is_pending(content, local_mtimes) -> bool` (adding `StringContent` byte comparison via `FileSystemPort.read_bytes`).
3. **Q3 (Discovery event shape & logging):**
   Replace batch `CourseFilesDiscovered` with `CourseSyncStarted(course, course_dir)`, `CourseFileDiscovered(course, path, is_pending)` (emitted before enqueueing download task; logged at `DEBUG`), and `CourseDiscoveryCompleted(course, synced_count, pending_count)` (logged at `INFO` as `"Course '%s': %d synced, %d pending."`).
4. **Q4 (Streaming `TaskOrchestrator` seam):**
   Replace `create_coroutines` with `create_channel() -> TaskChannel` owning one unbounded `asyncio.Queue[DownloadTask | None]` per downloader group, exposing `enqueue(task)` (raising `ValueError` on unregistered downloader), `close()`, and `create_workers(run_task=...)`, with zero imports from `events.py` or `threading`.
5. **Q5 (Course-level concurrency):**
   Keep `IDATSync.sync_courses` sequential across courses; streaming/concurrency runs within each course only.
6. **Q6 (`SharepointClient` fix & `UrlContentStrategy`):**
   Fix `SharepointClient.get_client_from_url` fallback to create a fresh `ClientContext` (`self.get_client(self._academic_site_url)`), and keep `UrlContentStrategy.list_url_content(...) -> list[Content]` unchanged.
7. **Q7 (`CourseTree` status labels & progress strings):**
   `"Fetching contents..."` (`"..."`) -> `"Fetching & Syncing"` (`"done/total+"`) -> `"Syncing"` (`"pct%"`) -> `"Completed"` (`"100%"`) / `"Failed"` (current progress).

---

## Turn 6 — Grilling Round 2 (All Confirmed by User: `"yes to all"`)

8. **Q8 (`PathDisambiguator` placement & contract):**
   Stateful pure `Path -> Path` `PathDisambiguator` in `src/idat_sync/domain/path_utils.py` (`disambiguate(path: Path) -> Path`, comparing via `path.as_posix().casefold()`, renaming leaf collisions via `split_extension` + `safe_suffixed_segment(stem, f" ({counter}){ext}")` from `counter = 2`, `<= 100` chars without looping), re-exported with `is_content_pending` in `src/idat_sync/domain/__init__.py`. `IDATSync` applies `replace(item, path=new_path)` only when changed.
9. **Q9 (Mid-stream failure, cancellation & `CourseTree` edge cases):**
   - `get_recordings` non-auth `RemoteServiceError`: log immediately at `logging.ERROR` and append to `errors` for `CourseSyncCompleted(course, errors[0])`.
   - Pre-stream failures (`populate_course_sections`, `ensure_dir`, `scan_mtimes`): catch `IDATSyncError`, yield `CourseSyncCompleted(course, exc)`, and re-raise.
   - `CourseTree`: while `discovering` is `True`, `TaskDownloadCompleted` keeps `(RowStatus.FETCHING_AND_SYNCING, f"{done}/{total}+")`; empty courses transition `(RowStatus.FETCHING, "...")` -> `(RowStatus.SYNCING, "100%")` on `CourseDiscoveryCompleted(course, 0, 0)` -> `(RowStatus.COMPLETED, "100%")` on `CourseSyncCompleted(course, None)`.
   - `CourseRepository.stream_course_content`: early exit / cancellation cancels all unfinished module tasks (and sibling `module.contents` tasks inside a module on `CredentialsExpiredError` / `ValueError`) and awaits cleanup in `finally` with zero leaked tasks.
10. **Q10 (ADR):**
    Created `docs/adr/0006-streaming-sync-pipeline.md` (superseding the batch-diff notes in ADR-0001 and ADR-0004 while leaving historical ADRs intact).
11. **Q11 (Modules touched, `MUST NOT` deletions, standards sync & public test seams):**
    - **Production modules (11):** `domain/sync.py`, `domain/__init__.py`, `domain/path_utils.py`, `application/course_repository.py`, `application/download_manager.py`, `application/task_orchestrator.py`, `application/events.py`, `application/sync_service.py`, `infrastructure/sharepoint_client.py`, `ui/course_tree.py`, `ui/sync_frame.py`. (`bootstrap.py`, `ports.py`, `MoodleAdapter`, `SharepointApiAdapter`, `*Downloader`, `AsyncRateLimiter` untouched.)
    - **Standards synced:** `agent-os/standards/global/layer-boundaries.md`, `agent-os/standards/backend/content-strategy-dispatch.md`, `agent-os/standards/testing/composition-and-seams.md`.
    - **`MUST NOT` deletions:** `SyncDiff`, `SyncEntry`, `SyncStatus`, `CourseRepository.get_course_content`, `CourseRepository.iter_course_remote_tree`, `DownloadManager.diff_trees`, `DownloadManager.prepare_download_tasks`, `TaskOrchestrator.create_coroutines`, `CourseFilesDiscovered`, `IDATSync.get_course_remote_tree`, `IDATSync.check_course_sync`, `CourseTree.show_files`, returning shared `self.client` from `SharepointClient.get_client_from_url`.
    - **Public test seams:** `domain.sync` (`is_content_pending`), `domain.path_utils` (`PathDisambiguator`), `CourseRepository`, `DownloadManager`, `TaskOrchestrator` (`create_channel` -> `TaskChannel`), `application.events` (`SyncEvent`, `log_sync_event`), `IDATSync`, `SharepointClient` (`get_client_from_url`), `CourseTree`, `SyncFrame`, `TestRealCourseContentsFixture`, AST layer-boundary checks.
