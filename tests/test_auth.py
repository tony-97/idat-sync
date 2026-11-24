"""Unit and UI integration tests for authentication, credential storage, and frames."""
# pylint: disable=too-many-lines

import ast
import asyncio
import importlib.resources
import importlib.util
import inspect
import json
import sys
import tempfile
import threading
import types
import unittest
import xml.etree.ElementTree as ET  # noqa: S405
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import idat_sync.main as main_module
import idat_sync.ui as ui_pkg
import idat_sync.ui.app as app_module
import idat_sync.ui.sync_frame as sync_frame_mod
from idat_sync.application import exceptions, ports
from idat_sync.application.auth_service import AuthService
from idat_sync.application.events import (
    CourseDiscoveryCompleted,
    CourseFileDiscovered,
    CourseSyncCompleted,
    CourseSyncStarted,
    TaskDownloadCompleted,
    TaskDownloadFailed,
    TaskDownloadProgress,
    TaskDownloadStarted,
)
from idat_sync.application.ports import (
    AuthenticationError,
    Credentials,
    CredentialsExpiredError,
    CredentialStoreError,
    FileSystemError,
    IDATSyncError,
    RemoteServiceError,
    SharepointAuthPort,
    StorageStateData,
)
from idat_sync.application.sync_service import IDATSync
from idat_sync.config import WEBVIEW_USER_AGENT, SyncConfig
from idat_sync.domain.course import Course, CourseSection
from idat_sync.domain.sync import ContentSyncState
from idat_sync.infrastructure.credential_store import JsonCredentialStore
from idat_sync.ui.app import AppFrame, MainApp
from idat_sync.ui.course_tree import CourseTree, RowStatus
from idat_sync.ui.error_format import format_ui_error
from idat_sync.ui.login_frame import LoginFrame
from idat_sync.ui.sync_frame import SyncFrame
from tests.test_architecture import FORBIDDEN_INNER_LAYER_IMPORTS


class FakeMoodleTokenPort:  # pylint: disable=too-few-public-methods
    """In-memory fake MoodleTokenPort for AuthService tests."""

    def __init__(self, token: str | None = "moodle-tok") -> None:  # noqa: S107
        """Initialize the fake with a fixed token response."""
        self.token = token
        self.calls: list[tuple[str, str]] = []

    async def fetch_token(self, username: str, password: str) -> str | None:
        """Record credentials and return the configured token."""
        self.calls.append((username, password))
        return self.token


class FakeSharepointAuthPort:  # pylint: disable=too-few-public-methods
    """In-memory fake SharepointAuthPort for AuthService tests."""

    def __init__(self, storage_state: StorageStateData | None = None) -> None:
        """Initialize the fake with a fixed storage state response."""
        self.storage_state = storage_state
        self.calls: list[tuple[str, str, str]] = []

    async def fetch_storage_state(
        self, username: str, password: str, get_mfa_code
    ) -> StorageStateData | None:
        """Await MFA callback, record inputs, and return storage state."""
        code = await get_mfa_code()
        self.calls.append((username, password, code))
        return self.storage_state


class InMemoryCredentialStore:
    """In-memory CredentialStorePort implementation for tests."""

    def __init__(
        self,
        initial: Credentials | None = None,
        *,
        save_result: bool = True,
    ) -> None:
        """Initialize store with optional initial Credentials and save result."""
        self.stored = initial
        self.save_result = save_result
        self.cleared = False

    def load(self) -> Credentials | None:
        """Return stored Credentials."""
        return self.stored

    def save(self, credentials: Credentials) -> None:
        """Store Credentials or raise CredentialStoreError on failure."""
        if not self.save_result:
            raise CredentialStoreError("Could not create credentials file")
        self.stored = credentials

    def clear(self) -> None:
        """Clear stored Credentials."""
        self.stored = None
        self.cleared = True


async def _mfa_code() -> str:
    return "123456"


class TestAuthService(unittest.TestCase):
    """Tests for AuthService login orchestration, cancellation, and storage."""

    def test_login_strips_domain_for_moodle_and_saves_credentials(self):
        """Strip email domain for Moodle while passing full username to SharePoint."""
        expected_state: StorageStateData = {
            "cookies": [
                {"name": "FedAuth", "value": "cookie-v", "domain": ".sharepoint.com"}
            ]
        }
        moodle = FakeMoodleTokenPort(token="tok-abc")  # noqa: S106
        sharepoint = FakeSharepointAuthPort(storage_state=expected_state)
        store = InMemoryCredentialStore()
        service = AuthService(
            moodle_auth=moodle,
            sharepoint_auth=sharepoint,
            credential_store=store,
        )

        assert (
            asyncio.run(service.login("jdoe@custom.edu", "secret", _mfa_code)) is None
        )
        assert moodle.calls == [("jdoe", "secret")]
        assert sharepoint.calls == [("jdoe@custom.edu", "secret", "123456")]
        assert store.stored == {
            "token": "tok-abc",
            "sharepoint_storage_state": expected_state,
        }

    def test_login_runs_providers_concurrently_and_offloads_save_to_thread(self):
        """Run Moodle and SharePoint concurrently and save Credentials off-thread."""
        main_thread_id = threading.get_ident()
        save_threads: list[int] = []

        async def exercise() -> None:
            sp_started = asyncio.Event()

            class ConcurrentMoodlePort:  # pylint: disable=too-few-public-methods
                """Stub Moodle port waiting on SharePoint start."""

                async def fetch_token(
                    self, _username: str, _password: str
                ) -> str | None:
                    """Wait for SharePoint start and return token."""
                    await asyncio.wait_for(sp_started.wait(), timeout=2.0)
                    return "tok-concurrent"

            class ConcurrentSharepointPort:  # pylint: disable=too-few-public-methods
                """Stub SharePoint port signalling concurrent execution."""

                async def fetch_storage_state(
                    self, _username: str, _password: str, get_mfa_code
                ) -> StorageStateData | None:
                    """Signal start, await MFA, and return empty cookies."""
                    await asyncio.sleep(0)
                    sp_started.set()
                    await get_mfa_code()
                    return {"cookies": []}

            class ThreadCheckingStore(InMemoryCredentialStore):
                """Store recording which thread executes save()."""

                def save(self, credentials: Credentials) -> None:
                    save_threads.append(threading.get_ident())
                    super().save(credentials)

            store = ThreadCheckingStore()
            service = AuthService(
                moodle_auth=ConcurrentMoodlePort(),  # type: ignore[arg-type]
                sharepoint_auth=ConcurrentSharepointPort(),  # type: ignore[arg-type]
                credential_store=store,
            )
            await service.login("jdoe@idat.pe", "secret", _mfa_code)

        asyncio.run(exercise())

        assert len(save_threads) == 1
        assert save_threads[0] != main_thread_id

    def test_login_raises_without_saving_when_either_provider_or_store_fails(self):
        """Raise AuthenticationError or CredentialStoreError without saving."""
        state: StorageStateData = {"cookies": []}

        for token, sp_state in [(None, state), ("", state), ("tok", None), ("tok", {})]:
            with self.subTest(token=token, sp_state=sp_state):
                store = InMemoryCredentialStore()
                service = AuthService(
                    moodle_auth=FakeMoodleTokenPort(token=token),
                    sharepoint_auth=FakeSharepointAuthPort(storage_state=sp_state),
                    credential_store=store,
                )
                with self.assertRaises(AuthenticationError):
                    asyncio.run(service.login("jdoe", "secret", _mfa_code))
                assert store.stored is None

        failing_store = InMemoryCredentialStore(save_result=False)
        service = AuthService(
            moodle_auth=FakeMoodleTokenPort(token="tok"),  # noqa: S106
            sharepoint_auth=FakeSharepointAuthPort(storage_state=state),
            credential_store=failing_store,
        )
        with self.assertRaises(CredentialStoreError):
            asyncio.run(service.login("jdoe", "secret", _mfa_code))

    def test_logout_is_authenticated_and_get_credentials_delegate_to_store(self):
        """Delegate is_authenticated, get_credentials, and logout to store."""
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

        assert service.is_authenticated()
        assert service.get_credentials() == creds

        service.logout()

        assert store.cleared
        assert not service.is_authenticated()
        assert service.get_credentials() is None

    def test_login_omits_login_flow_ended_parameter(self):
        """Ensure AuthService.login and SharepointAuthPort omit login_flow_ended."""
        for fn in (AuthService.login, SharepointAuthPort.fetch_storage_state):
            sig = inspect.signature(fn)
            assert "login_flow_ended" not in sig.parameters
            assert list(sig.parameters) == [
                "self",
                "username",
                "password",
                "get_mfa_code",
            ]

    def test_auth_service_omits_authenticate_wrapper(self):
        """Ensure AuthService does not expose the removed authenticate() wrapper."""
        assert not hasattr(AuthService, "authenticate")

    def test_login_cancels_sharepoint_task_on_moodle_error(self):
        """Cancel sibling SharePoint task and unwrap IDATSyncError on error."""
        sp_cancelled: list[bool] = []
        sp_cleaned_up: list[bool] = []
        store = InMemoryCredentialStore()

        async def exercise() -> None:
            sp_started = asyncio.Event()

            class FailingMoodlePort:  # pylint: disable=too-few-public-methods
                """Stub Moodle port raising RemoteServiceError."""

                async def fetch_token(
                    self, _username: str, _password: str
                ) -> str | None:
                    """Raise RemoteServiceError once SharePoint starts."""
                    await asyncio.wait_for(sp_started.wait(), timeout=2.0)
                    raise RemoteServiceError("moodle HTTP 500")

            class HangingSharepointPort:  # pylint: disable=too-few-public-methods
                """Stub SharePoint port recording cancellation."""

                async def fetch_storage_state(
                    self, _username: str, _password: str, _get_mfa_code
                ) -> StorageStateData | None:
                    """Wait indefinitely until cancelled."""
                    sp_started.set()
                    try:
                        await asyncio.get_running_loop().create_future()
                    except asyncio.CancelledError:
                        sp_cancelled.append(True)
                        raise
                    finally:
                        sp_cleaned_up.append(True)
                    return None

            service = AuthService(
                moodle_auth=FailingMoodlePort(),  # type: ignore[arg-type]
                sharepoint_auth=HangingSharepointPort(),  # type: ignore[arg-type]
                credential_store=store,
            )

            with self.assertRaisesRegex(RemoteServiceError, "moodle HTTP 500"):
                await service.login("jdoe", "secret", _mfa_code)
            assert sp_cancelled == [True]
            assert sp_cleaned_up == [True]

        asyncio.run(exercise())
        assert store.stored is None

    def test_login_cancels_sibling_on_moodle_authentication_error(self):
        """Cancel SharePoint and raise AuthenticationError directly."""
        assert issubclass(AuthenticationError, IDATSyncError)

        async def exercise() -> None:
            sp_started = asyncio.Event()
            sp_cancelled: list[bool] = []

            class RejectedMoodlePort:  # pylint: disable=too-few-public-methods
                """Stub Moodle port rejecting credentials."""

                async def fetch_token(self, _username: str, _password: str) -> str:
                    """Raise AuthenticationError."""
                    await asyncio.wait_for(sp_started.wait(), timeout=2.0)
                    raise AuthenticationError("invalid moodle creds")

            class SlowSharepointPort:  # pylint: disable=too-few-public-methods
                """Stub SharePoint port waiting for cancellation."""

                async def fetch_storage_state(
                    self, _username: str, _password: str, _get_mfa_code
                ) -> StorageStateData:
                    """Wait indefinitely until cancelled."""
                    sp_started.set()
                    try:
                        await asyncio.get_running_loop().create_future()
                    except asyncio.CancelledError:
                        sp_cancelled.append(True)
                        raise
                    raise AssertionError("unreachable")

            store = InMemoryCredentialStore()
            service = AuthService(
                moodle_auth=RejectedMoodlePort(),  # type: ignore[arg-type]
                sharepoint_auth=SlowSharepointPort(),  # type: ignore[arg-type]
                credential_store=store,
            )
            with self.assertRaisesRegex(AuthenticationError, "invalid moodle creds"):
                await service.login("jdoe", "bad", _mfa_code)
            assert sp_cancelled == [True]
            assert store.stored is None

        asyncio.run(exercise())

    def test_login_cancels_sibling_on_sharepoint_auth_and_mixed_errors(self):
        """Cancel Moodle on SharePoint AuthenticationError and surface crashes."""

        async def exercise_sharepoint_rejected() -> None:
            moodle_started = asyncio.Event()
            moodle_cancelled: list[bool] = []

            class SlowMoodlePort:  # pylint: disable=too-few-public-methods
                """Stub Moodle port waiting for cancellation."""

                async def fetch_token(self, _username: str, _password: str) -> str:
                    """Wait indefinitely until cancelled."""
                    moodle_started.set()
                    try:
                        await asyncio.get_running_loop().create_future()
                    except asyncio.CancelledError:
                        moodle_cancelled.append(True)
                        raise
                    raise AssertionError("unreachable")

            class RejectedSharepointPort:  # pylint: disable=too-few-public-methods
                """Stub SharePoint port rejecting MFA."""

                async def fetch_storage_state(
                    self, _username: str, _password: str, _get_mfa_code
                ) -> StorageStateData:
                    """Raise AuthenticationError."""
                    await asyncio.wait_for(moodle_started.wait(), timeout=2.0)
                    raise AuthenticationError("blank MFA")

            store = InMemoryCredentialStore()
            service = AuthService(
                moodle_auth=SlowMoodlePort(),  # type: ignore[arg-type]
                sharepoint_auth=RejectedSharepointPort(),  # type: ignore[arg-type]
                credential_store=store,
            )
            with self.assertRaisesRegex(AuthenticationError, "blank MFA"):
                await service.login("jdoe", "bad", _mfa_code)
            assert moodle_cancelled == [True]
            assert store.stored is None

        async def exercise_mixed_error() -> None:
            class RejectedMoodlePort:  # pylint: disable=too-few-public-methods
                """Stub Moodle port raising AuthenticationError."""

                async def fetch_token(self, _username: str, _password: str) -> str:
                    """Raise AuthenticationError."""
                    raise AuthenticationError("rejected")

            class CrashingSharepointPort:  # pylint: disable=too-few-public-methods
                """Stub SharePoint port raising RuntimeError."""

                async def fetch_storage_state(
                    self, _username: str, _password: str, _get_mfa_code
                ) -> StorageStateData:
                    """Raise RuntimeError."""
                    raise RuntimeError("sp crash")

            service = AuthService(
                moodle_auth=RejectedMoodlePort(),  # type: ignore[arg-type]
                sharepoint_auth=CrashingSharepointPort(),  # type: ignore[arg-type]
                credential_store=InMemoryCredentialStore(),
            )
            with self.assertRaises(ExceptionGroup) as ctx:
                await service.login("jdoe", "bad", _mfa_code)
            assert [str(e) for e in ctx.exception.exceptions] == ["sp crash"]

        asyncio.run(exercise_sharepoint_rejected())
        asyncio.run(exercise_mixed_error())


class TestJsonCredentialStore(unittest.TestCase):
    """Tests for JsonCredentialStore persistence, atomic writes, and permissions."""

    def test_save_creates_parent_dir_load_roundtrips_and_clear_deletes(self):
        """Create parent directories, round-trip Credentials, and clear file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            credentials_path = Path(tmpdir) / "nested" / "credentials.json"
            store = JsonCredentialStore(credentials_file=credentials_path)
            assert store.credentials_file == credentials_path

            assert store.load() is None
            with self.assertLogs(
                "idat_sync.infrastructure.credential_store", level="INFO"
            ) as cm:
                store.clear()  # No-op when absent
            assert "credentials file" in " ".join(cm.output)

            creds: Credentials = {
                "token": "tok123",
                "sharepoint_storage_state": {
                    "cookies": [
                        {"name": "FedAuth", "value": "val", "domain": ".sharepoint.com"}
                    ]
                },
            }
            assert store.save(creds) is None
            assert credentials_path.exists()
            assert store.load() == creds

            store.clear()
            assert not credentials_path.exists()
            assert store.load() is None

    def test_load_returns_none_for_corrupt_unreadable_or_incomplete_files(self):
        """Return None on malformed JSON, incomplete dicts, or PermissionError."""
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
                    assert JsonCredentialStore(credentials_path).load() is None

            assert JsonCredentialStore(Path(tmpdir)).load() is None
            valid_path = Path(tmpdir) / "valid.json"
            valid_path.write_text(
                '{"token": "tok", "sharepoint_storage_state": {"cookies": []}}',
                encoding="utf-8",
            )
            with patch.object(Path, "open", side_effect=PermissionError("unreadable")):
                assert JsonCredentialStore(valid_path).load() is None
            missing_path = Path(tmpdir) / "missing.json"
            with patch.object(
                Path, "exists", side_effect=PermissionError("unreadable parent")
            ):
                assert JsonCredentialStore(missing_path).load() is None

    def test_save_and_clear_raise_credential_store_error_on_os_error(self):
        """Raise CredentialStoreError and log error when saving or clearing fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = JsonCredentialStore(Path(tmpdir))
            creds: Credentials = {
                "token": "tok",
                "sharepoint_storage_state": {"cookies": []},
            }
            with (
                self.assertLogs(
                    "idat_sync.infrastructure.credential_store", level="ERROR"
                ) as cm,
                self.assertRaisesRegex(
                    CredentialStoreError, "credentials file"
                ) as save_ctx,
            ):
                store.save(creds)
            assert isinstance(save_ctx.exception.__cause__, OSError)
            assert "credentials file" in " ".join(cm.output)

            clear_target = Path(tmpdir) / "locked.json"
            clear_target.write_text("{}", encoding="utf-8")
            clear_store = JsonCredentialStore(clear_target)
            with (
                patch.object(Path, "unlink", side_effect=PermissionError("locked")),
                self.assertLogs(
                    "idat_sync.infrastructure.credential_store", level="ERROR"
                ) as clear_cm,
                self.assertRaisesRegex(
                    CredentialStoreError, "credentials file"
                ) as clear_ctx,
            ):
                clear_store.clear()
            assert isinstance(clear_ctx.exception.__cause__, OSError)
            assert "credentials file" in " ".join(clear_cm.output)

    def test_save_restricts_credentials_file_permissions_to_owner(self):
        """Apply 0o600 permissions to the temporary credentials file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            credentials_path = Path(tmpdir) / "session.json"
            store = JsonCredentialStore(credentials_file=credentials_path)
            creds: Credentials = {
                "token": "tok",
                "sharepoint_storage_state": {"cookies": []},
            }
            with patch.object(Path, "chmod") as mock_chmod:
                assert store.save(creds) is None

            mock_chmod.assert_called_once_with(0o600)

    def test_save_preserves_existing_credentials_and_cleans_part_on_failure(self):
        """Keep existing Credentials intact and unlink .part file on replace error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            credentials_path = Path(tmpdir) / "session.json"
            part_path = credentials_path.with_name("session.json.part")
            store = JsonCredentialStore(credentials_file=credentials_path)
            original: Credentials = {
                "token": "original-tok",
                "sharepoint_storage_state": {"cookies": []},
            }
            updated: Credentials = {
                "token": "updated-tok",
                "sharepoint_storage_state": {"cookies": []},
            }
            assert store.save(original) is None

            with (
                patch.object(Path, "replace", side_effect=OSError("disk full")),
                self.assertLogs(
                    "idat_sync.infrastructure.credential_store", level="ERROR"
                ),
                self.assertRaises(CredentialStoreError),
            ):
                store.save(updated)

            assert store.load() == original
            assert not part_path.exists()


class TestLoginFrame(unittest.TestCase):
    """Tests for pure-Python LoginFrame validation, inline MFA, and login."""

    def _create_app(self):
        class StubApp:
            """Minimal app stub for LoginFrame tests."""

            def __init__(self) -> None:
                self.switched_to_sync = 0
                self.push_count = 0

            def switch_to_sync(self) -> None:
                """Count transitions to SyncFrame."""
                self.switched_to_sync += 1

            def push_state(self) -> None:
                """Count state pushes."""
                self.push_count += 1

        return StubApp()

    def test_login_button_disables_while_blank_or_logging_in_and_guards_submit(self):  # noqa: PLR0915  # pylint: disable=too-many-statements
        """Disable can_login when inputs blank or in-flight and guard submit."""
        expected_state: StorageStateData = {
            "cookies": [
                {"name": "FedAuth", "value": "cookie-v", "domain": ".sharepoint.com"}
            ]
        }

        class ControllableSharepointAuth:  # pylint: disable=too-few-public-methods
            """Controllable SharePoint auth fake for concurrency tests."""

            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []
                self.error: Exception | None = None
                self.started: asyncio.Event | None = None
                self.release: asyncio.Event | None = None

            async def fetch_storage_state(
                self, username: str, password: str, _get_mfa_code
            ) -> StorageStateData | None:
                """Simulate async SharePoint login with controllable gate."""
                self.calls.append((username, password))
                if self.started is not None:
                    self.started.set()
                if self.release is not None:
                    await self.release.wait()
                if self.error is not None:
                    raise self.error
                return expected_state

        moodle = FakeMoodleTokenPort(token="tok-ui")  # noqa: S106
        sharepoint = ControllableSharepointAuth()
        store = InMemoryCredentialStore()
        auth = AuthService(
            moodle_auth=moodle,  # type: ignore[arg-type]
            sharepoint_auth=sharepoint,  # type: ignore[arg-type]
            credential_store=store,
        )
        app = self._create_app()
        frame = LoginFrame(app, auth)  # type: ignore[arg-type]

        assert not frame.can_login
        frame.username = "   "
        frame.password = " secret "  # noqa: S105
        assert not frame.can_login
        frame.username = " jdoe@custom.edu "
        frame.password = ""
        assert not frame.can_login
        frame.password = " secret "  # noqa: S105
        assert frame.can_login

        async def exercise_do_login() -> None:  # pylint: disable=too-many-statements
            sharepoint.started = asyncio.Event()
            sharepoint.release = asyncio.Event()
            sharepoint.error = RemoteServiceError("sso boom")

            with self.assertLogs("idat_sync.ui.login_frame", level="ERROR") as log_cm:
                task = asyncio.create_task(frame.do_login())
                await sharepoint.started.wait()
                assert frame.is_login is True
                assert not frame.can_login

                await frame.do_login()
                assert len(sharepoint.calls) == 1

                sharepoint.release.set()
                await task

            assert any("sso boom" in msg for msg in log_cm.output)
            assert frame.error == "Remote service error"
            assert frame.can_login
            assert not app.switched_to_sync

            moodle.token = None
            sharepoint.error = None
            with self.assertLogs("idat_sync.ui.login_frame", level="ERROR") as auth_cm:
                await frame.do_login()
            assert any("empty token" in msg for msg in auth_cm.output)
            assert frame.error == "Sign-in failed — check your username and password"
            assert not app.switched_to_sync

            moodle.token = "tok123"  # noqa: S105
            store.save_result = False
            with self.assertLogs("idat_sync.ui.login_frame", level="ERROR") as store_cm:
                await frame.do_login()
            assert any("credentials file" in msg for msg in store_cm.output)
            assert frame.error == "Could not update stored credentials"
            assert not app.switched_to_sync
            store.save_result = True

            sharepoint.started = None
            sharepoint.release = None
            sharepoint.error = None
            await frame.do_login()

            assert not frame.error
            assert moodle.calls == [
                ("jdoe", " secret "),
                ("jdoe", " secret "),
                ("jdoe", " secret "),
                ("jdoe", " secret "),
            ]
            assert sharepoint.calls == [
                ("jdoe@custom.edu", " secret "),
                ("jdoe@custom.edu", " secret "),
                ("jdoe@custom.edu", " secret "),
                ("jdoe@custom.edu", " secret "),
            ]
            assert app.switched_to_sync == 1
            assert frame.can_login

            frame.username = "   "
            assert not frame.can_login
            await frame.do_login()
            assert len(moodle.calls) == 4
            assert not frame.can_login

        asyncio.run(exercise_do_login())

    def test_login_frame_surfaces_logout_credential_store_error_on_init(self):
        """Show summarized CredentialStoreError in error if logout fails during init."""
        mock_auth = MagicMock(spec=AuthService)
        mock_auth.logout.side_effect = CredentialStoreError(
            "Could not remove credentials file: locked"
        )
        app = self._create_app()
        frame = LoginFrame(app, mock_auth)  # type: ignore[arg-type]
        assert frame.error == "Could not update stored credentials"

    def test_credentials_expired_error_exported_without_vendor_imports(self):
        """Export IDATSyncError hierarchy cleanly without vendor imports."""
        base_cls = exceptions.IDATSyncError
        assert issubclass(base_cls, Exception)
        assert not issubclass(base_cls, RuntimeError)
        expected_names = (
            "IDATSyncError",
            "AuthenticationError",
            "CredentialsExpiredError",
            "CredentialStoreError",
            "RemoteServiceError",
            "FileSystemError",
        )
        for name in expected_names:
            exc_cls = getattr(exceptions, name)
            assert issubclass(exc_cls, base_cls)
            assert getattr(ports, name) is exc_cls
            assert name in exceptions.__all__
            assert name in ports.__all__

        for mod in (exceptions, ports):
            tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
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
            assert imported & set(FORBIDDEN_INNER_LAYER_IMPORTS) == set()

        for alias_name in ("ServiceName", "AuthFailureReason", "FileSystemOperation"):
            assert hasattr(exceptions, alias_name)
            assert getattr(ports, alias_name) == getattr(exceptions, alias_name)
            assert alias_name in exceptions.__all__
            assert alias_name in ports.__all__

    def test_exception_structured_metadata_defaults_and_keyword_args(self):
        """Verify default and explicit keyword-only attributes on IDATSyncError."""
        auth_default = AuthenticationError("bad creds")
        assert str(auth_default) == "bad creds"
        assert auth_default.reason == "credentials"
        auth_mfa = AuthenticationError("mfa", reason="mfa")
        assert auth_mfa.reason == "mfa"
        with self.assertRaises(TypeError):
            _ = AuthenticationError("msg", "mfa")  # type: ignore[misc]  # pylint: disable=too-many-function-args

        exp_default = CredentialsExpiredError("expired")
        assert str(exp_default) == "expired"
        assert exp_default.service is None
        exp_sp = CredentialsExpiredError("403", service="SharePoint")
        assert exp_sp.service == "SharePoint"

        fs_default = FileSystemError("scan failed")
        assert str(fs_default) == "scan failed"
        assert fs_default.operation == "access"
        fs_write = FileSystemError("write failed", operation="write")
        assert fs_write.operation == "write"

        rem_default = RemoteServiceError("boom")
        assert str(rem_default) == "boom"
        assert rem_default.service is None
        assert rem_default.status_code is None
        assert rem_default.is_network is False
        rem_full = RemoteServiceError(
            "http 500", service="Moodle", status_code=500, is_network=True
        )
        assert str(rem_full) == "http 500"
        assert rem_full.service == "Moodle"
        assert rem_full.status_code == 500
        assert rem_full.is_network is True

    def test_mfa_popup_return_and_dismiss_and_dead_ui_cleanup(self):
        """Verify inline async MFA future handling and dead UI symbol removal."""
        assert not hasattr(LoginFrame, "get_mfa_code_ui")
        assert not hasattr(AppFrame, "exit")
        assert "LoadingDialog" not in ui_pkg.__all__
        assert not hasattr(ui_pkg, "LoadingDialog")
        assert "Icons" not in ui_pkg.__all__
        assert not hasattr(ui_pkg, "Icons")
        ui_dir = Path(ui_pkg.__file__).resolve().parent
        assert not (ui_dir / "dialogs.py").exists()
        assert not (ui_dir / "icons.py").exists()
        assert not (ui_dir / "mfa_dialog.py").exists()

        app = self._create_app()
        auth = AuthService(
            moodle_auth=FakeMoodleTokenPort(),  # type: ignore[arg-type]
            sharepoint_auth=FakeSharepointAuthPort(),  # type: ignore[arg-type]
            credential_store=InMemoryCredentialStore(),
        )
        frame = LoginFrame(app, auth)  # type: ignore[arg-type]

        async def exercise_mfa() -> None:
            task = asyncio.create_task(frame.get_mfa_code())
            await asyncio.sleep(0)
            assert frame.mfa_open is True
            frame.submit_mfa("654321")
            assert await task == "654321"
            assert frame.mfa_open is False

            task2 = asyncio.create_task(frame.get_mfa_code())
            await asyncio.sleep(0)
            assert frame.mfa_open is True
            frame.cancel_mfa()
            assert not await task2
            assert frame.mfa_open is False

            task3 = asyncio.create_task(frame.get_mfa_code())
            await asyncio.sleep(0)
            assert frame.mfa_open is True
            task3.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task3
            assert frame.mfa_open is False

        asyncio.run(exercise_mfa())


class _SyncStubMaster:
    """Minimal app stub for pure-Python SyncFrame tests."""

    def __init__(self, window: object | None = None) -> None:
        self.window = window
        self.switched_to_login = 0
        self.push_count = 0
        self.progress_payloads: list[dict[str, object]] = []
        self.opened_paths: list[Path] = []
        self.open_error: OSError | None = None

    def switch_to_login(self) -> None:
        """Count transitions to LoginFrame."""
        self.switched_to_login += 1

    def push_state(self) -> None:
        """Count state pushes."""
        self.push_count += 1

    def push_progress(self, payload: dict[str, object]) -> None:
        """Record targeted file progress payloads."""
        self.progress_payloads.append(payload)

    def pick_folder(self) -> str:
        """Simulate a cancelled desktop dialog."""
        return ""

    def open_path(self, path: Path) -> None:
        """Record opened paths or raise a configured OSError."""
        if self.open_error is not None:
            raise self.open_error
        self.opened_paths.append(path)


class TestSyncFrame(unittest.TestCase):
    """Tests for pure-Python SyncFrame selection, sync progress, and cancellation."""

    def test_sync_frame_open_file_handles_ready_non_ready_missing_and_os_error(self):  # pylint: disable=too-many-locals,too-many-statements
        """Open existing ready files even mid-sync, ignore non-ready, surface errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            course_dir = Path(tmpdir) / "Course One"
            (course_dir / "S1").mkdir(parents=True)
            synced_file = course_dir / "S1" / "synced.pdf"
            synced_file.write_bytes(b"ok")
            completed_file = course_dir / "S1" / "done.pdf"
            completed_file.write_bytes(b"ok")
            missing_file = course_dir / "S1" / "gone.pdf"
            new_file = course_dir / "S1" / "new.pdf"

            master = _SyncStubMaster()
            config = SyncConfig(session_file=Path(tmpdir) / "session.json")
            creds: Credentials = {
                "token": "tok",
                "sharepoint_storage_state": {"cookies": [], "origins": []},
            }
            frame = SyncFrame(master, credentials=creds, config=config)  # type: ignore[arg-type]
            frame.course_tree.set_courses(
                [Course(id=1, name="Course One")], selected={1}
            )
            frame.course_tree.start_course(1, course_dir)
            frame.course_tree.add_file(1, synced_file, ContentSyncState.SYNCED)
            frame.course_tree.add_file(1, completed_file, ContentSyncState.NEW)
            frame.course_tree.add_file(1, missing_file, ContentSyncState.NEW)
            frame.course_tree.add_file(1, new_file, ContentSyncState.NEW)
            frame.course_tree.set_file_status(completed_file, RowStatus.COMPLETED)
            frame.course_tree.set_file_status(missing_file, RowStatus.COMPLETED)

            frame.is_syncing = True
            frame.status = "Downloading other.pdf..."

            # Synced and Completed files open even while is_syncing is True
            frame.open_file("file:1:S1/synced.pdf")
            frame.open_file("file:1:S1/done.pdf")
            assert master.opened_paths == [
                synced_file.resolve(),
                completed_file.resolve(),
            ]
            assert not frame.error
            assert frame.status == "Downloading other.pdf..."

            # Non-ready row is silently ignored
            pushes_before = master.push_count
            frame.open_file("file:1:S1/new.pdf")
            assert len(master.opened_paths) == 2
            assert not frame.error
            assert master.push_count == pushes_before

            # Missing file logs error, sets frame.error, preserves status & row states
            with self.assertLogs("idat_sync.ui.sync_frame", level="ERROR") as logs:
                frame.open_file("file:1:S1/gone.pdf")
            assert any("gone.pdf" in msg for msg in logs.output)
            assert frame.error == "Could not open file"
            assert frame.status == "Downloading other.pdf..."
            assert (
                frame.course_tree.resolve_openable_file("file:1:S1/gone.pdf")
                == missing_file.resolve()
            )

            # OSError from app.open_path logs error, sets frame.error, preserves status
            frame.error = ""
            master.open_error = OSError("no associated app")
            with self.assertLogs("idat_sync.ui.sync_frame", level="ERROR") as logs:
                frame.open_file("file:1:S1/synced.pdf")
            assert any("no associated app" in msg for msg in logs.output)
            assert frame.error == "Could not open file"
            assert frame.status == "Downloading other.pdf..."

    def test_sync_frame_treeview_selection_progress_and_error_recovery(self):  # noqa: PLR0915  # pylint: disable=too-many-statements
        """Verify SyncFrame course ordering, tree progress, selection, and logout."""
        assert not hasattr(app_module, "ProgressInterceptor")

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

            async def exercise() -> None:  # noqa: PLR0915  # pylint: disable=too-many-statements
                master = _SyncStubMaster()
                assert not hasattr(sync_frame_mod, "_TreeProgressListener")
                frame = SyncFrame(master, creds, config)  # type: ignore[arg-type]
                assert not hasattr(frame, "output_text")
                assert not hasattr(frame, "progress_queue")
                assert not hasattr(frame, "update_progress")
                assert not hasattr(frame, "exit")
                mock_sync = MagicMock(spec=IDATSync)
                mock_sync.aclose = AsyncMock()
                mock_sync.list_courses = AsyncMock(
                    return_value=[past_course, current_course]
                )
                attempts = 0

                async def fake_create_sync_service(_credentials, _cfg):
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise RemoteServiceError("network offline", is_network=True)
                    return mock_sync

                async def fake_sync_courses(root_folder, courses, *, refresh=False):
                    assert root_folder == tmpdir
                    assert courses == [current_course]
                    if refresh:
                        for c in courses:
                            c.sections.clear()
                    c_dir = Path(tmpdir) / current_course.name
                    synced_p = c_dir / "S1" / "already.pdf"
                    ok_p = c_dir / "S1" / "new.pdf"
                    err_p = c_dir / "S1" / "broken.pdf"
                    yield CourseSyncStarted(current_course, c_dir)
                    yield CourseFileDiscovered(
                        current_course, synced_p, ContentSyncState.SYNCED
                    )
                    yield CourseFileDiscovered(
                        current_course, ok_p, ContentSyncState.NEW
                    )
                    yield TaskDownloadStarted(ok_p)
                    yield TaskDownloadCompleted(ok_p)
                    yield CourseFileDiscovered(
                        current_course, err_p, ContentSyncState.OUTDATED
                    )
                    yield CourseDiscoveryCompleted(current_course, 1, 2)
                    yield TaskDownloadStarted(err_p)
                    yield TaskDownloadFailed(
                        err_p,
                        FileSystemError(
                            "Failed to write 'C:\\secret\\broken.pdf': disk full",
                            operation="write",
                        ),
                    )
                    yield CourseSyncCompleted(
                        current_course, RemoteServiceError("partial")
                    )

                mock_sync.sync_courses.side_effect = fake_sync_courses

                with patch(
                    "idat_sync.ui.sync_frame.create_sync_service",
                    side_effect=fake_create_sync_service,
                ) as mock_factory:
                    with self.assertLogs(
                        "idat_sync.ui.sync_frame", level="ERROR"
                    ) as load_cm:
                        await frame.load_idat_sync(creds)
                    assert len(load_cm.output) == 1
                    assert "network offline" in load_cm.output[0]
                    assert frame.idat_sync is None
                    assert not frame.is_syncing
                    assert not master.switched_to_login
                    assert creds_file.exists()
                    assert frame.status == "Sync error: Network connection error"
                    assert frame.error == (
                        "Network connection error. "
                        f"Check {config.log_file.name} for details."
                    )

                    frame.set_folder_path(tmpdir)
                    assert frame.can_sync is True
                    await frame.do_sync()
                    assert mock_factory.call_count == 2
                    assert not frame.is_syncing
                    assert frame.error == (
                        "1 file failed to download. "
                        f"Check {config.log_file.name} for details."
                    )

                    rows = frame.course_tree.to_dict()
                    assert len(rows) == 2
                    assert rows[0]["name"] == current_course.name
                    assert rows[0]["checked"] is True
                    assert rows[1]["name"] == past_course.name
                    assert rows[1]["checked"] is False
                    assert (rows[0]["status"], rows[0]["progress"]) == (
                        "Failed",
                        "66%",
                    )
                    folders = rows[0]["children"]
                    assert len(folders) == 1
                    assert folders[0]["type"] == "folder"
                    assert folders[0]["name"] == "S1"
                    child_rows = {
                        f["name"]: (f["status"], f["progress"])
                        for f in folders[0]["children"]
                    }
                    assert child_rows == {
                        "already.pdf": ("Synced", "100%"),
                        "new.pdf": ("Completed", "100%"),
                        "broken.pdf": ("Failed", "0%"),
                    }
                    assert (
                        frame.course_tree.activity_to_dict()["failed"][0]["error"]
                        == "Could not save file to disk"
                    )

                    frame.clear_selection()
                    assert frame.can_sync is True
                    assert frame.selected_course_ids == set()
                    frame.select_all()
                    assert frame.can_sync is True
                    assert all(r["checked"] for r in frame.course_tree.to_dict())
                    frame.select_in_progress()
                    rows = frame.course_tree.to_dict()
                    assert rows[0]["checked"] is True
                    assert rows[1]["checked"] is False
                    frame.toggle_course(current_course.id)
                    assert current_course.id not in frame.selected_course_ids
                    frame.toggle_course(current_course.id)
                    assert current_course.id in frame.selected_course_ids

                    async def fail_sync_courses(
                        _root_folder, _courses, *, refresh=False
                    ):
                        _ = refresh
                        raise CredentialsExpiredError("401 expired")
                        yield  # pragma: no cover

                    mock_sync.sync_courses.side_effect = fail_sync_courses
                    with self.assertNoLogs("idat_sync.ui.sync_frame", level="ERROR"):
                        await frame.do_sync()
                    assert not frame.is_syncing
                    assert not master.switched_to_login
                    assert creds_file.exists()
                    assert frame.status == "Credentials expired. Sign in again."
                    assert frame.error == "Credentials expired — sign in again"
                    assert config.log_file.name not in frame.error

                    current_course.sections = [CourseSection(id=1, name="Cached")]
                    past_course.sections = [CourseSection(id=2, name="Kept")]
                    mock_sync.sync_courses.side_effect = fake_sync_courses
                    await frame.do_sync()
                    assert not current_course.sections
                    assert len(past_course.sections) == 1
                    assert mock_factory.call_count == 2

                    frame.is_syncing = True
                    await frame.do_logout()
                    mock_sync.aclose.assert_not_awaited()
                    assert not master.switched_to_login

                    frame.is_syncing = False
                    await frame.do_logout()
                    mock_sync.aclose.assert_awaited_once_with()
                    assert frame.idat_sync is None
                    assert master.switched_to_login == 1

            asyncio.run(exercise())

    def test_sync_frame_browse_folder_desktop_and_android_fallback(self):
        """Use MainApp desktop folder dialog when available, else Android Downloads."""
        creds: Credentials = {
            "token": "t",
            "sharepoint_storage_state": {"cookies": []},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            config = SyncConfig(session_file=Path(tmpdir) / "missing_credentials.json")

            mock_window = MagicMock()
            mock_window.create_file_dialog.return_value = ("/chosen/sync/dir",)
            fake_webview = types.SimpleNamespace(
                FileDialog=types.SimpleNamespace(FOLDER=20)
            )
            desktop_app = MainApp(config=config, window=mock_window)
            frame = SyncFrame(desktop_app, creds, config)
            with (
                patch.dict("os.environ", {}, clear=False),
                patch.dict(sys.modules, {"webview": fake_webview}),
            ):
                assert frame.browse_folder() == "/chosen/sync/dir"
            assert frame.folder_path == "/chosen/sync/dir"

            mock_window.create_file_dialog.return_value = None
            with (
                patch.dict("os.environ", {}, clear=False),
                patch.dict(sys.modules, {"webview": fake_webview}),
            ):
                assert frame.browse_folder() == "/chosen/sync/dir"
            assert frame.folder_path == "/chosen/sync/dir"

            android_frame = SyncFrame(
                MainApp(config=config, window=None), creds, config
            )
            with patch.dict(
                "os.environ",
                {"ANDROID_ARGUMENT": "1", "EXTERNAL_STORAGE": "/sdcard"},
                clear=False,
            ):
                expected = str(Path("/sdcard") / "Download" / "idat-sync")
                assert android_frame.browse_folder() == expected
            assert android_frame.folder_path == expected

    def test_sync_frame_closes_partial_service_and_logout_switches_on_aclose_error(
        self,
    ):
        """Close partial service on list_courses failure and still switch on logout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            creds_file = Path(tmpdir) / "session.json"
            creds_file.write_text('{"token":"t"}', encoding="utf-8")
            creds: Credentials = {
                "token": "t",
                "sharepoint_storage_state": {"cookies": []},
            }
            config = SyncConfig(session_file=creds_file, request_delay=0.0)

            async def exercise() -> None:
                master = _SyncStubMaster()
                frame = SyncFrame(master, creds, config)  # type: ignore[arg-type]
                partial_sync = MagicMock(spec=IDATSync)
                partial_sync.aclose = AsyncMock()
                partial_sync.list_courses = AsyncMock(
                    side_effect=RemoteServiceError("list failed")
                )

                with (
                    patch(
                        "idat_sync.ui.sync_frame.create_sync_service",
                        new=AsyncMock(return_value=partial_sync),
                    ),
                    self.assertLogs(
                        "idat_sync.ui.sync_frame", level="ERROR"
                    ) as list_cm,
                ):
                    await frame.load_idat_sync(creds)

                assert len(list_cm.output) == 1
                assert "list failed" in list_cm.output[0]
                partial_sync.aclose.assert_awaited_once_with()
                assert frame.idat_sync is None
                assert frame.status == "Sync error: Remote service error"
                assert frame.error == (
                    f"Remote service error. Check {config.log_file.name} for details."
                )

                with (
                    patch(
                        "idat_sync.ui.sync_frame.create_sync_service",
                        new=AsyncMock(return_value=None),
                    ),
                    self.assertLogs(
                        "idat_sync.ui.sync_frame", level="ERROR"
                    ) as init_cm,
                ):
                    await frame.load_idat_sync(creds)
                assert len(init_cm.output) == 1
                assert "Sync service failed to initialize" in init_cm.output[0]
                assert frame.status == "Sync error: Unexpected sync error"
                assert frame.error == (
                    f"Unexpected sync error. Check {config.log_file.name} for details."
                )

                failing_sync = MagicMock(spec=IDATSync)
                failing_sync.aclose = AsyncMock(side_effect=RuntimeError("close boom"))
                frame.idat_sync = failing_sync
                with self.assertRaisesRegex(RuntimeError, "close boom"):
                    await frame.do_logout()
                assert frame.idat_sync is None
                assert master.switched_to_login == 1

            asyncio.run(exercise())

    def test_ui_app_main_configures_logging_without_stdio_hijack(self):
        """Verify main.main configures logging without replacing stdio."""
        orig_stdout = sys.stdout
        orig_stderr = sys.stderr
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_root = Path(tmpdir) / "app_data"
            cfg = SyncConfig(log_file=Path(tmpdir) / "app.log")
            with (
                patch(
                    "idat_sync.main.user_data_path", return_value=fake_root
                ) as mock_user_data_path,
                patch("idat_sync.main.load_config", return_value=cfg) as mock_load_cfg,
                patch("idat_sync.main.configure_logging") as mock_cfg_log,
                patch("idat_sync.main.MainApp") as mock_app,
            ):
                main_module.main()

            mock_user_data_path.assert_called_once_with("idat-sync", appauthor=False)
            mock_load_cfg.assert_called_once_with(fake_root)
            mock_cfg_log.assert_called_once_with(cfg.log_file)
            mock_app.assert_called_once_with(config=cfg)
            mock_app.return_value.run.assert_called_once_with()
            assert sys.stdout is orig_stdout
            assert sys.stderr is orig_stderr

    def test_app_frame_aclose_and_main_app_on_close_closes_frame_before_destroy(self):
        """Close active AppFrame before destroying MainApp window idempotently."""

        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                cfg = SyncConfig(session_file=Path(tmpdir) / "missing_session.json")
                mock_window = MagicMock()
                app = MainApp(config=cfg, window=mock_window)
                try:
                    base_frame = AppFrame()
                    await base_frame.aclose()

                    active_frame = MagicMock()
                    active_frame.aclose = AsyncMock(
                        side_effect=RuntimeError("close error")
                    )
                    app._frame = active_frame  # noqa: SLF001  # type: ignore[attr-defined]  # pylint: disable=protected-access

                    with self.assertRaisesRegex(RuntimeError, "close error"):
                        await app.on_close()
                    await app.on_close()

                    active_frame.aclose.assert_awaited_once_with()
                    mock_window.destroy.assert_called_once_with()
                finally:
                    app.loop.close()

        asyncio.run(exercise())

    def test_main_app_initial_frame_switching_and_state_push(self):  # pylint: disable=too-many-statements
        """Select initial frame from credentials, switch views, and push JSON state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            creds_path = Path(tmpdir) / "credentials.json"
            cfg = SyncConfig(session_file=creds_path)
            mock_window = MagicMock()
            app = MainApp(config=cfg, window=mock_window)
            try:
                assert isinstance(app.active_frame, LoginFrame)
                assert app.get_state()["view"] == "login"

                mock_window.evaluate_js.reset_mock()
                app.push_state()
                mock_window.evaluate_js.assert_called_once()
                js_call = mock_window.evaluate_js.call_args.args[0]
                assert js_call.startswith("window.__onPythonState(")
                assert js_call.endswith(")")
                payload = json.loads(
                    js_call.removeprefix("window.__onPythonState(").removesuffix(")")
                )
                assert payload["view"] == "login"

                mock_window.evaluate_js.reset_mock()
                prog_payload = {
                    "id": "file:1:a.pdf",
                    "downloaded": 512,
                    "total": 1024,
                    "percent": 50,
                    "speed_bps": 1024.0,
                }
                app.push_progress(prog_payload)
                mock_window.evaluate_js.assert_called_once()
                prog_call = mock_window.evaluate_js.call_args.args[0]
                assert prog_call.startswith("window.__onFileProgress(")
                assert prog_call.endswith(")")
                assert (
                    json.loads(
                        prog_call.removeprefix("window.__onFileProgress(").removesuffix(
                            ")"
                        )
                    )
                    == prog_payload
                )

                app.auth.credential_store.save(
                    {
                        "token": "tok-saved",
                        "sharepoint_storage_state": {"cookies": []},
                    }
                )
                app.switch_to_sync()
                assert isinstance(app.active_frame, SyncFrame)
                assert app.get_state()["view"] == "sync"

                app.switch_to_login()
                assert isinstance(app.active_frame, LoginFrame)
                assert app.get_state()["view"] == "login"
            finally:
                app.loop.close()

            # Authenticated startup lands directly on SyncFrame
            JsonCredentialStore(credentials_file=creds_path).save(
                {
                    "token": "tok-saved",
                    "sharepoint_storage_state": {"cookies": []},
                }
            )
            app_authed = MainApp(config=cfg, window=MagicMock())
            try:
                assert isinstance(app_authed.active_frame, SyncFrame)
                assert app_authed.get_state()["view"] == "sync"
            finally:
                app_authed.loop.close()

    def test_main_app_on_close_cancels_active_login_and_mfa(self):
        """Verify on_close cancels active login task and dismisses open MFA prompt."""

        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                creds_path = Path(tmpdir) / "credentials.json"
                cfg = SyncConfig(session_file=creds_path)
                mock_window = MagicMock()
                real_auth = AuthService(
                    moodle_auth=FakeMoodleTokenPort("tok"),
                    sharepoint_auth=FakeSharepointAuthPort({"cookies": []}),
                    credential_store=JsonCredentialStore(credentials_file=creds_path),
                )
                with patch(
                    "idat_sync.ui.app.create_auth_service", return_value=real_auth
                ):
                    app = MainApp(config=cfg, window=mock_window)
                try:
                    frame = app.active_frame
                    assert isinstance(frame, LoginFrame)
                    frame.username = "jdoe"
                    frame.password = "secret"  # noqa: S105

                    login_task = asyncio.create_task(frame.do_login())
                    for _ in range(50):
                        if frame.mfa_open:
                            break
                        await asyncio.sleep(0.01)
                    assert frame.mfa_open

                    await app.on_close()
                    assert not frame.mfa_open
                    mock_window.destroy.assert_called_once_with()
                    with self.assertRaises(asyncio.CancelledError):
                        await login_task
                finally:
                    app.loop.close()

        asyncio.run(exercise())

    def test_login_frame_switch_to_sync_succeeds(self):
        """Verify successful do_login transitions cleanly to SyncFrame."""

        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                creds_path = Path(tmpdir) / "credentials.json"
                cfg = SyncConfig(session_file=creds_path)
                real_auth = AuthService(
                    moodle_auth=FakeMoodleTokenPort("tok"),
                    sharepoint_auth=FakeSharepointAuthPort({"cookies": []}),
                    credential_store=JsonCredentialStore(credentials_file=creds_path),
                )
                with patch(
                    "idat_sync.ui.app.create_auth_service", return_value=real_auth
                ):
                    app = MainApp(config=cfg, window=MagicMock())
                try:
                    frame = app.active_frame
                    assert isinstance(frame, LoginFrame)
                    frame.username = "jdoe"
                    frame.password = "secret"  # noqa: S105

                    login_task = asyncio.create_task(frame.do_login())
                    for _ in range(50):
                        if frame.mfa_open:
                            break
                        await asyncio.sleep(0.01)
                    assert frame.mfa_open
                    frame.submit_mfa("123456")
                    await login_task

                    assert isinstance(app.active_frame, SyncFrame)
                finally:
                    app.loop.close()

        asyncio.run(exercise())

    def test_main_app_run_starts_webview_with_bundled_html_and_ua(self):  # pylint: disable=too-many-locals
        """Load bundled index.html via importlib.resources and start webview."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = SyncConfig(session_file=Path(tmpdir) / "missing_credentials.json")
            app = MainApp(config=cfg)
            expected_html = (
                importlib.resources.files("idat_sync.ui")
                .joinpath("index.html")
                .read_text(encoding="utf-8")
            )
            closing_handlers = []
            fake_window = MagicMock()
            fake_window.events.closing.__iadd__.side_effect = lambda fn: (
                closing_handlers.append(fn) or fake_window.events.closing
            )
            loop_was_running = False

            def fake_start(**kwargs):
                nonlocal loop_was_running
                loop_was_running = app.loop.is_running()
                assert kwargs.get("user_agent") == WEBVIEW_USER_AGENT
                assert len(closing_handlers) == 1
                # Triggering closing event closes frame without re-entrant destroy().
                closing_handlers[0]()
                fake_window.destroy.assert_not_called()

            fake_webview = MagicMock()
            fake_webview.create_window.return_value = fake_window
            fake_webview.start.side_effect = fake_start

            with patch.dict(sys.modules, {"webview": fake_webview}):
                app.run()

            assert loop_was_running
            assert app.loop.is_closed()
            fake_webview.create_window.assert_called_once()
            create_kwargs = fake_webview.create_window.call_args.kwargs
            assert create_kwargs["html"] == expected_html
            assert create_kwargs["width"] == 1100
            assert create_kwargs["height"] == 720
            assert create_kwargs["min_size"] == (400, 500)
            js_api = create_kwargs["js_api"]
            assert js_api.get_state()["view"] == "login"
            assert not hasattr(js_api, "set_folder_path")
            for method_name in (
                "toggle_term",
                "sync_course",
                "cancel_sync",
                "retry_load",
            ):
                assert callable(getattr(js_api, method_name))
            fake_webview.start.assert_called_once_with(user_agent=WEBVIEW_USER_AGENT)

    def test_bundled_html_is_non_empty_and_has_no_external_cdn_links(self):
        """Assert bundled index.html is self-contained without Google Fonts CDN."""
        html = app_module.load_ui_html()
        assert html.strip()
        assert "fonts.googleapis.com" not in html
        assert "__onPythonState" in html
        assert "__onFileProgress" in html

    def test_bundled_html_layout_and_responsive_panels(self):
        """Assert full-width bars, zero .selbar/.ctog, and toggleable panels."""
        html = app_module.load_ui_html()
        assert ".selbar" not in html
        assert "selbar" not in html
        assert ".ctog" not in html
        assert "ctog" not in html
        assert (
            html.count("grid-column:1/-1") >= 2
            or html.count("grid-column: 1 / -1") >= 2
            or (
                ".pathbar{grid-area:1/1/auto/-1}" in html
                and "grid-area:3/1/auto/-1" in html
            )
        )
        assert ".sbt>b{" in html or ".sbt > b {" in html
        assert "text-overflow:ellipsis" in html
        assert ".sbt>span{" in html or ".sbt > span {" in html
        assert ".chd{" in html
        assert "max-width:639px" in html or "max-width: 639px" in html
        assert "Active term" in html
        assert "Clear" in html
        assert "Courses by term" in html
        assert "Toggle courses" in html
        assert "Toggle activity" in html
        assert "min-width: 960px" in html or "min-width:960px" in html
        assert (
            "grid-template-rows:auto minmax(0,1fr) auto" in html
            or "grid-template-rows: auto minmax(0, 1fr) auto" in html
            or "grid-template:auto minmax(0,1fr) auto/" in html
        )
        assert ".mni{" in html or ".mni {" in html

    def test_sync_frame_on_close_cancels_load_and_sync_tasks(self):  # noqa: PLR0915  # pylint: disable=too-many-statements
        """Cancel active load_idat_sync and do_sync tasks on SyncFrame.aclose."""
        creds: Credentials = {
            "token": "tok",
            "sharepoint_storage_state": {"cookies": []},
        }

        async def exercise_load_cancel() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                cfg = SyncConfig(session_file=Path(tmpdir) / "session.json")
                master = _SyncStubMaster()
                frame = SyncFrame(master, creds, cfg)  # type: ignore[arg-type]
                load_started = asyncio.Event()
                load_cancelled = asyncio.Event()

                async def slow_create_sync_service(_c, _conf):
                    load_started.set()
                    try:
                        await asyncio.Future()
                    except asyncio.CancelledError:
                        load_cancelled.set()
                        raise

                with patch(
                    "idat_sync.ui.sync_frame.create_sync_service",
                    side_effect=slow_create_sync_service,
                ):
                    load_task = asyncio.create_task(frame.load_idat_sync(creds))
                    await asyncio.wait_for(load_started.wait(), timeout=2.0)
                    await frame.aclose()
                    assert load_cancelled.is_set()
                    with self.assertRaises(asyncio.CancelledError):
                        await load_task

        async def exercise_sync_cancel() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                cfg = SyncConfig(session_file=Path(tmpdir) / "session.json")
                master = _SyncStubMaster()
                frame = SyncFrame(master, creds, cfg)  # type: ignore[arg-type]
                frame._populate_courses([Course(id=1, name="Course 1")])  # noqa: SLF001  # pylint: disable=protected-access
                frame.selected_course_ids = {1}
                frame.set_folder_path(tmpdir)

                sync_started = asyncio.Event()
                sync_cancelled = asyncio.Event()

                async def slow_sync_courses(_path, _selected, *, refresh=False):
                    _ = refresh
                    sync_started.set()
                    try:
                        await asyncio.Future()
                    except asyncio.CancelledError:
                        sync_cancelled.set()
                        raise
                    yield  # pragma: no cover

                mock_service = MagicMock(spec=IDATSync)
                mock_service.sync_courses = slow_sync_courses
                mock_service.aclose = AsyncMock()
                frame.idat_sync = mock_service

                sync_task = asyncio.create_task(frame.do_sync())
                await asyncio.wait_for(sync_started.wait(), timeout=2.0)
                await frame.aclose()
                assert sync_cancelled.is_set()
                mock_service.aclose.assert_awaited_once_with()
                with self.assertRaises(asyncio.CancelledError):
                    await sync_task

        asyncio.run(exercise_load_cancel())
        asyncio.run(exercise_sync_cancel())

    def test_sync_frame_reports_sync_completion_status(self):
        """Set status to 'Sync completed with errors.' or 'Sync done.'."""
        creds: Credentials = {
            "token": "tok",
            "sharepoint_storage_state": {"cookies": []},
        }

        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                cfg = SyncConfig(session_file=Path(tmpdir) / "session.json")
                master = _SyncStubMaster()
                frame = SyncFrame(master, creds, cfg)  # type: ignore[arg-type]
                c1 = Course(id=1, name="Course 1")
                frame._populate_courses([c1])  # noqa: SLF001  # pylint: disable=protected-access
                frame.selected_course_ids = {1}
                frame.set_folder_path(tmpdir)

                async def err_sync_courses(_path, _selected, *, refresh=False):
                    _ = refresh
                    yield CourseSyncCompleted(c1, RemoteServiceError("boom"))

                mock_service = MagicMock(spec=IDATSync)
                mock_service.sync_courses = err_sync_courses
                frame.idat_sync = mock_service

                await frame.do_sync()
                assert frame.status == "Sync completed with errors."
                assert frame.error == (
                    f"Remote service error. Check {cfg.log_file.name} for details."
                )

                async def ok_sync_courses(_path, _selected, *, refresh=False):
                    _ = refresh
                    yield CourseSyncCompleted(c1, None)

                mock_service.sync_courses = ok_sync_courses
                await frame.do_sync()
                assert frame.status == "Sync done."
                assert not frame.error

        asyncio.run(exercise())

    def test_sync_frame_reports_multi_file_download_failures(self):
        """Summarize per-file errors and show plural failed count on SyncBar."""
        creds: Credentials = {
            "token": "tok",
            "sharepoint_storage_state": {"cookies": []},
        }

        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                cfg = SyncConfig(session_file=Path(tmpdir) / "session.json")
                master = _SyncStubMaster()
                frame = SyncFrame(master, creds, cfg)  # type: ignore[arg-type]
                c1 = Course(id=1, name="Course 1")
                frame._populate_courses([c1])  # noqa: SLF001  # pylint: disable=protected-access
                frame.selected_course_ids = {1}
                frame.set_folder_path(tmpdir)

                async def multi_file_err_sync_courses(
                    _path, _selected, *, refresh=False
                ):
                    _ = refresh
                    c_dir = Path(tmpdir) / c1.name
                    p1 = c_dir / "S1" / "a.pdf"
                    p2 = c_dir / "S1" / "b.pdf"
                    yield CourseSyncStarted(c1, c_dir)
                    yield CourseFileDiscovered(c1, p1, ContentSyncState.NEW)
                    yield CourseFileDiscovered(c1, p2, ContentSyncState.NEW)
                    yield CourseDiscoveryCompleted(c1, 0, 2)
                    yield TaskDownloadFailed(
                        p1,
                        FileSystemError(
                            "Failed to write 'C:\\a.pdf': disk full", operation="write"
                        ),
                    )
                    yield TaskDownloadFailed(
                        p2,
                        RemoteServiceError(
                            "HTTP 500 from Moodle web service",
                            service="Moodle",
                            status_code=500,
                        ),
                    )
                    yield CourseSyncCompleted(c1, RemoteServiceError("partial"))

                mock_service = MagicMock(spec=IDATSync)
                mock_service.sync_courses = multi_file_err_sync_courses
                frame.idat_sync = mock_service

                await frame.do_sync()
                assert frame.status == "Sync completed with errors."
                assert frame.error == (
                    "2 files failed to download. "
                    f"Check {cfg.log_file.name} for details."
                )
                failed = frame.course_tree.activity_to_dict()["failed"]
                assert [f["error"] for f in failed] == [
                    "Could not save file to disk",
                    "Moodle error (HTTP 500)",
                ]

        asyncio.run(exercise())

    def test_sync_frame_cancel_sync_ignores_load_idat_sync(self):
        """Do not cancel load_idat_sync when cancel_sync is invoked during loading."""
        creds: Credentials = {
            "token": "tok",
            "sharepoint_storage_state": {"cookies": []},
        }

        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                cfg = SyncConfig(session_file=Path(tmpdir) / "session.json")
                master = _SyncStubMaster()
                frame = SyncFrame(master, creds, cfg)  # type: ignore[arg-type]
                loading = asyncio.Event()
                release = asyncio.Event()
                c1 = Course(id=1, name="Course 1")

                mock_service = MagicMock(spec=IDATSync)
                mock_service.aclose = AsyncMock()

                async def slow_list_courses() -> list[Course]:
                    loading.set()
                    await release.wait()
                    return [c1]

                mock_service.list_courses = AsyncMock(side_effect=slow_list_courses)

                with patch(
                    "idat_sync.ui.sync_frame.create_sync_service",
                    new_callable=AsyncMock,
                    return_value=mock_service,
                ):
                    load_task = asyncio.create_task(frame.load_idat_sync(creds))
                    await asyncio.wait_for(loading.wait(), timeout=2.0)
                    frame.cancel_sync()
                    release.set()
                    await load_task

                assert frame.courses_loaded is True
                assert frame.idat_sync is mock_service
                assert not frame.is_syncing
                mock_service.aclose.assert_not_awaited()

        asyncio.run(exercise())

    def test_sync_frame_term_toggle_single_course_progress_cancel_and_retry(self):  # noqa: PLR0915  # pylint: disable=too-many-statements
        """Verify toggle_term, sync_course, fallback, push_progress, cancel, retry."""
        creds: Credentials = {
            "token": "tok",
            "sharepoint_storage_state": {"cookies": []},
        }
        c_old_a = Course(id=1, name="A", category="I.01.2025-IIA")
        c_old_b = Course(id=2, name="B", category="I.03.2025-IIA")
        c_cur = Course(id=3, name="C", category="IV.01.2026-III")

        async def exercise() -> None:  # noqa: PLR0915  # pylint: disable=too-many-locals,too-many-statements
            with tempfile.TemporaryDirectory() as tmpdir:
                cfg = SyncConfig(session_file=Path(tmpdir) / "session.json")
                master = _SyncStubMaster()
                frame = SyncFrame(master, creds, cfg)  # type: ignore[arg-type]

                mock_service = MagicMock(spec=IDATSync)
                mock_service.aclose = AsyncMock()
                mock_service.list_courses = AsyncMock(
                    return_value=[c_old_a, c_old_b, c_cur]
                )

                # 1. Startup error -> retry_load recovers
                attempts = 0

                async def flaky_factory(_c, _cfg):
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise CredentialsExpiredError("expired token")
                    return mock_service

                with patch(
                    "idat_sync.ui.sync_frame.create_sync_service",
                    side_effect=flaky_factory,
                ):
                    await frame.load_idat_sync(creds)
                    state = frame.to_dict()
                    assert state["courses_loaded"] is False
                    assert state["auth_expired"] is True
                    assert state["error"] == "Credentials expired — sign in again"
                    assert cfg.log_file.name not in state["error"]

                    await frame.retry_load()
                    state = frame.to_dict()
                    assert state["courses_loaded"] is True
                    assert state["auth_expired"] is False
                    assert not state["error"]
                    assert [c["id"] for c in state["courses"]] == [3, 1, 2]
                    assert state["courses"][0]["term"] == "2026-III"
                    assert state["courses"][1]["term"] == "2025-IIA"
                    assert state["courses"][2]["term"] == "2025-IIA"

                # 2. toggle_term tri-state
                frame.toggle_term("2025-IIA")
                assert {1, 2}.issubset(frame.selected_course_ids)
                frame.toggle_term("2025-IIA")
                assert not ({1, 2} & frame.selected_course_ids)

                # 3. do_sync fallback when selection is empty -> in_progress_ids
                frame.set_folder_path(tmpdir)
                frame.clear_selection()
                synced_course_batches: list[list[int]] = []

                async def prog_sync_courses(_root, courses, *, refresh=False):
                    _ = refresh
                    synced_course_batches.append([c.id for c in courses])
                    c = courses[0]
                    c_dir = Path(tmpdir) / c.name
                    p1 = c_dir / "S1" / "f1.pdf"
                    yield CourseSyncStarted(c, c_dir)
                    yield CourseFileDiscovered(c, p1, ContentSyncState.NEW)
                    yield CourseDiscoveryCompleted(c, 0, 1)
                    yield TaskDownloadStarted(p1)
                    push_before = master.push_count
                    yield TaskDownloadProgress(p1, 500, 1000)
                    yield TaskDownloadProgress(p1, 1000, 1000)
                    assert master.push_count == push_before
                    yield TaskDownloadCompleted(p1)
                    yield CourseSyncCompleted(c, None)

                mock_service.sync_courses.side_effect = prog_sync_courses
                await frame.do_sync()
                assert synced_course_batches == [[3]]
                assert [p["percent"] for p in master.progress_payloads] == [50, 99]

                # 4. sync_course runs single course even after prior completion
                await frame.sync_course(1)
                assert synced_course_batches == [[3], [1]]

                # 5. cancel_sync cancels active sync without closing idat_sync
                dl_started = asyncio.Event()

                async def cancellable_sync(_root, courses, *, refresh=False):
                    _ = refresh
                    c = courses[0]
                    c_dir = Path(tmpdir) / c.name
                    p_done = c_dir / "S1" / "done.pdf"
                    p_dl = c_dir / "S1" / "active.pdf"
                    p_wait = c_dir / "S1" / "wait.pdf"
                    yield CourseSyncStarted(c, c_dir)
                    yield CourseFileDiscovered(c, p_done, ContentSyncState.NEW)
                    yield CourseFileDiscovered(c, p_dl, ContentSyncState.OUTDATED)
                    yield CourseFileDiscovered(c, p_wait, ContentSyncState.NEW)
                    yield CourseDiscoveryCompleted(c, 0, 3)
                    yield TaskDownloadStarted(p_done)
                    yield TaskDownloadCompleted(p_done)
                    yield TaskDownloadStarted(p_dl)
                    yield TaskDownloadProgress(p_dl, 250, 1000)
                    dl_started.set()
                    await asyncio.Future()

                mock_service.sync_courses.side_effect = cancellable_sync
                sync_task = asyncio.create_task(frame.sync_course(3))
                await asyncio.wait_for(dl_started.wait(), timeout=2.0)
                frame.cancel_sync()
                await sync_task

                assert frame.idat_sync is mock_service
                mock_service.aclose.assert_not_awaited()
                assert frame.is_syncing is False
                assert frame.status == "Sync cancelled."
                c3_row = frame.to_dict()["courses"][0]
                statuses = {
                    f["name"]: (f["status"], f["percent"])
                    for f in c3_row["children"][0]["children"]
                }
                assert statuses == {
                    "done.pdf": ("Completed", 100),
                    "active.pdf": ("Outdated", 0),
                    "wait.pdf": ("New", 0),
                }

        asyncio.run(exercise())

    def test_sync_frame_resets_activity_and_status_when_syncing_new_course(self):  # pylint: disable=too-many-statements
        """Reset activity and status immediately when syncing a new course."""
        creds: Credentials = {
            "token": "tok",
            "sharepoint_storage_state": {"cookies": []},
        }
        c1, c2 = (
            Course(id=1, name="Course One", category="IV.01.2026-III"),
            Course(id=2, name="Course Two", category="IV.01.2026-III"),
        )

        async def exercise() -> None:  # pylint: disable=too-many-statements
            with tempfile.TemporaryDirectory() as tmpdir:
                cfg = SyncConfig(session_file=Path(tmpdir) / "session.json")
                snapshots: list[dict[str, object]] = []
                master = _SyncStubMaster()
                frame = SyncFrame(master, creds, cfg)  # type: ignore[arg-type]
                master.push_state = lambda: snapshots.append(frame.to_dict())  # type: ignore[method-assign]
                frame._populate_courses([c1, c2])  # noqa: SLF001  # pylint: disable=protected-access
                frame.set_folder_path(tmpdir)

                async def first_course_sync(_root, courses, *, refresh=False):
                    _ = refresh
                    c = courses[0]
                    c_dir = Path(tmpdir) / c.name
                    p_done, p_fail = c_dir / "S1" / "done.pdf", c_dir / "S1" / "bad.pdf"
                    yield CourseSyncStarted(c, c_dir)
                    yield CourseFileDiscovered(
                        c, c_dir / "S1" / "already.pdf", ContentSyncState.SYNCED
                    )
                    yield CourseFileDiscovered(c, p_done, ContentSyncState.NEW)
                    yield CourseFileDiscovered(c, p_fail, ContentSyncState.NEW)
                    yield CourseDiscoveryCompleted(c, 1, 2)
                    yield TaskDownloadStarted(p_done)
                    yield TaskDownloadCompleted(p_done)
                    yield TaskDownloadFailed(
                        p_fail,
                        RemoteServiceError(
                            "http 500", service="Moodle", status_code=500
                        ),
                    )
                    yield CourseSyncCompleted(c, RemoteServiceError("partial"))

                mock_service = MagicMock(spec=IDATSync)
                mock_service.sync_courses.side_effect = first_course_sync
                frame.idat_sync = mock_service
                await frame.sync_course(1)
                assert frame.to_dict()["activity"]["total_pending"] == 2

                snapshots.clear()
                during_fetch: dict[str, object] = {}

                async def second_course_sync(_root, courses, *, refresh=False):
                    _ = refresh
                    c = courses[0]
                    yield CourseSyncStarted(c, Path(tmpdir) / c.name)
                    during_fetch.update(frame.to_dict()["activity"])
                    yield CourseSyncCompleted(
                        c,
                        RemoteServiceError(
                            "moodle timeout", service="Moodle", is_network=True
                        ),
                    )

                mock_service.sync_courses.side_effect = second_course_sync
                await frame.sync_course(2)

                empty_activity = {
                    "total_pending": 0,
                    "completed": 0,
                    "skipped": 0,
                    "failed_count": 0,
                    "percent": 0,
                    "downloading": [],
                    "queued": [],
                    "finished": [],
                    "failed": [],
                }
                assert snapshots[0]["is_syncing"] is True
                assert snapshots[0]["status"] != "Sync completed with errors."
                assert snapshots[0]["activity"] == empty_activity
                assert during_fetch == empty_activity
                assert frame.error == (
                    f"Moodle connection error. Check {cfg.log_file.name} for details."
                )
                c1_row = next(r for r in frame.to_dict()["courses"] if r["id"] == 1)
                assert len(c1_row["children"]) == 1

        asyncio.run(exercise())


class TestAppFrameBusy(unittest.TestCase):
    """Tests for pure-Python AppFrame._busy and aclose lifecycle."""

    def test_busy_sets_active_task_resets_flag_and_aclose_cancels(self):
        """Track _active_task, reset flag on exit/error, and cancel in aclose."""
        assert AppFrame.__bases__ == (object,)
        assert not hasattr(AppFrame, "exit")

        async def exercise() -> None:
            frame = AppFrame()
            flags: list[bool] = []

            async with frame._busy(flags.append):  # noqa: SLF001  # pylint: disable=protected-access
                assert flags == [True]
                assert frame._active_task is asyncio.current_task()  # noqa: SLF001  # pylint: disable=protected-access

            assert flags == [True, False]
            assert frame._active_task is None  # noqa: SLF001  # pylint: disable=protected-access

            async def fail_while_busy() -> None:
                async with frame._busy(flags.append):  # noqa: SLF001  # pylint: disable=protected-access
                    raise RuntimeError("boom")

            with self.assertRaisesRegex(RuntimeError, "boom"):
                await fail_while_busy()

            assert flags == [True, False, True, False]
            assert frame._active_task is None  # noqa: SLF001  # pylint: disable=protected-access

            started = asyncio.Event()
            cancelled = asyncio.Event()

            async def long_work() -> None:
                async with frame._busy(flags.append):  # noqa: SLF001  # pylint: disable=protected-access
                    started.set()
                    try:
                        await asyncio.Future()
                    except asyncio.CancelledError:
                        cancelled.set()
                        raise

            task = asyncio.create_task(long_work())
            await started.wait()
            await frame.aclose()
            assert cancelled.is_set()
            assert frame._active_task is None  # noqa: SLF001  # pylint: disable=protected-access
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(exercise())


class TestCourseTree(unittest.TestCase):
    """Tests for pure-Python CourseTree selection, streaming discovery, and progress."""

    def test_course_tree_lifecycle_and_progress(self):  # pylint: disable=too-many-statements
        """Manage course rows, nested folders, streaming discovery, and to_dict."""
        ct = CourseTree()
        c1 = Course(id=1, name="Course One")
        c2 = Course(id=2, name="Course Two")
        ct.set_courses([c1, c2], selected={1})

        rows = ct.to_dict()
        assert len(rows) == 2
        assert rows[0]["checked"] is True
        assert rows[1]["checked"] is False
        assert (rows[0]["status"], rows[0]["progress"]) == (RowStatus.IDLE, "0%")

        ct.refresh_selection({2})
        rows = ct.to_dict()
        assert rows[0]["checked"] is False
        assert rows[1]["checked"] is True

        assert not hasattr(ct, "show_files")
        assert RowStatus.FETCHING_AND_SYNCING == "Fetching & Syncing"

        ct.reset_for_sync([1, 2])
        assert (ct.to_dict()[0]["status"], ct.to_dict()[0]["progress"]) == (
            RowStatus.QUEUED,
            "0%",
        )

        c_dir = Path("Course One")
        synced_p = c_dir / "S1" / "done.pdf"
        pending_p = c_dir / "S1" / "todo.pdf"
        late_p = c_dir / "S2" / "late.pdf"

        ct.start_course(1, c_dir)
        assert (ct.to_dict()[0]["status"], ct.to_dict()[0]["progress"]) == (
            RowStatus.FETCHING,
            "...",
        )

        ct.add_file(1, synced_p, ContentSyncState.SYNCED)
        row = ct.to_dict()[0]
        assert row["open"] is True
        assert (row["status"], row["progress"]) == (
            RowStatus.FETCHING_AND_SYNCING,
            "1/1+",
        )

        ct.add_file(1, pending_p, ContentSyncState.NEW)
        assert (ct.to_dict()[0]["status"], ct.to_dict()[0]["progress"]) == (
            RowStatus.FETCHING_AND_SYNCING,
            "1/2+",
        )

        ct.set_file_status(pending_p, RowStatus.DOWNLOADING)
        ct.set_file_status(pending_p, RowStatus.COMPLETED)
        assert (ct.to_dict()[0]["status"], ct.to_dict()[0]["progress"]) == (
            RowStatus.FETCHING_AND_SYNCING,
            "2/2+",
        )

        ct.add_file(1, late_p, ContentSyncState.OUTDATED)
        row = ct.to_dict()[0]
        assert (row["status"], row["progress"]) == (
            RowStatus.FETCHING_AND_SYNCING,
            "2/3+",
        )
        assert [child["name"] for child in row["children"]] == ["S1", "S2"]

        ct.finish_discovery(1)
        assert (ct.to_dict()[0]["status"], ct.to_dict()[0]["progress"]) == (
            RowStatus.SYNCING,
            "66%",
        )

        ct.set_course_status(1, RowStatus.FAILED)
        assert (ct.to_dict()[0]["status"], ct.to_dict()[0]["progress"]) == (
            RowStatus.FAILED,
            "66%",
        )

        ct.set_file_status(late_p, RowStatus.COMPLETED)
        assert (ct.to_dict()[0]["status"], ct.to_dict()[0]["progress"]) == (
            RowStatus.SYNCING,
            "100%",
        )

        ct.set_course_status(1, RowStatus.COMPLETED)
        assert (ct.to_dict()[0]["status"], ct.to_dict()[0]["progress"]) == (
            RowStatus.COMPLETED,
            "100%",
        )

        # Empty course transitions to 100% on finish_discovery
        ct.start_course(2, Path("Course Two"))
        assert (ct.to_dict()[1]["status"], ct.to_dict()[1]["progress"]) == (
            RowStatus.FETCHING,
            "...",
        )
        ct.finish_discovery(2)
        assert (ct.to_dict()[1]["status"], ct.to_dict()[1]["progress"]) == (
            RowStatus.SYNCING,
            "100%",
        )

    def test_folder_nodes_track_aggregate_status_and_progress(self):
        """Folder rows report aggregate status and 0%-100% progress."""
        ct = CourseTree()
        c_past = Course(id=1, name="Past")
        c_cur = Course(id=2, name="Current")
        ordered = ct.set_courses([c_past, c_cur], selected={2}, in_progress={2})
        assert [c.id for c in ordered] == [2, 1]
        assert [row["id"] for row in ct.to_dict()] == [2, 1]

        c_dir = Path("Current")
        synced_p = c_dir / "S1" / "done.pdf"
        pending_p = c_dir / "S1" / "todo.pdf"
        ct.start_course(2, c_dir)
        ct.add_file(2, synced_p, ContentSyncState.SYNCED)
        ct.add_file(2, pending_p, ContentSyncState.NEW)
        s1 = ct.to_dict()[0]["children"][0]
        assert (s1["status"], s1["progress"]) == (RowStatus.NEW, "50%")

        ct.set_file_status(pending_p, RowStatus.DOWNLOADING)
        assert ct.to_dict()[0]["children"][0]["status"] == RowStatus.DOWNLOADING

        ct.set_file_status(pending_p, RowStatus.COMPLETED)
        s1 = ct.to_dict()[0]["children"][0]
        assert (s1["status"], s1["progress"]) == (RowStatus.COMPLETED, "100%")

    def test_folder_nodes_emit_unique_ids_across_courses(self):
        """Folder nodes emit unique IDs so collapsing one does not toggle others."""
        ct = CourseTree()
        ct.set_courses(
            [Course(id=1, name="Course One"), Course(id=2, name="Course Two")],
            selected={1, 2},
        )
        ct.start_course(1, Path("Course One"))
        ct.add_file(1, Path("Course One/S1/Sub/a.pdf"), ContentSyncState.SYNCED)
        ct.start_course(2, Path("Course Two"))
        ct.add_file(2, Path("Course Two/S1/b.pdf"), ContentSyncState.SYNCED)

        rows = ct.to_dict()
        c1_s1 = rows[0]["children"][0]
        c1_sub = c1_s1["children"][0]
        c2_s1 = rows[1]["children"][0]
        assert c1_s1["id"]
        assert c1_sub["id"]
        assert c2_s1["id"]
        assert len({c1_s1["id"], c1_sub["id"], c2_s1["id"]}) == 3

    def test_start_course_resets_children_and_stale_counts(self):
        """Starting a course clears prior children and resets Progress counts."""
        ct = CourseTree()
        ct.set_courses([Course(id=1, name="Course One")], selected={1})
        c_dir = Path("Course One")
        ct.start_course(1, c_dir)
        ct.add_file(1, c_dir / "old.pdf", ContentSyncState.SYNCED)
        ct.finish_discovery(1)
        assert ct.to_dict()[0]["progress"] == "100%"

        ct.start_course(1, c_dir)
        ct.add_file(1, c_dir / "new.pdf", ContentSyncState.NEW)
        ct.finish_discovery(1)
        row = ct.to_dict()[0]
        assert row["progress"] == "0%"
        assert [child["name"] for child in row["children"]] == ["new.pdf"]

    def test_failed_course_before_file_discovery_preserves_progress(self):
        """A course failing during FETCHING before add_file preserves '...'."""
        ct = CourseTree()
        ct.set_courses([Course(id=1, name="Course One")], selected={1})
        ct.start_course(1, Path("Course One"))
        ct.set_course_status(1, RowStatus.FAILED)
        row = ct.to_dict()[0]
        assert row["status"] == RowStatus.FAILED
        assert row["progress"] == "..."

    def test_course_tree_terms_ordering(self):
        """Order courses newest-term-first with in_progress and term metadata."""
        ct = CourseTree()
        c_old = Course(id=1, name="Old", category="I.01.2025-IIA")
        c_mid = Course(id=2, name="Mid", category="II.01.2025-III")
        c_cur = Course(id=3, name="Cur", category="IV.01.2026-III")
        ordered = ct.set_courses([c_old, c_mid, c_cur], selected={3}, in_progress={3})
        assert [c.id for c in ordered] == [3, 2, 1]
        rows = ct.to_dict()
        assert [(r["id"], r["term"], r["in_progress"]) for r in rows] == [
            (3, "2026-III", True),
            (2, "2025-III", False),
            (1, "2025-IIA", False),
        ]

    def test_course_tree_progress_speed_cancel_and_activity(self):  # noqa: PLR0915  # pylint: disable=too-many-locals,too-many-statements
        """Track stable file IDs, speed_bps, cancel_sync, and activity projection."""
        ct = CourseTree()
        c_cur = Course(id=3, name="Cur", category="IV.01.2026-III")
        ct.set_courses([c_cur], selected={3}, in_progress={3})

        c_dir = Path("Cur")
        p_sync = c_dir / "S1" / "s.pdf"
        p_new = c_dir / "S1" / "n.pdf"
        p_out = c_dir / "S1" / "o.pdf"
        p_fail = c_dir / "S1" / "f.pdf"
        ct.start_course(3, c_dir)
        ct.add_file(3, p_sync, ContentSyncState.SYNCED)
        ct.add_file(3, p_new, ContentSyncState.NEW)
        ct.add_file(3, p_out, ContentSyncState.OUTDATED)
        ct.add_file(3, p_fail, ContentSyncState.NEW)

        ct.set_file_status(p_new, RowStatus.DOWNLOADING)
        clock = [100.0]
        with patch(
            "idat_sync.ui.course_tree.time.monotonic", side_effect=lambda: clock[0]
        ):
            p1 = ct.set_file_progress(p_new, 200, 1000)
            clock[0] = 101.0
            p2 = ct.set_file_progress(p_new, 1000, 1000)
            clock[0] = 102.0
            ct.set_file_status(p_out, RowStatus.DOWNLOADING)
            p3 = ct.set_file_progress(p_out, 256, None)

        assert p1 is not None
        assert p2 is not None
        assert p3 is not None
        assert p1["id"] == "file:3:S1/n.pdf"
        assert p1["percent"] == 20
        assert p2["percent"] == 99
        assert p2["speed_bps"] is not None
        assert p2["speed_bps"] > 0
        assert p3["percent"] is None

        ct.set_file_status(p_new, RowStatus.COMPLETED)
        ct.set_file_status(p_fail, RowStatus.FAILED, error="disk full")

        activity = ct.activity_to_dict()
        assert activity["total_pending"] == 3
        assert activity["completed"] == 1
        assert activity["skipped"] == 1
        assert activity["failed_count"] == 1
        assert len(activity["downloading"]) == 1
        assert len(activity["finished"]) == 1
        assert activity["failed"][0]["error"] == "disk full"

        ct.cancel_sync()
        files_by_name = {
            f["name"]: f for f in ct.to_dict()[0]["children"][0]["children"]
        }
        assert files_by_name["s.pdf"]["status"] == "Synced"
        assert files_by_name["n.pdf"]["status"] == "Completed"
        assert files_by_name["n.pdf"]["percent"] == 100
        assert files_by_name["o.pdf"]["status"] == "Outdated"
        assert not files_by_name["o.pdf"]["percent"]
        assert files_by_name["f.pdf"]["status"] == "Failed"
        assert files_by_name["f.pdf"]["error"] == "disk full"

    def test_course_tree_reset_for_sync_scopes_activity_to_current_run(self):
        """Scope activity_to_dict to the latest sync run while keeping other trees."""
        ct = CourseTree()
        c1 = Course(id=1, name="Course One")
        c2 = Course(id=2, name="Course Two")
        ct.set_courses([c1, c2], selected={1})

        ct.reset_for_sync([1])
        ct.start_course(1, Path("Course One"))
        p_done = Path("Course One/S1/a.pdf")
        p_sync = Path("Course One/S1/b.pdf")
        p_wait = Path("Course One/S1/c.pdf")
        ct.add_file(1, p_done, ContentSyncState.NEW)
        ct.add_file(1, p_sync, ContentSyncState.SYNCED)
        ct.add_file(1, p_wait, ContentSyncState.OUTDATED)
        ct.set_file_status(p_done, RowStatus.COMPLETED)
        assert ct.activity_to_dict()["total_pending"] == 2
        assert ct.activity_to_dict()["skipped"] == 1

        # Starting a new sync targeting only Course 2 resets Activity immediately
        ct.reset_for_sync([2])
        act = ct.activity_to_dict()
        assert act == {
            "total_pending": 0,
            "completed": 0,
            "skipped": 0,
            "failed_count": 0,
            "percent": 0,
            "downloading": [],
            "queued": [],
            "finished": [],
            "failed": [],
        }

        # Course 1's children remain intact for browsing while Course 2 syncs
        ct.start_course(2, Path("Course Two"))
        ct.add_file(2, Path("Course Two/S1/new.pdf"), ContentSyncState.NEW)
        act2 = ct.activity_to_dict()
        assert act2["total_pending"] == 1
        assert [item["course_id"] for item in act2["queued"]] == [2]
        assert act2["finished"] == []
        assert not act2["skipped"]
        c1_row = next(r for r in ct.to_dict() if r["id"] == 1)
        assert len(c1_row["children"]) == 1

    def test_course_tree_resolve_openable_file(self):
        """Resolve only Synced/Completed files strictly contained in course_dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            course_dir = Path(tmpdir) / "Course One"
            ct = CourseTree()
            ct.set_courses([Course(id=1, name="Course One")], selected={1})
            ct.start_course(1, course_dir)

            p_synced = course_dir / "S1" / "synced.pdf"
            p_completed = course_dir / "S1" / "done.pdf"
            p_new = course_dir / "S1" / "new.pdf"
            p_downloading = course_dir / "S1" / "dl.pdf"
            p_failed = course_dir / "S1" / "fail.pdf"
            p_escape = Path(tmpdir) / "outside.pdf"

            ct.add_file(1, p_synced, ContentSyncState.SYNCED)
            ct.add_file(1, p_completed, ContentSyncState.NEW)
            ct.add_file(1, p_new, ContentSyncState.NEW)
            ct.add_file(1, p_downloading, ContentSyncState.NEW)
            ct.add_file(1, p_failed, ContentSyncState.NEW)
            ct.add_file(1, p_escape, ContentSyncState.SYNCED)

            ct.set_file_status(p_completed, RowStatus.COMPLETED)
            ct.set_file_status(p_downloading, RowStatus.DOWNLOADING)
            ct.set_file_status(p_failed, RowStatus.FAILED, error="boom")

            assert (
                ct.resolve_openable_file("file:1:S1/synced.pdf") == p_synced.resolve()
            )
            assert (
                ct.resolve_openable_file("file:1:S1/done.pdf") == p_completed.resolve()
            )
            assert ct.resolve_openable_file("file:1:S1/new.pdf") is None
            assert ct.resolve_openable_file("file:1:S1/dl.pdf") is None
            assert ct.resolve_openable_file("file:1:S1/fail.pdf") is None
            assert ct.resolve_openable_file("file:1:missing.pdf") is None
            assert ct.resolve_openable_file("unknown") is None
            assert ct.resolve_openable_file(f"file:1:{p_escape.as_posix()}") is None
            # Also verify .. traversal embedded in rel_path is rejected
            ct.add_file(
                1,
                course_dir / "S1" / ".." / ".." / "outside.pdf",
                ContentSyncState.SYNCED,
            )
            assert ct.resolve_openable_file("file:1:S1/../../outside.pdf") is None


class TestErrorFormat(unittest.TestCase):
    """Tests for pure-Python UI error summarization in idat_sync.ui.error_format."""

    def test_error_format_authentication_and_credentials_errors(self):
        """Map authentication and credential errors strictly from attributes."""
        cases: list[tuple[IDATSyncError, str]] = [
            (
                AuthenticationError("MFA code is required", reason="mfa"),
                "Verification code is required",
            ),
            (
                AuthenticationError("SharePoint sign-in timed out.", reason="timeout"),
                "Sign-in timed out — try again",
            ),
            (
                AuthenticationError(
                    "SharePoint browser authentication failed", reason="sharepoint"
                ),
                "SharePoint sign-in failed — try again",
            ),
            (
                AuthenticationError(
                    "Authentication failed: empty token or SharePoint storage state."
                ),
                "Sign-in failed — check your username and password",
            ),
            (
                AuthenticationError("Invalid login"),
                "Sign-in failed — check your username and password",
            ),
            (
                CredentialsExpiredError("HTTP 403", service="SharePoint"),
                "SharePoint credentials expired — sign in again",
            ),
            (
                CredentialsExpiredError("HTTP 401", service="Moodle"),
                "Moodle credentials expired — sign in again",
            ),
            (
                CredentialsExpiredError("401 Unauthorized"),
                "Credentials expired — sign in again",
            ),
            (
                CredentialStoreError(
                    "Could not create credentials file: [Errno 13] Permission denied"
                ),
                "Could not update stored credentials",
            ),
        ]
        for exc, expected in cases:
            with self.subTest(exc=exc):
                assert format_ui_error(exc) == expected

    def test_error_format_filesystem_remote_service_and_base_errors(self):
        """Map FileSystemError, RemoteServiceError, and IDATSyncError."""
        cases: list[tuple[IDATSyncError, str]] = [
            (
                FileSystemError(
                    "Failed to write 'C:\\secret\\a.pdf': disk full", operation="write"
                ),
                "Could not save file to disk",
            ),
            (
                FileSystemError(
                    "Could not scan 'C:\\courses\\write_notes': access denied"
                ),
                "Could not access local folder",
            ),
            (
                RemoteServiceError(
                    "SharePoint error: ('400', \"400 Bad Request for url: "
                    'https://idat628.sharepoint.com/...")',
                    service="SharePoint",
                    status_code=400,
                ),
                "SharePoint error (HTTP 400)",
            ),
            (
                RemoteServiceError(
                    "HTTP 500 from Moodle web service (core_course_get_contents).",
                    service="Moodle",
                    status_code=500,
                ),
                "Moodle error (HTTP 500)",
            ),
            (
                RemoteServiceError(
                    "HTTP 404 for url 'https://aulavirtual.idat.edu.pe/pluginfile.php/1'",
                    status_code=404,
                ),
                "Server error (HTTP 404)",
            ),
            (
                RemoteServiceError(
                    "SharePoint request failed: ConnectError",
                    service="SharePoint",
                    is_network=True,
                ),
                "SharePoint connection error",
            ),
            (
                RemoteServiceError(
                    "Moodle timed out", service="Moodle", is_network=True
                ),
                "Moodle connection error",
            ),
            (
                RemoteServiceError(
                    "Network error downloading 'https://...'", is_network=True
                ),
                "Network connection error",
            ),
            (
                RemoteServiceError(
                    "SharePoint error on file 500_lab.pdf", service="SharePoint"
                ),
                "SharePoint error",
            ),
            (
                RemoteServiceError("Moodle web service failure", service="Moodle"),
                "Moodle error",
            ),
            (
                RemoteServiceError("ERROR: yt-dlp failure"),
                "Remote service error",
            ),
            (
                IDATSyncError("internal detail"),
                "Unexpected sync error",
            ),
        ]
        for exc, expected in cases:
            with self.subTest(exc=exc):
                result = format_ui_error(exc)
                assert result == expected
                assert "http://" not in result.lower()
                assert "https://" not in result.lower()


class TestMainAppOpenPath(unittest.TestCase):
    """Tests for _JsApi.open_file forwarding and MainApp.open_path platform dispatch."""

    def _make_app(self, tmpdir: str) -> app_module.MainApp:
        creds_file = Path(tmpdir) / "session.json"
        creds_file.write_text(
            '{"token": "tok", "sharepoint_storage_state": '
            '{"cookies": [{"name": "a"}], "origins": []}}',
            encoding="utf-8",
        )
        config = SyncConfig(session_file=creds_file)
        app = app_module.MainApp(config)
        self.addCleanup(app.loop.close)
        return app

    def test_js_api_open_file_forwards_to_sync_frame(self):
        """Forward _JsApi.open_file(file_id) to SyncFrame.open_file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            app = self._make_app(tmpdir)
            api = app_module._JsApi(app)  # noqa: SLF001  # pylint: disable=protected-access
            assert isinstance(app.active_frame, SyncFrame)
            with patch.object(app.active_frame, "open_file") as mock_open:
                api.open_file("file:1:S1/a.pdf")
                mock_open.assert_called_once_with("file:1:S1/a.pdf")

    def test_open_path_dispatches_desktop_platforms(self):
        """Dispatch Windows, macOS, and Linux native openers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            app = self._make_app(tmpdir)
            sample = Path(tmpdir) / "notes.pdf"

            with (
                patch.object(app_module, "is_android", return_value=False),
                patch.object(app_module.sys, "platform", "win32"),
                patch.object(app_module.os, "startfile", create=True) as mock_start,
            ):
                app.open_path(sample)
                mock_start.assert_called_once_with(str(sample))

            with (
                patch.object(app_module, "is_android", return_value=False),
                patch.object(app_module.sys, "platform", "darwin"),
                patch.object(app_module.subprocess, "Popen") as mock_popen,
            ):
                app.open_path(sample)
                mock_popen.assert_called_once_with(["open", str(sample)])

            with (
                patch.object(app_module, "is_android", return_value=False),
                patch.object(app_module.sys, "platform", "linux"),
                patch.object(app_module.subprocess, "Popen") as mock_popen,
            ):
                app.open_path(sample)
                mock_popen.assert_called_once_with(["xdg-open", str(sample)])

    def test_open_path_dispatches_android_fileprovider_intent(self):
        """Launch Android ACTION_VIEW Intent with FileProvider URI and read grant."""
        with tempfile.TemporaryDirectory() as tmpdir:
            app = self._make_app(tmpdir)
            sample = Path(tmpdir) / "slides.pdf"

            activity = MagicMock()
            activity.getPackageName.return_value = "pe.edu.idat.idatsync"
            python_activity = MagicMock(mActivity=activity)
            java_file_cls = MagicMock(side_effect=lambda p: f"JavaFile({p})")
            file_provider_cls = MagicMock()
            file_provider_cls.getUriForFile.return_value = "content://uri/slides.pdf"

            intent_instance = MagicMock()
            intent_cls = MagicMock(return_value=intent_instance)
            intent_cls.ACTION_VIEW = "android.intent.action.VIEW"
            intent_cls.FLAG_GRANT_READ_URI_PERMISSION = 1

            classes = {
                "org.kivy.android.PythonActivity": python_activity,
                "java.io.File": java_file_cls,
                "androidx.core.content.FileProvider": file_provider_cls,
                "android.content.Intent": intent_cls,
            }
            fake_jnius = MagicMock(autoclass=MagicMock(side_effect=classes.__getitem__))

            with (
                patch.object(app_module, "is_android", return_value=True),
                patch.object(
                    app_module.importlib, "import_module", return_value=fake_jnius
                ),
            ):
                app.open_path(sample)

            java_file_cls.assert_called_once_with(str(sample))
            file_provider_cls.getUriForFile.assert_called_once_with(
                activity,
                "pe.edu.idat.idatsync.fileprovider",
                f"JavaFile({sample})",
            )
            intent_cls.assert_called_once_with("android.intent.action.VIEW")
            intent_instance.setDataAndType.assert_called_once_with(
                "content://uri/slides.pdf", "application/pdf"
            )
            intent_instance.addFlags.assert_called_once_with(1)
            activity.startActivity.assert_called_once_with(intent_instance)


class TestOpenFilePackaging(unittest.TestCase):
    """Packaging and bundle checks for opening downloaded files."""

    def test_buildozer_spec_configures_android_fileprovider(self):
        """Assert buildozer.spec declares androidx.core:core and FileProvider hook."""
        root = Path(__file__).resolve().parents[1]
        spec_text = (root / "buildozer.spec").read_text(encoding="utf-8")
        assert "androidx.core:core" in spec_text
        assert "android.extra_manifest_application_arguments" not in spec_text
        assert "p4a.hook = android/p4a_hook.py" in spec_text
        assert "android.add_resources" in spec_text
        assert "file_paths.xml" in spec_text
        paths_xml = root / "android" / "res" / "xml" / "file_paths.xml"
        assert paths_xml.is_file()
        xml_text = paths_xml.read_text(encoding="utf-8")
        assert "external-path" in xml_text
        assert "files-path" in xml_text

    def test_p4a_hook_injects_valid_fileprovider_xml(self):
        """Assert android/p4a_hook.py injects a valid <provider> into <application>."""
        root = Path(__file__).resolve().parents[1]
        hook_path = root / "android" / "p4a_hook.py"
        spec = importlib.util.spec_from_file_location("p4a_hook", hook_path)
        assert spec
        assert spec.loader
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)

        sample_manifest = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '    <application android:label="IDAT Sync">\n'
            "    </application>\n"
            "</manifest>\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "AndroidManifest.xml"
            manifest.write_text(sample_manifest, encoding="utf-8")
            with patch.object(hook, "MANIFEST_PATH", manifest):
                hook.before_apk_assemble(None)
                hook.before_apk_assemble(None)

            parsed = ET.fromstring(manifest.read_text(encoding="utf-8"))  # noqa: S314
            providers = parsed.findall("./application/provider")
            assert len(providers) == 1
            ns = "{http://schemas.android.com/apk/res/android}"
            assert (
                providers[0].attrib[f"{ns}name"]
                == "androidx.core.content.FileProvider"
            )
            assert (
                providers[0].attrib[f"{ns}authorities"]
                == "${applicationId}.fileprovider"
            )

    def test_bundled_html_wires_open_file(self):
        """Assert the compiled SolidJS bundle wires the open_file JS API call."""
        html = app_module.load_ui_html()
        assert "open_file" in html


if __name__ == "__main__":
    unittest.main()
