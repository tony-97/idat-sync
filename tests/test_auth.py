import asyncio
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from idat_sync.application.auth_service import AuthService
from idat_sync.application.ports import Credentials, StorageStateData
from idat_sync.infrastructure.auth import (
    JsonCredentialStore,
    SharepointBrowserAuth,
)


class FakeMoodleTokenPort:
    def __init__(self, token: str | None = "moodle-tok") -> None:
        self.token = token
        self.calls: list[tuple[str, str]] = []

    def fetch_token(self, username: str, password: str) -> str | None:
        self.calls.append((username, password))
        return self.token


class FakeSharepointAuthPort:
    def __init__(self, storage_state: StorageStateData | None = None) -> None:
        self.storage_state = storage_state
        self.calls: list[tuple[str, str, str]] = []

    async def fetch_storage_state(
        self, username: str, password: str, get_mfa_code
    ) -> StorageStateData | None:
        code = await get_mfa_code()
        self.calls.append((username, password, code))
        return self.storage_state


class InMemoryCredentialStore:
    def __init__(
        self,
        initial: Credentials | None = None,
        save_result: bool = True,
    ) -> None:
        self.stored = initial
        self.save_result = save_result
        self.cleared = False

    def load(self) -> Credentials | None:
        return self.stored

    def save(self, credentials: Credentials) -> bool:
        if self.save_result:
            self.stored = credentials
        return self.save_result

    def clear(self) -> None:
        self.stored = None
        self.cleared = True


async def _mfa_code() -> str:
    return "123456"


class TestAuthService(unittest.TestCase):
    def test_login_strips_email_domain_runs_concurrently_and_saves_credentials(self):
        expected_state: StorageStateData = {
            "cookies": [{"name": "FedAuth", "value": "cookie-v", "domain": ".sharepoint.com"}]
        }
        moodle = FakeMoodleTokenPort(token="tok-abc")
        sharepoint = FakeSharepointAuthPort(storage_state=expected_state)
        store = InMemoryCredentialStore()
        service = AuthService(
            moodle_auth=moodle,
            sharepoint_auth=sharepoint,
            credential_store=store,
        )

        ok = asyncio.run(service.login("jdoe@idat.pe", "secret", _mfa_code))

        self.assertTrue(ok)
        self.assertEqual(moodle.calls, [("jdoe", "secret")])
        self.assertEqual(sharepoint.calls, [("jdoe", "secret", "123456")])
        self.assertEqual(
            store.stored,
            {"token": "tok-abc", "sharepoint_storage_state": expected_state},
        )

    def test_login_returns_false_without_saving_when_either_provider_fails(self):
        state: StorageStateData = {"cookies": []}

        for token, sp_state in [(None, state), ("", state), ("tok", None)]:
            with self.subTest(token=token, sp_state=sp_state):
                store = InMemoryCredentialStore()
                service = AuthService(
                    moodle_auth=FakeMoodleTokenPort(token=token),
                    sharepoint_auth=FakeSharepointAuthPort(storage_state=sp_state),
                    credential_store=store,
                )
                ok = asyncio.run(service.login("jdoe", "secret", _mfa_code))
                self.assertFalse(ok)
                self.assertIsNone(store.stored)

        failing_store = InMemoryCredentialStore(save_result=False)
        service = AuthService(
            moodle_auth=FakeMoodleTokenPort(token="tok"),
            sharepoint_auth=FakeSharepointAuthPort(storage_state=state),
            credential_store=failing_store,
        )
        self.assertFalse(asyncio.run(service.login("jdoe", "secret", _mfa_code)))

    def test_logout_is_authenticated_and_get_credentials_delegate_to_store(self):
        creds: Credentials = {
            "token": "tok-1",
            "sharepoint_storage_state": {"cookies": []},
        }
        store = InMemoryCredentialStore(initial=creds)
        service = AuthService(
            moodle_auth=FakeMoodleTokenPort(),
            sharepoint_auth=FakeSharepointAuthPort(),
            credential_store=store,
        )

        self.assertTrue(service.is_authenticated())
        self.assertEqual(service.get_credentials(), creds)

        service.logout()

        self.assertTrue(store.cleared)
        self.assertFalse(service.is_authenticated())
        self.assertIsNone(service.get_credentials())

    def test_login_omits_login_flow_ended_parameter(self):
        sig = inspect.signature(AuthService.login)
        self.assertNotIn("login_flow_ended", sig.parameters)
        self.assertEqual(
            list(sig.parameters), ["self", "username", "password", "get_mfa_code"]
        )


class TestJsonCredentialStore(unittest.TestCase):
    def test_save_creates_parent_dir_load_roundtrips_and_clear_deletes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session_path = Path(tmpdir) / "nested" / "session.json"
            store = JsonCredentialStore(session_path)

            self.assertIsNone(store.load())
            store.clear()  # No-op when absent

            creds: Credentials = {
                "token": "tok123",
                "sharepoint_storage_state": {
                    "cookies": [
                        {"name": "FedAuth", "value": "val", "domain": ".sharepoint.com"}
                    ]
                },
            }
            self.assertTrue(store.save(creds))
            self.assertTrue(session_path.exists())
            self.assertEqual(store.load(), creds)

            store.clear()
            self.assertFalse(session_path.exists())
            self.assertIsNone(store.load())

    def test_load_returns_none_for_corrupt_or_incomplete_files(self):
        bad_payloads = [
            "{not valid json",
            "[]",
            "null",
            "{}",
            '{"token": "", "sharepoint_storage_state": {}}',
            '{"token": 123, "sharepoint_storage_state": {}}',
            '{"token": "tok", "sharepoint_storage_state": []}',
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, payload in enumerate(bad_payloads):
                session_path = Path(tmpdir) / f"bad_{idx}.json"
                session_path.write_text(payload, encoding="utf-8")
                with self.subTest(payload=payload):
                    self.assertIsNone(JsonCredentialStore(session_path).load())

    def test_save_returns_false_on_os_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = JsonCredentialStore(Path(tmpdir))  # directory, cannot open for write
            creds: Credentials = {
                "token": "tok",
                "sharepoint_storage_state": {"cookies": []},
            }
            self.assertFalse(store.save(creds))


def _make_mock_playwright():
    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.wait_for_url = AsyncMock()
    mock_page.fill = AsyncMock()
    mock_page.click = AsyncMock()

    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.storage_state = AsyncMock(
        return_value={"cookies": [{"name": "FedAuth", "value": "v", "domain": ".sharepoint.com"}]}
    )
    mock_context.close = AsyncMock()

    mock_browser = MagicMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw = MagicMock()
    mock_pw.devices = {"Galaxy A55 landscape": {}}
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw_cm = MagicMock()
    mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_pw_cm, mock_browser, mock_context, mock_page


class TestSharepointBrowserAuth(unittest.TestCase):
    def test_requires_tenant_hosts_and_omits_login_flow_ended(self):
        with self.assertRaises(TypeError):
            SharepointBrowserAuth()  # type: ignore[call-arg]

        sig = inspect.signature(SharepointBrowserAuth.fetch_storage_state)
        self.assertNotIn("login_flow_ended", sig.parameters)
        self.assertEqual(
            list(sig.parameters), ["self", "username", "password", "get_mfa_code"]
        )

    def test_fetch_storage_state_runs_playwright_sso_and_mfa_flow(self):
        mock_pw_cm, mock_browser, mock_context, mock_page = _make_mock_playwright()
        auth = SharepointBrowserAuth(
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            email_domain="example.edu",
        )

        with patch(
            "idat_sync.infrastructure.auth.async_playwright",
            return_value=mock_pw_cm,
        ), patch("idat_sync.infrastructure.auth.asyncio.sleep", new=AsyncMock()):
            state = asyncio.run(
                auth.fetch_storage_state("jdoe", "pw", _mfa_code)
            )

        self.assertEqual(
            state,
            {"cookies": [{"name": "FedAuth", "value": "v", "domain": ".sharepoint.com"}]},
        )
        goto_urls = [call.args[0] for call in mock_page.goto.call_args_list]
        self.assertEqual(
            goto_urls,
            [
                "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx",
                "https://contoso-my.sharepoint.com/shared",
            ],
        )
        mock_page.fill.assert_any_call("#i0116", "jdoe@example.edu", force=True)
        mock_page.fill.assert_any_call("#i0118", "pw")
        mock_page.fill.assert_any_call("#idTxtBx_SAOTCC_OTC", "123456", force=True)
        mock_context.close.assert_awaited_once()
        mock_browser.close.assert_awaited_once()

    def test_fetch_storage_state_returns_none_on_exception_and_propagates_cancelled(self):
        mock_pw_cm, mock_browser, mock_context, mock_page = _make_mock_playwright()
        mock_page.goto = AsyncMock(side_effect=RuntimeError("network timeout"))
        auth = SharepointBrowserAuth(
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            email_domain="example.edu",
        )

        with patch(
            "idat_sync.infrastructure.auth.async_playwright",
            return_value=mock_pw_cm,
        ):
            state = asyncio.run(auth.fetch_storage_state("jdoe", "pw", _mfa_code))

        self.assertIsNone(state)
        mock_context.close.assert_awaited_once()
        mock_browser.close.assert_awaited_once()

        mock_pw_cm2, mock_browser2, mock_context2, mock_page2 = _make_mock_playwright()
        mock_page2.goto = AsyncMock(side_effect=asyncio.CancelledError())
        with patch(
            "idat_sync.infrastructure.auth.async_playwright",
            return_value=mock_pw_cm2,
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(auth.fetch_storage_state("jdoe", "pw", _mfa_code))

        mock_context2.close.assert_awaited_once()
        mock_browser2.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
