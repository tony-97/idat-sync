# Design

## Context

See `proposal.md` - Why. `SharepointBrowserAuth` in `src/idat_sync/infrastructure/sharepoint_auth.py` implements `SharepointAuthPort` (see [0001-hexagonal-ports-and-adapters.md](../../../../docs/adr/0001-hexagonal-ports-and-adapters.md)) and drives Playwright through Microsoft SSO to capture SharePoint `Credentials`.

- **Modules touched**: `src/idat_sync/infrastructure/sharepoint_auth.py`, `tests/test_auth.py`
- **Public test seam**: `SharepointBrowserAuth` seam (`idat_sync.infrastructure.sharepoint_auth.async_playwright` and `asyncio.sleep`)

## Goals / Non-Goals

**Goals:**
- Handle the conditional `"Let's keep your account secure"` (`#skipMfaRegistrationLink`) prompt after MFA submission without race conditions against client-side DOM rendering.
- Preserve exact ordering guarantees between `#idSubmit_SAOTCC_Continue`, `wait_for_url("https://login.microsoftonline.com/common/SAS/ProcessAuth", timeout=timeout)`, `wait_for_selector("#skipMfaRegistrationLink, #KmsiCheckboxField", timeout=timeout)`, conditional `#skipMfaRegistrationLink`, and `#KmsiCheckboxField` / `#idSIButton9`.

**Non-Goals:**
- Changing port signatures or any module outside `src/idat_sync/infrastructure/sharepoint_auth.py` and `tests/test_auth.py`.

## Decisions

1. **Wait for combined selector `"#skipMfaRegistrationLink, #KmsiCheckboxField"` before `is_visible("#skipMfaRegistrationLink")`**
   - *Rationale*: `page.is_visible(selector)` evaluates immediately without waiting for client-side rendering. Calling `await page.wait_for_selector("#skipMfaRegistrationLink, #KmsiCheckboxField", timeout=timeout)` after `wait_for_url("https://login.microsoftonline.com/common/SAS/ProcessAuth", timeout=timeout)` guarantees that whichever screen Microsoft renders first is visible before checking `if await page.is_visible("#skipMfaRegistrationLink"):`.
   - *Alternatives considered*:
     - Calling `await page.click("#skipMfaRegistrationLink")` unconditionally: rejected because accounts that already registered a second factor or snoozed the prompt bypass `#skipMfaRegistrationLink` and go directly to `#KmsiCheckboxField`.
     - Calling `await page.is_visible("#skipMfaRegistrationLink")` immediately after `wait_for_url` without `wait_for_selector`: rejected because Knockout.js rendering on `login.microsoftonline.com` can lag behind URL navigation, causing `is_visible` to return `False` prematurely.

2. **Select `#idDiv_SAOTCS_Proofs > div:nth-child(2)` and launch with `headless=False`**
   - *Rationale*: Matches the current Microsoft SSO proof ordering where the SMS OTP option is the second tile, while keeping `headless=False` enabled for visible browser debugging.

## Risks / Trade-offs

- `[headless=False opens a visible browser window during login]` → Intentional for debugging as agreed in the grilling session; can be switched back to `headless=True` in a future change once Microsoft SSO flow changes stabilize.
