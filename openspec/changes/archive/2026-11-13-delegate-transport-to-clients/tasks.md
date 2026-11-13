# Tasks

## 1. Moodle Client & Adapter Seams

- [x] 1.1 At the `MoodleClient` seam (`tests/test_moodle_client.py`), write a failing test for `MoodleClient.download_file(url, dest)` covering authenticated `requests.get` (`wstoken`/`token`, `RQ_HEADER`, `timeout=60`, `stream=True`, `allow_redirects=True`), `1024 * 256` chunk writing to `dest`, `time.sleep(self.request_delay)`, and `RuntimeError` on HTTP `403`, then implement `MoodleClient.download_file` in `src/idat_sync/infrastructure/moodle_client.py` and verify `tests/test_moodle_client.py` passes.
- [x] 1.2 At the `MoodleAdapter` seam (`tests/test_moodle_adapter.py`), write a failing test asserting `hasattr(self.adapter, "token")` is `False`, remove `@property def token` from `src/idat_sync/infrastructure/moodle_adapter.py`, and verify `tests/test_moodle_adapter.py` passes.

## 2. SharePoint Client & Adapter Seams

- [x] 2.1 At the `SharepointClient` and `SharepointApiAdapter` seams (`tests/test_sharepoint_adapter.py`), write failing tests for `SharepointClient.download_file`, `fetch_folder_by_guest_url`, `fetch_file_by_guest_url`, `fetch_file_by_id`, `fetch_folder_files`, and `fetch_subfolders` (including `request_delay` sleeps) and for `SharepointApiAdapter` delegating guest URLs, direct links (`uniqueid`/`sourcedoc`), and `fetch_folder_contents` (`fetch_folder_files` then `fetch_subfolders`) to `SharepointClient` without accepting `request_delay` or importing `time`/`ClientContext`; implement the changes in `src/idat_sync/infrastructure/sharepoint_client.py` and `src/idat_sync/infrastructure/sharepoint_adapter.py` and verify `tests/test_sharepoint_adapter.py` passes.

## 3. Downloaders, Composition Root & Architecture Seams

- [x] 3.1 At the `MoodleDownloader`, `SharepointDownloader`, `bootstrap.create_sync_service`, and AST boundary seams (`tests/test_sync_pipeline.py`), write failing tests verifying `MoodleDownloader(moodle_client)` and `SharepointDownloader(sharepoint_client)` delegate file streaming to `client.download_file(..., f)` without accepting `request_delay`, `create_sync_service` wires `moodle_client` and `sharepoint_raw_client` directly into the downloaders and omits `request_delay` from `SharepointApiAdapter`, and `downloaders.py` does not import `requests`, `RQ_HEADER`, or `time`; implement the updates in `src/idat_sync/infrastructure/downloaders.py` and `src/idat_sync/bootstrap.py` and verify `tests/test_sync_pipeline.py` passes.

## 4. Final Verification

- [x] 4.1 Run the full test suite (`python -m unittest discover -s tests`), typecheck, and lint across the repository and verify all checks pass.
