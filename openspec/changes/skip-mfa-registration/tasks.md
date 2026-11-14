# Tasks

## 1. Conditional MFA Registration Skip in SharepointBrowserAuth

- [x] 1.1 At the `SharepointBrowserAuth` seam in `tests/test_auth.py` and `src/idat_sync/infrastructure/sharepoint_auth.py`, write a failing test verifying `headless=False`, `#idDiv_SAOTCS_Proofs > div:nth-child(2)`, `wait_for_selector("#skipMfaRegistrationLink, #KmsiCheckboxField", timeout=timeout)` after `wait_for_url("https://login.microsoftonline.com/common/SAS/ProcessAuth", timeout=timeout)`, and both `is_visible("#skipMfaRegistrationLink") == True` (clicking `#skipMfaRegistrationLink` before `#KmsiCheckboxField` and `#idSIButton9`) and `False` (must not click `#skipMfaRegistrationLink`), then implement the minimal code in `SharepointBrowserAuth.fetch_storage_state` and verify `tests/test_auth.py` passes.

## 2. Verification

- [x] 2.1 Run the full test suite, typecheck, and lint across the repository and verify all checks pass.
