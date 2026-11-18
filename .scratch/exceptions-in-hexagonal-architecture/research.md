# Exceptions and Exception Handling in Clean / Hexagonal Architecture (`idat-sync`)

---

## Part 1: Architectural Foundations from Primary Sources

### 1.1 Alistair Cockburn — Hexagonal Architecture (Ports and Adapters)

**Primary Source:** Alistair Cockburn, *"Hexagonal Architecture"* (originally titled *"Ports and Adapters"*, published 2005 on `alistair.cockburn.us/hexagonal-architecture/`).

1. **Symmetry of Ports and Asymmetry of Primary (Driving) vs. Secondary (Driven) Adapters:**
   - An application is structured as a hexagon enclosing pure domain and application behavior, communicating with external actors exclusively through **Ports** (interfaces grouped by conversation purpose/protocol).
   - **Primary (Driving) Adapters** (e.g., GUI frames, CLI scripts, notebooks, HTTP routers, test harnesses) initiate interactions with the application via **Driving Ports** (use-case entrypoints).
   - **Secondary (Driven) Adapters** (e.g., HTTP/REST clients, file-based stores, browser automation, local filesystems) are invoked by the application via **Driven Ports** (interfaces owned and defined by the application core).
2. **Exceptions Are Part of a Port's Contract:**
   - Every method on a port has two output channels: its **normal return type** and its **exceptional return channel** (the set of exceptions it may raise).
   - Because a Port is an interface defined inside the hexagon in the vocabulary of the application's conversation with the outside world, **any exception that crosses a port boundary is part of that port's contract**.
   - **Driven Port Rule:** A secondary adapter implementing a driven port MUST NOT allow technology-specific exceptions (`httpx.HTTPError`, `office365.runtime.client_request_exception.ClientRequestException`, `playwright.async_api.Error`, `yt_dlp.utils.DownloadError`, `OSError`) to escape unmapped across the port boundary. Either:
     1. The driven port contract specifies a return value (such as `None` for missing/unreadable local state) and the adapter absorbs low-level errors; or
     2. The driven port contract specifies a port-owned exception defined inside the hexagon, and the secondary adapter translates vendor/OS exceptions into that port exception.
   - **Driving Port Rule:** Primary adapters (e.g., `LoginFrame`, `SyncFrame`, CLI scripts, notebooks) invoke application use cases and catch application/port exceptions (or consume error events) to render meaningful messages and UI/CLI states.

---

### 1.2 Robert C. Martin — Clean Architecture & The Dependency Rule

**Primary Sources:**
- Robert C. Martin, *"The Clean Architecture"* (*The Clean Code Blog*, August 13, 2012, `blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html`).
- Robert C. Martin, *Clean Architecture: A Craftsman's Guide to Software Structure and Design* (Prentice Hall, 2017), Chapters 11 (*DIP*), 20 (*Business Rules*), 22 (*The Clean Architecture*), and 24 (*Partial Boundaries*).

1. **The Dependency Rule:**
   > *"Source code dependencies must point only inward, toward higher-level policies. Nothing in an inner circle can know anything at all about something in an outer circle. In particular, the name of something declared in an outer circle must not be mentioned by the code in an inner circle."* (Chapter 22)
2. **Which Layer Owns Exception Types:**
   - **Domain Layer (`domain/`):** Owns exceptions (or standard value validation errors) representing violated **Critical Business Rules** / domain invariants (e.g., path-traversal invariants, duplicate tree paths).
   - **Application / Use-Case Layer (`application/`):** Owns exceptions representing **Application-Specific Business Rules** and **Port Boundary Contracts** (`IDATSyncError` and its subclasses in `application/exceptions.py`, re-exported by `application/ports.py`).
   - **Interface Adapters & Frameworks/Drivers (`infrastructure/`, `ui/`):** May encounter or catch third-party SDK and OS exceptions internally, because `infrastructure/` depends inward on `application/` and `domain/`. By the Dependency Inversion Principle (DIP, Chapter 11), `infrastructure/` imports port exceptions from `application/` and raises them when fulfilling port contracts.
3. **Exception Flow vs. Source-Code Dependency Direction:**
   - **Source-code dependencies (`import`)** always point **inward**: `infrastructure/` $\rightarrow$ `application/` $\rightarrow$ `domain/`, and `ui/` $\rightarrow$ `application/` $\rightarrow$ `domain/`.
   - **Runtime control flow of raised exceptions** travels up the call stack: instantiated in `infrastructure/` using a class defined in `application/`, propagating inward to `application/` and outward to `ui/`, without `application/` or `ui/` ever importing `infrastructure/` or vendor SDKs.

---

### 1.3 Eric Evans & Vaughn Vernon — Domain-Driven Design (DDD) & Anti-Corruption Layers

**Primary Sources:**
- Eric Evans, *Domain-Driven Design: Tackling Complexity in the Heart of Software* (Addison-Wesley, 2003), Chapter 14 (*Maintaining Model Integrity: Anti-Corruption Layer*).
- Vaughn Vernon, *Implementing Domain-Driven Design* (Addison-Wesley, 2013), Chapter 3 (*Context Maps*), Chapter 4 (*Architecture — Hexagonal / Ports and Adapters*), and Chapter 13 (*Integrating Bounded Contexts*).

1. **Anti-Corruption Layer (ACL) Exception Translation:**
   - Evans defines the **Anti-Corruption Layer (ACL)** as an isolating layer between the client Bounded Context and upstream external systems (here: Moodle Web Services, Microsoft SharePoint REST API, Microsoft SSO via Playwright, `yt-dlp`, and the local OS filesystem).
   - As Vernon details, an ACL translates not only upstream data structures (raw JSON dicts or `office365` SDK objects into `Course` and `Content` dataclasses) but **also upstream error semantics**:
     - Upstream systems signal failures inconsistently: Moodle returns HTTP `200 OK` with `{"exception": "...", "errorcode": "invalidtoken"}` or `{"error": "..."}` in JSON *or* HTTP `401`/`403` on file downloads; SharePoint throws `ClientRequestException` with an attached `response.status_code`, returns `odata.null` causing `TypeError` in `office365`, or returns an empty `personal_site.url`; `yt-dlp` raises `DownloadError` wrapping `urllib.error.HTTPError` or embedding `"HTTP Error 403"` in text.
     - The ACL intercepts these vendor-specific failure modes at the boundary and translates them into clear, domain/application-level exceptions (`AuthenticationError`, `CredentialsExpiredError`, `RemoteServiceError`, `CredentialStoreError`, `FileSystemError`).
2. **Error Taxonomy:**
   - **Domain Invariants:** Broken structural/model invariants expressed inside `domain/` (here via `ValueError`).
   - **Application / Port Exceptions:** Use-case and port contract failures (`IDATSyncError` hierarchy) that carry meaningful user-facing messages and allow orchestration/UI layers to catch and branch cleanly without vendor coupling.
   - **Lifecycle Control Signals:** `asyncio.CancelledError` (`BaseException`), which is cooperative task cancellation rather than an application error.

---

### 1.4 Harry Percival & Bob Gregory — *Architecture Patterns with Python* (*Cosmic Python*)

**Primary Source:** Harry Percival & Bob Gregory, *Architecture Patterns with Python: Enabling Test-Driven Development, Domain-Driven Design, and Event-Driven Microservices* (O'Reilly Media, 2020; `cosmicpython.com/book/`), Chapters 1, 2, 4, and 8.

1. **Using Exceptions to Express Domain and Service Concepts (Chapters 1 & 4):**
   - Idiomatic Python uses exceptions rather than error codes to signal failed operations across domain and service boundaries.
   - Under Chapter 4 (*"Services Can Use Domain Exceptions, or Define Their Own"*), exceptions representing domain model invariants belong in `domain/`, while exceptions representing use-case orchestration, external boundary failures, or persistence conditions belong in the **Application / Service Layer** (`application/`).
   - Keeping application exceptions distinct keeps **Primary Adapters** (`LoginFrame`, `SyncFrame`, CLIs) thin: the entrypoint invokes the service and handles application exceptions in one place to present user-facing messages.
2. **Events as First-Class Error Carriers (Chapter 8):**
   - When an application service streams batch/concurrent progress, immutable lifecycle events (`TaskDownloadFailed(path, error)`, `CourseSyncCompleted(course, error)`) carry caught `IDATSyncError` instances as structured data for logging (`log_sync_event`) and UI updates without coupling orchestration to UI widgets.

---

### 1.5 Python Official Documentation & PEPs

#### A. PEP 3134, PEP 409, and PEP 415 — Exception Chaining (`raise ... from exc` vs. `from None`)
**Primary Sources:**
- **PEP 3134** (*Exception Chaining and Embedded Tracebacks*, Ka-Ping Yee, Python 3.0, `peps.python.org/pep-3134/`).
- **PEP 409** (*Suppressing exception context*, Ethan Furman, Python 3.3, `peps.python.org/pep-0409/`) and **PEP 415** (*Implement context suppression with exception attributes*, Eli Bendersky, Python 3.3, `peps.python.org/pep-0415/`).
- Python Standard Library Docs: *Built-in Exceptions — Exception context* (`docs.python.org/3/library/exceptions.html#exception-context`).

| Mechanism | Syntax | Attributes Set | Architectural Use at Hexagonal Boundaries |
| :--- | :--- | :--- | :--- |
| **Implicit Chaining** | `raise NewError(...)` inside `except` | `__context__ = orig_exc`, `__cause__ = None`, `__suppress_context__ = False` | Signals an *unexpected secondary failure* during exception handling. Avoid for intentional adapter translation. |
| **Explicit Chaining** | `raise PortError(...) from exc` | `__cause__ = exc`, `__context__ = exc`, `__suppress_context__ = True` | **Standard for ACL translation:** Converts vendor/OS exceptions into `IDATSyncError` subclasses while preserving the root-cause traceback in `__cause__` for `log.err`. |
| **Context Suppression** | `raise SafeError(...) from None` | `__cause__ = None`, `__context__ = exc`, `__suppress_context__ = True` | **Standard for security redaction / noise suppression:** Suppresses display of the original exception in tracebacks when it contains secret URLs (`wstoken`/`token` in `MoodleClient.download_file`) or noisy internal lookup details (`ZoneInfoNotFoundError` in `config.py`). |

#### B. PEP 654 — `ExceptionGroup`, `BaseExceptionGroup`, and `except*` with `asyncio.TaskGroup`
**Primary Sources:**
- **PEP 654** (*Exception Groups and `except*`*, Irit Katriel, Yury Selivanov, Guido van Rossum, Python 3.11, `peps.python.org/pep-0654/`).
- Python Standard Library Docs: *Coroutines and Tasks — Task Groups* (`docs.python.org/3/library/asyncio-task.html#task-groups`).

1. When any child task inside `async with asyncio.TaskGroup() as tg:` raises an unhandled exception (other than `asyncio.CancelledError`), `TaskGroup` cancels sibling tasks, awaits them, and raises an `ExceptionGroup`.
2. Standard `except SomeError:` does **not** match `ExceptionGroup`; callers must use `except* SomeError as eg:`. Any leaf exceptions not matched by `except*` automatically propagate in a residual `ExceptionGroup`.
3. Inside `except*`, bare `raise` is forbidden (PEP 654); to keep `TaskGroup` an internal implementation detail of a service method, unwrap via `raise eg.exceptions[0] from eg`.

#### C. `BaseException` vs. `Exception` and `asyncio.CancelledError` (bpo-32528)
**Primary Sources:**
- Python Standard Library Docs: *Exception hierarchy* (`docs.python.org/3/library/exceptions.html#exception-hierarchy`).
- Python 3.8 Release Notes & **bpo-32528** (`docs.python.org/3/whatsnew/3.8.html#asyncio`): `asyncio.CancelledError` changed from inheriting from `Exception` to inheriting directly from `BaseException`.

1. Fault-isolation handlers (`except IDATSyncError:` or `except Exception:`) naturally allow `asyncio.CancelledError` to propagate unhindered.
2. Cleanup/rollback guards (`_atomic_download`, `SharepointClient.create`, `bootstrap.create_sync_service`, `IDATSync.aclose`, `AppFrame.aclose`) must use `try ... finally:` or `except BaseException:` + `raise` so `.part` files, transports, and browsers are cleaned up even upon task cancellation.
3. `asyncio.to_thread(...)` (used in `RecordingDownloader`) bridges `asyncio.CancelledError` via a per-download `threading.Event` checked inside `yt-dlp`'s progress hook to raise `DownloadCancelled`.

---

## Part 2: Pre-Refactor Audit of `src/idat_sync/`

The codebase audit prior to refactoring identified the following baseline state and boundary gaps:

1. **Inconsistent Login Exception Behavior (`moodle_adapter.py:31-46`, `moodle_client.py:28-43`, `sharepoint_auth.py:151-157`, `auth_service.py:42-78`):**
   - `AuthService.login(...) -> bool` caught `except* AuthenticationError:` and returned `False`, discarding the specific failure reason (`Invalid login`, `MFA code is required`, or `CredentialStorePort.save()` returning `False` on disk failure).
   - `SharepointBrowserAuth.fetch_storage_state` translated all runtime `Exception`s into `AuthenticationError`, while `MoodleTokenAdapter.fetch_token` let raw `httpx.HTTPStatusError` / `httpx.RequestError` / `json.JSONDecodeError` escape as `ExceptionGroup` into `LoginFrame.do_login()`, forcing `LoginFrame` (`ui/login_frame.py:105-110`) to handle both `bool` returns and `ExceptionGroup` unpacking.
2. **Raw Vendor Exceptions and Noisy Messages Crossing Driven Ports (`moodle_client.py:87-98, 168-186`, `sharepoint_client.py:139-151`, `downloaders.py:194-203`):**
   - `AuthenticationError` and `CredentialsExpiredError` were defined in `application/ports.py:155-160` inheriting from `RuntimeError`, with no shared base exception (`IDATSyncError`) and no port exceptions for non-auth remote errors or filesystem/credential-store write errors.
   - Non-auth failures leaked raw `httpx.HTTPStatusError` (including `httpx`'s `"For more information check: https://developer.mozilla.org/..."` footer), `RuntimeError` (Moodle WS exceptions), `ClientRequestException`, `yt_dlp.utils.DownloadError`, and raw `OSError`s into `SyncEvent.error`, `SyncFrame.status_var`, and logs.
   - `JsonCredentialStore.save()` returned `bool` and `clear()` swallowed `OSError` (`infrastructure/credential_store.py:41-68`), while `LocalFileSystemAdapter.scan_mtimes()` and `ensure_dir()` (`infrastructure/filesystem.py:30-45`) and `_atomic_download()` (`infrastructure/downloaders.py:42-54`) leaked raw `OSError`.
3. **Uncaught Domain `ValueError` in `CourseRepository.list_url_content()` (`course_repository.py:70, 90`):**
   - `self._safe_subfolder(content.filepath)` (line 70) and `[item.in_folder(subfolder) ...]` (line 90) still raise `ValueError` on unsafe `".."` traversal paths or anchored paths, which are domain/data invariant violations intentionally kept outside URL-skip handling.
4. **`*Client` vs. `*Adapter` Translation Alignment (`agent-os/standards/backend/client-adapter-acl.md:24`):**
   - `MoodleClient` and `SharepointClient` are directly injected into both `*Adapter`s and `*Downloader`s (`MoodleDownloader`, `SharepointDownloader`), so translating HTTP/SDK errors inside `*Client` prevents duplicating HTTP `401`/`403` and transport-error mapping across five callers.

---

## Part 3: Confirmed Refactoring Decisions for `idat-sync`

The following architecture was confirmed during the `/grill-with-docs` design session (no backward compatibility retained):

### 3.1 Exception Hierarchy (`src/idat_sync/application/exceptions.py`, re-exported in `application/ports.py`)

```text
Exception
 +-- IDATSyncError
      +-- AuthenticationError
      +-- CredentialsExpiredError
      +-- CredentialStoreError
      +-- RemoteServiceError
      +-- FileSystemError
```

- `IDATSyncError` inherits directly from `Exception` (not `RuntimeError`).
- All six classes are defined in `src/idat_sync/application/exceptions.py` and re-exported from `src/idat_sync/application/ports.py` (`__all__`).
- `application/exceptions.py` has zero imports from `infrastructure/`, `ui/`, or third-party SDKs (enforced by `tests/test_architecture.py`).
- Domain invariant guards (`Content.in_folder`, `SyncDiff.from_trees`, `CourseRepository._safe_subfolder`, and `SyncConfig` validation) continue raising `ValueError` (`FileNotFoundError` for missing explicit config file).

### 3.2 Port Contracts & Error Translation Matrix

| Layer / Component | Normal Return / Non-Error Absence | Exceptions Raised / Propagated |
| :--- | :--- | :--- |
| **`MoodleTokenClient.request_token`** | Returns raw `dict[str, Any]` | `RemoteServiceError` on `httpx.HTTPStatusError` (clean HTTP status message), `httpx.RequestError`, or JSON `ValueError` (`from exc`). |
| **`MoodleTokenAdapter.fetch_token`** | Returns `str` token | `AuthenticationError` on Moodle `"error"` dict or missing/empty `"token"`; lets `RemoteServiceError` propagate. |
| **`SharepointBrowserAuth.fetch_storage_state`** | Returns `StorageStateData` | `AuthenticationError` on blank MFA, `playwright.async_api.TimeoutError` (`"SharePoint sign-in timed out."` `from exc`), or other runtime `Exception` (`"SharePoint browser authentication failed"` `from exc`). Never swallows `asyncio.CancelledError`. |
| **`JsonCredentialStore`** | `load()` returns `Credentials \| None`; `save()` & `clear()` return `None` | `load()` absorbs `(OSError, json.JSONDecodeError)` -> `None`. `save()` & `clear()` log and raise `CredentialStoreError(...) from exc` on `OSError`. |
| **`AuthService.login`** | Returns `None` | Runs Moodle + SharePoint concurrently in `asyncio.TaskGroup()`, raises `AuthenticationError` if either result is empty, awaits `to_thread(credential_store.save, ...)`, and unwraps `except* IDATSyncError as eg: raise eg.exceptions[0] from eg`. |
| **`MoodleClient` (`call_ws`, `download_file`)** | Returns raw WS payload / streams bytes | `CredentialsExpiredError` on HTTP `401`/`403` or `errorcode == "invalidtoken"`. `call_ws` translates Moodle `"exception"` dicts, `httpx.HTTPStatusError`, `httpx.RequestError`, and JSON `ValueError` into `RemoteServiceError(...) from exc`. `download_file` strips `wstoken` and `token` query params and raises `RemoteServiceError(...) from None` on `httpx.HTTPStatusError` / `httpx.RequestError`. |
| **`SharepointClient` (`create`, `_await_query`)** | Returns initialized client / SDK entities | `CredentialsExpiredError` on HTTP `401`/`403` or empty `personal_site.url` or `search_client is None`. All other `ClientRequestException` and `httpx.HTTPError` map to `RemoteServiceError(...) from exc`. Absorbs `odata.null` `TypeError` in `load_entity`. |
| **`RecordingDownloader`** | Downloads video | `CredentialsExpiredError` on `401`/`403` `DownloadError`; `RemoteServiceError` on other `DownloadError` (`from exc`); `FileSystemError` on `OSError` (`from exc`). |
| **`LocalFileSystemAdapter` & `_atomic_download`** | `read_bytes()` returns `bytes \| None` | `read_bytes()` returns `None` on `OSError`. `scan_mtimes()`, `ensure_dir()`, and `_atomic_download()` raise `FileSystemError(...) from exc` on `OSError` (`_atomic_download` cleans up `.part` under `BaseException`). |
| **`CourseRepository.list_url_content`** | Returns `list[Content]` | Catches `RemoteServiceError` to log warning and skip failed URL; lets `CredentialsExpiredError` and `ValueError` propagate. |
| **`IDATSync` & `application/events.py`** | Streams `SyncEvent`s (`TaskDownloadFailed.error: IDATSyncError`, `CourseSyncCompleted.error: IDATSyncError \| None`) | `_execute_tasks` aborts on `CredentialsExpiredError` (unwrapped via `except* CredentialsExpiredError as eg: raise eg.exceptions[0] from eg`) and isolates `(RemoteServiceError, FileSystemError)`. `sync_course` emits `CourseSyncCompleted(course, exc)` on `IDATSyncError`; `sync_courses` aborts on `CredentialsExpiredError` and skips per-course `IDATSyncError`. |
| **Driving Adapters (`LoginFrame`, `SyncFrame`, `scripts/login_cli.py`, `scripts/moodle_json.py`, `colab.ipynb`)** | Render UI / CLI / notebook output | Catch `IDATSyncError` (and `CredentialsExpiredError` where distinct UI instructions apply) and display `str(exc)` cleanly to the user. `LoginFrame.__init__` wraps `self.auth.logout()` in `try ... except CredentialStoreError` into `error_var`. `SyncFrame._ensure_sync_service` raises `IDATSyncError` instead of `RuntimeError` if uninitialized. |
