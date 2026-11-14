# Proposal

## Why

Microsoft Entra ID now places the phone-call MFA option first on the proof selection screen (`#idDiv_SAOTCS_Proofs > div:nth-child(2)` is required for the SMS OTP code) and may present a `"Let's keep your account secure"` MFA registration interrupt (`#skipMfaRegistrationLink`) at `https://login.microsoftonline.com/common/SAS/ProcessAuth` before showing the `"Stay signed in?"` (`#KmsiCheckboxField`) screen. Without handling this screen, `SharepointBrowserAuth.fetch_storage_state` stalls waiting for `#KmsiCheckboxField` and cannot acquire SharePoint `Credentials`.

## What Changes

- Launch Playwright Chromium with `headless=False` during `SharepointBrowserAuth.fetch_storage_state` to support visible browser debugging.
- Select the second MFA proof tile (`#idDiv_SAOTCS_Proofs > div:nth-child(2)`) before requesting the OTP code.
- After submitting the MFA OTP (`#idSubmit_SAOTCC_Continue`) and reaching `https://login.microsoftonline.com/common/SAS/ProcessAuth`, wait for `"#skipMfaRegistrationLink, #KmsiCheckboxField"` (`timeout=timeout`), conditionally click `#skipMfaRegistrationLink` only when `await page.is_visible("#skipMfaRegistrationLink")` is `True`, and then proceed to click `#KmsiCheckboxField` and `#idSIButton9`.
- Update unit tests in `tests/test_auth.py` at the `SharepointBrowserAuth` seam to cover both `is_visible("#skipMfaRegistrationLink") == True` and `False` branches.

## Non-Goals

- Changing `AuthService`, `MoodleTokenClient`, `MoodleTokenAdapter`, `JsonCredentialStore`, `bootstrap.py`, or any UI modules.
- Modifying `GLOSSARY.md` or adding new ADRs under `docs/adr/`.
- Tracking untracked local CLI/helper scripts (`login_cli.py`, `moodle_json.py`, `all_courses_contents.json`).

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `authentication`: Update `Requirement: Browser-based SharePoint SSO and MFA capture` to specify `headless=False`, selecting `#idDiv_SAOTCS_Proofs > div:nth-child(2)`, waiting for `"#skipMfaRegistrationLink, #KmsiCheckboxField"` at `https://login.microsoftonline.com/common/SAS/ProcessAuth`, and conditionally clicking `#skipMfaRegistrationLink` when visible before `#KmsiCheckboxField` and `#idSIButton9`.

## Impact

- **Modules touched**: `src/idat_sync/infrastructure/sharepoint_auth.py`, `tests/test_auth.py`.
- **Public test seam**: `SharepointBrowserAuth` seam (`idat_sync.infrastructure.sharepoint_auth.async_playwright` and `asyncio.sleep`).
- **Applicable `agent-os` standards (`agent-os/standards/index.yml`)**:
  - `global/layer-boundaries`
  - `testing/composition-and-seams`
