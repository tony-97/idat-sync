import asyncio
import inspect
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from idat_sync.application.auth_service import AuthService
from idat_sync.application.ports import (
    Credentials,
    SharepointAuthPort,
    StorageStateData,
)
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
    def test_login_strips_domain_for_moodle_passes_full_username_to_sharepoint_and_saves(self):
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

        ok = asyncio.run(service.login("jdoe@custom.edu", "secret", _mfa_code))

        self.assertTrue(ok)
        self.assertEqual(moodle.calls, [("jdoe", "secret")])
        self.assertEqual(sharepoint.calls, [("jdoe@custom.edu", "secret", "123456")])
        self.assertEqual(
            store.stored,
            {"token": "tok-abc", "sharepoint_storage_state": expected_state},
        )

    def test_login_runs_providers_concurrently_and_offloads_blocking_calls_to_threads(self):
        main_thread_id = threading.get_ident()
        sp_started = threading.Event()
        fetch_token_threads: list[int] = []
        save_threads: list[int] = []

        class ConcurrentMoodlePort:
            def fetch_token(self, username: str, password: str) -> str | None:
                fetch_token_threads.append(threading.get_ident())
                if not sp_started.wait(timeout=2.0):
                    raise TimeoutError("SharePoint login did not start concurrently")
                return "tok-concurrent"

        class ConcurrentSharepointPort:
            async def fetch_storage_state(
                self, username: str, password: str, get_mfa_code
            ) -> StorageStateData | None:
                await asyncio.sleep(0)
                sp_started.set()
                await get_mfa_code()
                return {"cookies": []}

        class ThreadCheckingStore(InMemoryCredentialStore):
            def save(self, credentials: Credentials) -> bool:
                save_threads.append(threading.get_ident())
                return super().save(credentials)

        store = ThreadCheckingStore()
        service = AuthService(
            moodle_auth=ConcurrentMoodlePort(),
            sharepoint_auth=ConcurrentSharepointPort(),
            credential_store=store,
        )

        ok = asyncio.run(service.login("jdoe@idat.pe", "secret", _mfa_code))

        self.assertTrue(ok)
        self.assertEqual(len(fetch_token_threads), 1)
        self.assertNotEqual(fetch_token_threads[0], main_thread_id)
        self.assertEqual(len(save_threads), 1)
        self.assertNotEqual(save_threads[0], main_thread_id)

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
        for fn in (AuthService.login, SharepointAuthPort.fetch_storage_state):
            sig = inspect.signature(fn)
            self.assertNotIn("login_flow_ended", sig.parameters)
            self.assertEqual(
                list(sig.parameters), ["self", "username", "password", "get_mfa_code"]
            )


class TestJsonCredentialStore(unittest.TestCase):
    def test_save_creates_parent_dir_load_roundtrips_and_clear_deletes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            credentials_path = Path(tmpdir) / "nested" / "credentials.json"
            store = JsonCredentialStore(credentials_path)

            self.assertIsNone(store.load())
            with patch("builtins.print") as mock_print:
                store.clear()  # No-op when absent
            self.assertIn(
                "credentials file",
                " ".join(str(call.args[0]) for call in mock_print.call_args_list),
            )

            creds: Credentials = {
                "token": "tok123",
                "sharepoint_storage_state": {
                    "cookies": [
                        {"name": "FedAuth", "value": "val", "domain": ".sharepoint.com"}
                    ]
                },
            }
            self.assertTrue(store.save(creds))
            self.assertTrue(credentials_path.exists())
            self.assertEqual(store.load(), creds)

            store.clear()
            self.assertFalse(credentials_path.exists())
            self.assertIsNone(store.load())

    def test_load_returns_none_for_corrupt_unreadable_or_incomplete_files(self):
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
                credentials_path = Path(tmpdir) / f"bad_{idx}.json"
                credentials_path.write_text(payload, encoding="utf-8")
                with self.subTest(payload=payload):
                    self.assertIsNone(JsonCredentialStore(credentials_path).load())

            # Unreadable directory/file and PermissionError (including Python 3.10-3.11 stat/exists)
            self.assertIsNone(JsonCredentialStore(Path(tmpdir)).load())
            valid_path = Path(tmpdir) / "valid.json"
            valid_path.write_text(
                '{"token": "tok", "sharepoint_storage_state": {"cookies": []}}',
                encoding="utf-8",
            )
            with patch("builtins.open", side_effect=PermissionError("unreadable")):
                self.assertIsNone(JsonCredentialStore(valid_path).load())
            missing_path = Path(tmpdir) / "missing.json"
            with patch.object(Path, "exists", side_effect=PermissionError("unreadable parent")):
                self.assertIsNone(JsonCredentialStore(missing_path).load())

    def test_save_returns_false_on_os_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = JsonCredentialStore(Path(tmpdir))  # directory, cannot open for write
            creds: Credentials = {
                "token": "tok",
                "sharepoint_storage_state": {"cookies": []},
            }
            with patch("builtins.print") as mock_print:
                self.assertFalse(store.save(creds))
            self.assertIn(
                "credentials file",
                " ".join(str(call.args[0]) for call in mock_print.call_args_list),
            )


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
        auth = SharepointBrowserAuth(
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            email_domain="example.edu",
        )

        for input_user, expected_email in [
            ("jdoe", "jdoe@example.edu"),
            ("jdoe@other.edu", "jdoe@other.edu"),
        ]:
            with self.subTest(input_user=input_user):
                mock_pw_cm, mock_browser, mock_context, mock_page = _make_mock_playwright()
                with patch(
                    "idat_sync.infrastructure.auth.async_playwright",
                    return_value=mock_pw_cm,
                ), patch("idat_sync.infrastructure.auth.asyncio.sleep", new=AsyncMock()):
                    state = asyncio.run(
                        auth.fetch_storage_state(input_user, "pw", _mfa_code)
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
                mock_page.fill.assert_any_call("#i0116", expected_email, force=True)
                mock_page.fill.assert_any_call("#i0118", "pw")
                mock_page.fill.assert_any_call("#idTxtBx_SAOTCC_OTC", "123456", force=True)
                mock_context.close.assert_awaited_once()
                mock_browser.close.assert_awaited_once()

    def test_fetch_storage_state_returns_none_on_exception_and_propagates_cancelled(self):
        auth = SharepointBrowserAuth(
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            email_domain="example.edu",
        )

        # Startup failure in async_playwright().__aenter__
        mock_pw_cm_start, _, _, _ = _make_mock_playwright()
        mock_pw_cm_start.__aenter__ = AsyncMock(side_effect=RuntimeError("driver failed"))
        with patch(
            "idat_sync.infrastructure.auth.async_playwright",
            return_value=mock_pw_cm_start,
        ):
            self.assertIsNone(asyncio.run(auth.fetch_storage_state("jdoe", "pw", _mfa_code)))

        # Launch failure
        mock_pw_cm_launch, mock_browser_launch, _, _ = _make_mock_playwright()
        mock_pw_launch = mock_pw_cm_launch.__aenter__.return_value
        mock_pw_launch.chromium.launch = AsyncMock(side_effect=RuntimeError("launch failed"))
        with patch(
            "idat_sync.infrastructure.auth.async_playwright",
            return_value=mock_pw_cm_launch,
        ):
            self.assertIsNone(asyncio.run(auth.fetch_storage_state("jdoe", "pw", _mfa_code)))
        mock_browser_launch.close.assert_not_awaited()

        # Context creation failure must still close launched browser
        mock_pw_cm_ctx, mock_browser_ctx, mock_context_ctx, _ = _make_mock_playwright()
        mock_browser_ctx.new_context = AsyncMock(side_effect=RuntimeError("context failed"))
        with patch(
            "idat_sync.infrastructure.auth.async_playwright",
            return_value=mock_pw_cm_ctx,
        ):
            self.assertIsNone(asyncio.run(auth.fetch_storage_state("jdoe", "pw", _mfa_code)))
        mock_context_ctx.close.assert_not_awaited()
        mock_browser_ctx.close.assert_awaited_once()

        # Page navigation failure closes context and browser
        mock_pw_cm, mock_browser, mock_context, mock_page = _make_mock_playwright()
        mock_page.goto = AsyncMock(side_effect=RuntimeError("network timeout"))
        with patch(
            "idat_sync.infrastructure.auth.async_playwright",
            return_value=mock_pw_cm,
        ):
            state = asyncio.run(auth.fetch_storage_state("jdoe", "pw", _mfa_code))

        self.assertIsNone(state)
        mock_context.close.assert_awaited_once()
        mock_browser.close.assert_awaited_once()

        # Cancellation closes context and browser and propagates CancelledError
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
