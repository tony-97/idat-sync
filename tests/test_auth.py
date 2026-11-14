import asyncio
import contextlib
import inspect
import io
from pathlib import Path
import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from idat_sync.application.auth_service import AuthService
from idat_sync.application.ports import (
    Credentials,
    SharepointAuthPort,
    StorageStateData,
)
from idat_sync.infrastructure.credential_store import JsonCredentialStore
from idat_sync.infrastructure.sharepoint_auth import SharepointBrowserAuth
from idat_sync.ui.login_frame import LoginFrame


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

        for token, sp_state in [(None, state), ("", state), ("tok", None), ("tok", {})]:
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

    def test_login_cancels_sharepoint_task_and_raises_exception_group_on_moodle_error(self):
        sp_started = threading.Event()
        sp_cancelled: list[bool] = []
        sp_cleaned_up: list[bool] = []

        class FailingMoodlePort:
            def fetch_token(self, username: str, password: str) -> str | None:
                if not sp_started.wait(timeout=2.0):
                    raise TimeoutError("SharePoint login did not start")
                raise RuntimeError("moodle HTTP 500")

        class HangingSharepointPort:
            async def fetch_storage_state(
                self, username: str, password: str, get_mfa_code
            ) -> StorageStateData | None:
                sp_started.set()
                try:
                    await asyncio.get_running_loop().create_future()
                except asyncio.CancelledError:
                    sp_cancelled.append(True)
                    raise
                finally:
                    sp_cleaned_up.append(True)
                return None

        store = InMemoryCredentialStore()
        service = AuthService(
            moodle_auth=FailingMoodlePort(),
            sharepoint_auth=HangingSharepointPort(),
            credential_store=store,
        )

        async def exercise() -> None:
            with self.assertRaises(ExceptionGroup) as ctx:
                await service.login("jdoe", "secret", _mfa_code)
            self.assertEqual(sp_cancelled, [True])
            self.assertEqual(sp_cleaned_up, [True])
            self.assertEqual(
                [str(exc) for exc in ctx.exception.exceptions],
                ["moodle HTTP 500"],
            )

        asyncio.run(exercise())
        self.assertIsNone(store.stored)


class TestJsonCredentialStore(unittest.TestCase):
    def test_save_creates_parent_dir_load_roundtrips_and_clear_deletes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            credentials_path = Path(tmpdir) / "nested" / "credentials.json"
            store = JsonCredentialStore(credentials_file=credentials_path)
            self.assertEqual(store.credentials_file, credentials_path)

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
    mock_page.wait_for_selector = AsyncMock()
    mock_page.is_visible = AsyncMock(return_value=False)
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

        for input_user, expected_email, skip_visible in [
            ("jdoe", "jdoe@example.edu", True),
            ("jdoe@other.edu", "jdoe@other.edu", False),
        ]:
            with self.subTest(input_user=input_user, skip_visible=skip_visible):
                mock_pw_cm, mock_browser, mock_context, mock_page = _make_mock_playwright()
                events: list[tuple[str, object]] = []
                mock_page.wait_for_url = AsyncMock(
                    side_effect=lambda url, **kw: events.append(("wait_for_url", (url, kw.get("timeout"))))
                )
                mock_page.wait_for_selector = AsyncMock(
                    side_effect=lambda sel, **kw: events.append(("wait_for_selector", (sel, kw.get("timeout"))))
                )

                async def _is_visible(sel: str) -> bool:
                    events.append(("is_visible", sel))
                    return skip_visible

                mock_page.is_visible = AsyncMock(side_effect=_is_visible)
                mock_page.click = AsyncMock(
                    side_effect=lambda sel, **kw: events.append(("click", sel))
                )
                with patch(
                    "idat_sync.infrastructure.sharepoint_auth.async_playwright",
                    return_value=mock_pw_cm,
                ), patch(
                    "idat_sync.infrastructure.sharepoint_auth.asyncio.sleep",
                    new=AsyncMock(),
                ):
                    state = asyncio.run(
                        auth.fetch_storage_state(input_user, "pw", _mfa_code)
                    )

                self.assertEqual(
                    state,
                    {"cookies": [{"name": "FedAuth", "value": "v", "domain": ".sharepoint.com"}]},
                )
                mock_launch = mock_pw_cm.__aenter__.return_value.chromium.launch
                mock_launch.assert_awaited_once()
                self.assertIs(mock_launch.call_args.kwargs.get("headless"), False)
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
                expected_post_mfa = [
                    ("click", "#idDiv_SAOTCS_Proofs > div:nth-child(2)"),
                    ("click", "#idChkBx_SAOTCC_TD"),
                    ("click", "#idSubmit_SAOTCC_Continue"),
                    ("wait_for_url", ("https://login.microsoftonline.com/common/SAS/ProcessAuth", 60000 * 3)),
                    ("wait_for_selector", ("#skipMfaRegistrationLink, #KmsiCheckboxField", 60000 * 3)),
                    ("is_visible", "#skipMfaRegistrationLink"),
                    *(
                        [("click", "#skipMfaRegistrationLink")]
                        if skip_visible
                        else []
                    ),
                    ("click", "#KmsiCheckboxField"),
                    ("click", "#idSIButton9"),
                    ("wait_for_url", ("https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx", 60000 * 3)),
                ]
                mfa_start = events.index(("click", "#idDiv_SAOTCS_Proofs > div:nth-child(2)"))
                self.assertEqual(events[mfa_start:], expected_post_mfa)
                mock_context.close.assert_awaited_once()
                mock_browser.close.assert_awaited_once()

    def test_fetch_storage_state_returns_none_on_exception_and_propagates_cancelled(self):
        auth = SharepointBrowserAuth(
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            email_domain="example.edu",
        )

        def run_failure_case(
            configure_mocks,
            *,
            expected_exception: type[BaseException] | None = None,
            expect_context_close: bool = False,
            expect_browser_close: bool = False,
        ) -> None:
            mock_pw_cm, mock_browser, mock_context, mock_page = _make_mock_playwright()
            configure_mocks(mock_pw_cm, mock_browser, mock_context, mock_page)
            with patch(
                "idat_sync.infrastructure.sharepoint_auth.async_playwright",
                return_value=mock_pw_cm,
            ), patch(
                "idat_sync.infrastructure.sharepoint_auth.asyncio.sleep",
                new=AsyncMock(),
            ):
                if expected_exception is not None:
                    with self.assertRaises(expected_exception):
                        asyncio.run(auth.fetch_storage_state("jdoe", "pw", _mfa_code))
                else:
                    self.assertIsNone(
                        asyncio.run(auth.fetch_storage_state("jdoe", "pw", _mfa_code))
                    )
            if expect_context_close:
                mock_context.close.assert_awaited_once()
            else:
                mock_context.close.assert_not_awaited()
            if expect_browser_close:
                mock_browser.close.assert_awaited_once()
            else:
                mock_browser.close.assert_not_awaited()

        # Startup failure in async_playwright().__aenter__
        run_failure_case(
            lambda cm, _b, _c, _p: setattr(
                cm, "__aenter__", AsyncMock(side_effect=RuntimeError("driver failed"))
            ),
        )

        # Launch failure
        run_failure_case(
            lambda cm, _b, _c, _p: setattr(
                cm.__aenter__.return_value.chromium,
                "launch",
                AsyncMock(side_effect=RuntimeError("launch failed")),
            ),
        )

        # Context creation failure must still close launched browser
        run_failure_case(
            lambda _cm, b, _c, _p: setattr(
                b, "new_context", AsyncMock(side_effect=RuntimeError("context failed"))
            ),
            expect_browser_close=True,
        )

        # Page navigation failure closes context and browser
        run_failure_case(
            lambda _cm, _b, _c, p: setattr(
                p, "goto", AsyncMock(side_effect=RuntimeError("network timeout"))
            ),
            expect_context_close=True,
            expect_browser_close=True,
        )

        # Context cleanup failure still closes browser
        run_failure_case(
            lambda _cm, _b, c, _p: setattr(
                c, "close", AsyncMock(side_effect=RuntimeError("context close failed"))
            ),
            expect_context_close=True,
            expect_browser_close=True,
        )

        # Cancellation closes context and browser and propagates CancelledError
        run_failure_case(
            lambda _cm, _b, _c, p: setattr(
                p, "goto", AsyncMock(side_effect=asyncio.CancelledError())
            ),
            expected_exception=asyncio.CancelledError,
            expect_context_close=True,
            expect_browser_close=True,
        )

        # Cancellation still propagates if context.close() raises during cleanup
        def cancel_with_close_failure(_cm, _b, c, p) -> None:
            p.goto = AsyncMock(side_effect=asyncio.CancelledError())
            c.close = AsyncMock(side_effect=RuntimeError("context close failed"))

        run_failure_case(
            cancel_with_close_failure,
            expected_exception=asyncio.CancelledError,
            expect_context_close=True,
            expect_browser_close=True,
        )


class TestLoginFrame(unittest.TestCase):
    def _create_master(self):
        class StubMaster(tk.Tk):
            def __init__(self) -> None:
                super().__init__()
                self.withdraw()
                self.switched_to_sync = 0

            def center_window(self, window_width: int, window_height: int) -> None:
                self.centered_size = (window_width, window_height)

            def switch_to_sync(self) -> None:
                self.switched_to_sync += 1

        master = StubMaster()
        self.addCleanup(master.destroy)
        return master

    def _login_button(self, frame: LoginFrame) -> tk.Button:
        return next(w for w in frame.winfo_children() if isinstance(w, tk.Button))

    def _set_entry(self, entry: tk.Entry, value: str) -> None:
        entry.delete(0, tk.END)
        entry.insert(0, value)

    def test_login_button_disables_while_blank_or_logging_in_and_guards_submit(self):
        expected_state: StorageStateData = {
            "cookies": [{"name": "FedAuth", "value": "cookie-v", "domain": ".sharepoint.com"}]
        }

        class ControllableSharepointAuth:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []
                self.error: Exception | None = None
                self.started: asyncio.Event | None = None
                self.release: asyncio.Event | None = None

            async def fetch_storage_state(
                self, username: str, password: str, get_mfa_code
            ) -> StorageStateData | None:
                self.calls.append((username, password))
                if self.started is not None:
                    self.started.set()
                if self.release is not None:
                    await self.release.wait()
                if self.error is not None:
                    raise self.error
                return expected_state

        moodle = FakeMoodleTokenPort(token="tok-ui")
        sharepoint = ControllableSharepointAuth()
        store = InMemoryCredentialStore()
        auth = AuthService(
            moodle_auth=moodle,
            sharepoint_auth=sharepoint,
            credential_store=store,
        )
        master = self._create_master()
        frame = LoginFrame(master, auth)  # type: ignore[arg-type]
        login_button = self._login_button(frame)

        self.assertEqual(str(login_button["state"]), "disabled")

        self._set_entry(frame.user_entry, "   ")
        self._set_entry(frame.pass_entry, " secret ")
        self.assertEqual(str(login_button["state"]), "disabled")

        self._set_entry(frame.user_entry, " jdoe@custom.edu ")
        self._set_entry(frame.pass_entry, "")
        self.assertEqual(str(login_button["state"]), "disabled")

        self._set_entry(frame.pass_entry, " secret ")
        self.assertEqual(str(login_button["state"]), "normal")

        self._set_entry(frame.user_entry, "   ")
        self.assertEqual(str(login_button["state"]), "disabled")

        self._set_entry(frame.user_entry, " jdoe@custom.edu ")
        self.assertEqual(str(login_button["state"]), "normal")

        async def exercise_do_login() -> None:
            # Failed concurrent submission: disables during flight, blocks duplicate Return, resets in finally
            sharepoint.started = asyncio.Event()
            sharepoint.release = asyncio.Event()
            sharepoint.error = RuntimeError("sso boom")

            stderr_buf = io.StringIO()
            with contextlib.redirect_stderr(stderr_buf):
                task = asyncio.create_task(frame.do_login())
                await sharepoint.started.wait()
                self.assertEqual(str(login_button["state"]), "disabled")

                # Duplicate submit while login is active must be ignored
                await frame.do_login()
                self.assertEqual(len(sharepoint.calls), 1)

                sharepoint.release.set()
                await task

            self.assertIn("sso boom", stderr_buf.getvalue())
            self.assertEqual(str(login_button["state"]), "normal")
            self.assertEqual(master.switched_to_sync, 0)

            # Successful submission strips username, preserves password, switches to sync
            sharepoint.started = None
            sharepoint.release = None
            sharepoint.error = None
            await frame.do_login()

            self.assertEqual(moodle.calls, [("jdoe", " secret "), ("jdoe", " secret ")])
            self.assertEqual(
                sharepoint.calls,
                [("jdoe@custom.edu", " secret "), ("jdoe@custom.edu", " secret ")],
            )
            self.assertEqual(master.switched_to_sync, 1)
            self.assertEqual(str(login_button["state"]), "normal")

            # Blank username submit is ignored and keeps button disabled
            self._set_entry(frame.user_entry, "   ")
            self.assertEqual(str(login_button["state"]), "disabled")
            await frame.do_login()
            self.assertEqual(len(moodle.calls), 2)
            self.assertEqual(str(login_button["state"]), "disabled")

        asyncio.run(exercise_do_login())

    def test_credentials_expired_error_exported_without_vendor_imports(self):
        import ast
        from idat_sync.application import ports

        err_cls = getattr(ports, "CredentialsExpiredError")
        self.assertTrue(issubclass(err_cls, RuntimeError))
        self.assertIn("CredentialsExpiredError", ports.__all__)

        tree = ast.parse(Path(ports.__file__).read_text(encoding="utf-8"))
        imported = {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertEqual(
            imported & {"requests", "office365", "yt_dlp", "playwright", "tkinter"},
            set(),
        )


class TestSyncFrame(unittest.TestCase):
    def test_sync_frame_treeview_selection_progress_and_error_recovery(self):
        from idat_sync.application.ports import CredentialsExpiredError
        from idat_sync.config import SyncConfig
        from idat_sync.domain.course import Course
        import idat_sync.ui.app as app_module
        from idat_sync.ui.sync_frame import SyncFrame

        self.assertFalse(hasattr(app_module, "ProgressInterceptor"))

        with tempfile.TemporaryDirectory() as tmpdir:
            creds_file = Path(tmpdir) / "session.json"
            creds_file.write_text('{"token": "tok"}', encoding="utf-8")
            config = SyncConfig(session_file=creds_file)
            creds: Credentials = {
                "token": "tok",
                "sharepoint_storage_state": {"cookies": [], "origins": []},
            }

            past_course = Course(
                id=1, name="I.01.2025-IIA PAST", category="I.01.2025-IIA"
            )
            current_course = Course(
                id=2, name="IV.01.2026-III CURRENT", category="IV.01.2026-III"
            )

            async def exercise():
                class StubMaster(tk.Tk):
                    def __init__(self) -> None:
                        super().__init__()
                        self.withdraw()
                        self.loop = asyncio.get_running_loop()
                        self.switched_to_login = 0

                    def center_window(self, w: int, h: int) -> None:
                        pass

                    def switch_to_login(self) -> None:
                        self.switched_to_login += 1

                master = StubMaster()
                try:
                    frame = SyncFrame(master, creds, config)  # type: ignore[arg-type]
                    self.assertFalse(hasattr(frame, "output_text"))
                    try:
                        mock_sync = MagicMock()
                        mock_sync.list_courses.return_value = [
                            past_course,
                            current_course,
                        ]

                        def fake_sync_courses(
                            root_folder, courses, progress_listener=None
                        ):
                            self.assertEqual(root_folder, tmpdir)
                            self.assertEqual(courses, [current_course])
                            assert progress_listener is not None
                            c_dir = Path(tmpdir) / current_course.name
                            synced_p = c_dir / "S1" / "already.pdf"
                            ok_p = c_dir / "S1" / "new.pdf"
                            err_p = c_dir / "S1" / "broken.pdf"
                            progress_listener.on_course_start(current_course)
                            progress_listener.on_course_files(
                                current_course,
                                c_dir,
                                [synced_p],
                                [ok_p, err_p],
                            )
                            progress_listener.on_task_start(current_course, ok_p)
                            progress_listener.on_task_complete(current_course, ok_p)
                            progress_listener.on_task_start(current_course, err_p)
                            progress_listener.on_task_error(
                                current_course, err_p, OSError("disk full")
                            )
                            progress_listener.on_course_complete(
                                current_course, RuntimeError("partial")
                            )

                        mock_sync.sync_courses.side_effect = fake_sync_courses

                        with patch(
                            "idat_sync.ui.sync_frame.create_sync_service",
                            side_effect=[RuntimeError("network offline"), mock_sync],
                        ) as mock_factory:
                            # 1. Startup failure recovers controls and preserves credentials
                            await frame.load_idat_sync(creds)
                            await asyncio.sleep(0)
                            self.assertIsNone(frame.idat_sync)
                            self.assertFalse(frame.is_syncing.get())
                            self.assertEqual(str(frame.browse_button["state"]), "normal")
                            self.assertEqual(str(frame.logout_button["state"]), "normal")
                            self.assertEqual(master.switched_to_login, 0)
                            self.assertTrue(creds_file.exists())
                            self.assertIn("network offline", frame.status_var.get())

                            # 2. Selecting folder and calling do_sync retries create_sync_service
                            frame.folder_path.set(tmpdir)
                            self.assertEqual(str(frame.sync_button["state"]), "normal")
                            await frame.do_sync()
                            await asyncio.sleep(0)
                            self.assertEqual(mock_factory.call_count, 2)
                            self.assertFalse(frame.is_syncing.get())

                            # Verify ordering: in-progress first (checked icon), past second (unchecked icon)
                            top_items = frame.tree.get_children("")
                            self.assertEqual(len(top_items), 2)
                            self.assertEqual(
                                frame.tree.item(top_items[0], "text"),
                                current_course.name,
                            )
                            self.assertEqual(
                                frame.tree.item(top_items[0], "image"),
                                (str(frame._checked_icon),),
                            )
                            self.assertEqual(
                                frame.tree.item(top_items[1], "text"),
                                past_course.name,
                            )
                            self.assertEqual(
                                frame.tree.item(top_items[1], "image"),
                                (str(frame._unchecked_icon),),
                            )

                            # Verify parent & nested folder/file progress rows with icons
                            self.assertEqual(
                                frame.tree.item(top_items[0], "values"),
                                ("Failed", "66%"),
                            )
                            folder_items = frame.tree.get_children(top_items[0])
                            self.assertEqual(len(folder_items), 1)
                            self.assertEqual(
                                frame.tree.item(folder_items[0], "text"), "S1"
                            )
                            self.assertEqual(
                                frame.tree.item(folder_items[0], "image"),
                                (str(frame._folder_icon),),
                            )
                            file_items = frame.tree.get_children(folder_items[0])
                            child_rows = {
                                frame.tree.item(cid, "text"): (
                                    frame.tree.item(cid, "values"),
                                    frame.tree.item(cid, "image"),
                                )
                                for cid in file_items
                            }
                            self.assertEqual(
                                child_rows,
                                {
                                    "already.pdf": (
                                        ("Synced", "100%"),
                                        (str(frame._file_icon),),
                                    ),
                                    "new.pdf": (
                                        ("Completed", "100%"),
                                        (str(frame._file_icon),),
                                    ),
                                    "broken.pdf": (
                                        ("Failed", "0%"),
                                        (str(frame._file_icon),),
                                    ),
                                },
                            )

                            # 3. Quick-select buttons & row-click toggling
                            frame.clear_selection()
                            self.assertEqual(str(frame.sync_button["state"]), "disabled")
                            frame.select_all()
                            self.assertEqual(str(frame.sync_button["state"]), "normal")
                            self.assertTrue(
                                all(
                                    frame.tree.item(iid, "image")
                                    == (str(frame._checked_icon),)
                                    for iid in frame.tree.get_children("")
                                )
                            )
                            frame.select_in_progress()
                            self.assertEqual(
                                frame.tree.item(top_items[0], "image"),
                                (str(frame._checked_icon),),
                            )
                            self.assertEqual(
                                frame.tree.item(top_items[1], "image"),
                                (str(frame._unchecked_icon),),
                            )
                            frame.toggle_course_row(top_items[0])
                            self.assertEqual(str(frame.sync_button["state"]), "disabled")
                            frame.toggle_course_row(top_items[0])
                            self.assertEqual(str(frame.sync_button["state"]), "normal")

                            # 4. CredentialsExpiredError during sync prompts Logout without wiping file
                            mock_sync.sync_courses.side_effect = CredentialsExpiredError(
                                "401 expired"
                            )
                            await frame.do_sync()
                            await asyncio.sleep(0)
                            self.assertFalse(frame.is_syncing.get())
                            self.assertEqual(master.switched_to_login, 0)
                            self.assertTrue(creds_file.exists())
                            self.assertIn("Logout", frame.status_var.get())
                    finally:
                        frame.exit()
                        await asyncio.sleep(0)
                finally:
                    master.destroy()

            asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
