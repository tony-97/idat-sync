# Tasks

## 1. SharepointClient Throttling, Streaming, and Retry Fixes

- [x] 1.1 At the `SharepointClient` seam (`tests/test_sharepoint_adapter.py`), write a failing test asserting that `fetch_document_library_items` sleeps `self.request_delay` once (not twice) and `download_file` calls `file.download_session(dest, use_path=False).execute_query()`, then update `fetch_document_library_items` and `download_file` in `src/idat_sync/infrastructure/sharepoint_client.py` and verify the test passes.
- [x] 1.2 At the `SharepointClient` seam (`tests/test_sharepoint_adapter.py`, `tests/test_sync_pipeline.py`), write failing tests asserting that all SDK queries execute via `.execute_query()` and `_run_query` retries HTTP `429`/`503` up to 5 attempts (honoring `Retry-After` or `self.request_delay` and re-raising on exhaustion) without retrying `401`/`403`/`404`, then update `SharepointClient` in `src/idat_sync/infrastructure/sharepoint_client.py` and verify the tests pass.

## 2. Final Verification

- [x] 2.1 Run the full test suite, typecheck, and lint (`pytest`, `mypy`, `ruff`) and verify all checks pass.
