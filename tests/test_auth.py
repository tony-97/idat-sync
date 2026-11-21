"""Unit and UI integration tests for authentication, credential storage, and frames."""
# pylint: disable=too-many-lines

import ast
import asyncio
import importlib.resources
import inspect
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import idat_sync.main as main_module
import idat_sync.ui as ui_pkg
import idat_sync.ui.app as app_module
import idat_sync.ui.sync_frame as sync_frame_mod
from idat_sync.application import exceptions, ports
from idat_sync.application.auth_service import AuthService
from idat_sync.application.events import (
    CourseFilesDiscovered,
    CourseSyncCompleted,
    CourseSyncStarted,
    TaskDownloadCompleted,
    TaskDownloadFailed,
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
from idat_sync.config import SyncConfig
from idat_sync.domain.course import Course, CourseSection
from idat_sync.infrastructure.credential_store import JsonCredentialStore
from idat_sync.ui.app import AppFrame, MainApp
from idat_sync.ui.course_tree import CourseTree, RowStatus
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
                with pytest.raises(AuthenticationError):
                    asyncio.run(service.login("jdoe", "secret", _mfa_code))
                assert store.stored is None

        failing_store = InMemoryCredentialStore(save_result=False)
        service = AuthService(
            moodle_auth=FakeMoodleTokenPort(token="tok"),  # noqa: S106
            sharepoint_auth=FakeSharepointAuthPort(storage_state=state),
            credential_store=failing_store,
        )
        with pytest.raises(CredentialStoreError):
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

    def test_authenticate_strips_domain_and_awaits_moodle_port(self):
        """Strip email domain before delegating authenticate() to MoodleTokenPort."""
        moodle = FakeMoodleTokenPort(token="tok-auth")  # noqa: S106
        service = AuthService(
            moodle_auth=moodle,
            sharepoint_auth=FakeSharepointAuthPort(),
            credential_store=InMemoryCredentialStore(),
        )
        token = asyncio.run(service.authenticate("jdoe@custom.edu", "secret"))
        assert token == "tok-auth"  # noqa: S105
        assert moodle.calls == [("jdoe", "secret")]

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

            with pytest.raises(RemoteServiceError, match="moodle HTTP 500"):
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
            with pytest.raises(AuthenticationError, match="invalid moodle creds"):
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
            with pytest.raises(AuthenticationError, match="blank MFA"):
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
            with pytest.raises(ExceptionGroup) as ctx:
                await service.login("jdoe", "bad", _mfa_code)
            assert [str(e) for e in ctx.value.exceptions] == ["sp crash"]

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
                pytest.raises(
                    CredentialStoreError, match="credentials file"
                ) as save_ctx,
            ):
                store.save(creds)
            assert isinstance(save_ctx.value.__cause__, OSError)
            assert "credentials file" in " ".join(cm.output)

            clear_target = Path(tmpdir) / "locked.json"
            clear_target.write_text("{}", encoding="utf-8")
            clear_store = JsonCredentialStore(clear_target)
            with (
                patch.object(Path, "unlink", side_effect=PermissionError("locked")),
                self.assertLogs(
                    "idat_sync.infrastructure.credential_store", level="ERROR"
                ) as clear_cm,
                pytest.raises(
                    CredentialStoreError, match="credentials file"
                ) as clear_ctx,
            ):
                clear_store.clear()
            assert isinstance(clear_ctx.value.__cause__, OSError)
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
                pytest.raises(CredentialStoreError),
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
            assert frame.error == "sso boom"
            assert frame.can_login
            assert not app.switched_to_sync

            moodle.token = None
            await frame.do_login()
            assert bool(frame.error)
            assert not app.switched_to_sync

            moodle.token = "tok123"  # noqa: S105
            sharepoint.started = None
            sharepoint.release = None
            sharepoint.error = None
            await frame.do_login()

            assert not frame.error
            assert moodle.calls == [
                ("jdoe", " secret "),
                ("jdoe", " secret "),
                ("jdoe", " secret "),
            ]
            assert sharepoint.calls == [
                ("jdoe@custom.edu", " secret "),
                ("jdoe@custom.edu", " secret "),
                ("jdoe@custom.edu", " secret "),
            ]
            assert app.switched_to_sync == 1
            assert frame.can_login

            frame.username = "   "
            assert not frame.can_login
            await frame.do_login()
            assert len(moodle.calls) == 3
            assert not frame.can_login

        asyncio.run(exercise_do_login())

    def test_login_frame_surfaces_logout_credential_store_error_on_init(self):
        """Show CredentialStoreError in error if logout fails during init."""
        mock_auth = MagicMock(spec=AuthService)
        mock_auth.logout.side_effect = CredentialStoreError(
            "Could not remove credentials file: locked"
        )
        app = self._create_app()
        frame = LoginFrame(app, mock_auth)  # type: ignore[arg-type]
        assert "Could not remove credentials file: locked" in frame.error

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

    def test_mfa_popup_return_and_dismiss_and_dead_ui_cleanup(self):
        """Verify inline async MFA future handling and dead UI symbol removal."""
        assert not hasattr(LoginFrame, "get_mfa_code_ui")
        assert not hasattr(AppFrame, "exit")
        assert "LoadingDialog" not in ui_pkg.__all__
        assert not hasattr(ui_pkg, "LoadingDialog")
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
            with pytest.raises(asyncio.CancelledError):
                await task3
            assert frame.mfa_open is False

        asyncio.run(exercise_mfa())


class _SyncStubMaster:
    """Minimal app stub for pure-Python SyncFrame tests."""

    def __init__(self, window: object | None = None) -> None:
        self.window = window
        self.switched_to_login = 0
        self.push_count = 0

    def switch_to_login(self) -> None:
        """Count transitions to LoginFrame."""
        self.switched_to_login += 1

    def push_state(self) -> None:
        """Count state pushes."""
        self.push_count += 1


class TestSyncFrame(unittest.TestCase):
    """Tests for pure-Python SyncFrame selection, sync progress, and cancellation."""

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
                mock_sync = MagicMock()
                mock_sync.aclose = AsyncMock()
                mock_sync.list_courses = AsyncMock(
                    return_value=[past_course, current_course]
                )
                attempts = 0

                async def fake_create_sync_service(_credentials, _cfg):
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise RemoteServiceError("network offline")
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
                    yield CourseSyncStarted(current_course)
                    yield CourseFilesDiscovered(
                        current_course,
                        c_dir,
                        (synced_p,),
                        (ok_p, err_p),
                    )
                    yield TaskDownloadStarted(ok_p)
                    yield TaskDownloadCompleted(ok_p)
                    yield TaskDownloadStarted(err_p)
                    yield TaskDownloadFailed(err_p, FileSystemError("disk full"))
                    yield CourseSyncCompleted(
                        current_course, RemoteServiceError("partial")
                    )

                mock_sync.sync_courses.side_effect = fake_sync_courses

                with patch(
                    "idat_sync.ui.sync_frame.create_sync_service",
                    side_effect=fake_create_sync_service,
                ) as mock_factory:
                    await frame.load_idat_sync(creds)
                    assert frame.idat_sync is None
                    assert not frame.is_syncing
                    assert not master.switched_to_login
                    assert creds_file.exists()
                    assert "network offline" in frame.status

                    frame.set_folder_path(tmpdir)
                    assert frame.can_sync is True
                    await frame.do_sync()
                    assert mock_factory.call_count == 2
                    assert not frame.is_syncing

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

                    frame.clear_selection()
                    assert frame.can_sync is False
                    frame.select_all()
                    assert frame.can_sync is True
                    assert all(r["checked"] for r in frame.course_tree.to_dict())
                    frame.select_in_progress()
                    rows = frame.course_tree.to_dict()
                    assert rows[0]["checked"] is True
                    assert rows[1]["checked"] is False
                    frame.toggle_course(current_course.id)
                    assert frame.can_sync is False
                    frame.toggle_course(current_course.id)
                    assert frame.can_sync is True

                    async def fail_sync_courses(
                        _root_folder, _courses, *, refresh=False
                    ):
                        _ = refresh
                        raise CredentialsExpiredError("401 expired")
                        yield  # pragma: no cover

                    mock_sync.sync_courses.side_effect = fail_sync_courses
                    await frame.do_sync()
                    assert not frame.is_syncing
                    assert not master.switched_to_login
                    assert creds_file.exists()
                    assert "Logout" in frame.status

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
            desktop_app = MainApp(config=config, window=mock_window)
            frame = SyncFrame(desktop_app, creds, config)
            with patch.dict("os.environ", {}, clear=False):
                chosen = frame.browse_folder()
            assert chosen == "/chosen/sync/dir"
            assert frame.folder_path == "/chosen/sync/dir"

            mock_window.create_file_dialog.return_value = None
            with patch.dict("os.environ", {}, clear=False):
                cancelled = frame.browse_folder()
            assert cancelled == "/chosen/sync/dir"
            assert frame.folder_path == "/chosen/sync/dir"

            android_app = MainApp(config=config, window=None)
            android_frame = SyncFrame(android_app, creds, config)
            with patch.dict(
                "os.environ",
                {"ANDROID_ARGUMENT": "1", "EXTERNAL_STORAGE": "/sdcard"},
                clear=False,
            ):
                fallback = android_frame.browse_folder()
            assert fallback == str(Path("/sdcard") / "Download" / "idat-sync")
            assert android_frame.folder_path == fallback

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
                partial_sync = MagicMock()
                partial_sync.aclose = AsyncMock()
                partial_sync.list_courses = AsyncMock(
                    side_effect=RemoteServiceError("list failed")
                )

                with patch(
                    "idat_sync.ui.sync_frame.create_sync_service",
                    new=AsyncMock(return_value=partial_sync),
                ):
                    await frame.load_idat_sync(creds)

                partial_sync.aclose.assert_awaited_once_with()
                assert frame.idat_sync is None
                assert "list failed" in frame.status

                with patch(
                    "idat_sync.ui.sync_frame.create_sync_service",
                    new=AsyncMock(return_value=None),
                ):
                    await frame.load_idat_sync(creds)
                assert "Sync service failed to initialize" in frame.status

                failing_sync = MagicMock()
                failing_sync.aclose = AsyncMock(side_effect=RuntimeError("close boom"))
                frame.idat_sync = failing_sync
                with pytest.raises(RuntimeError, match="close boom"):
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

                    with pytest.raises(RuntimeError, match="close error"):
                        await app.on_close()
                    await app.on_close()

                    active_frame.aclose.assert_awaited_once_with()
                    mock_window.destroy.assert_called_once_with()
                finally:
                    app.loop.close()

        asyncio.run(exercise())

    def test_main_app_initial_frame_switching_and_state_push(self):
        """Select initial frame from credentials, switch views, and push JSON state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            creds_path = Path(tmpdir) / "credentials.json"
            cfg = SyncConfig(session_file=creds_path)
            mock_window = MagicMock()
            app = MainApp(config=cfg, window=mock_window)
            try:
                assert isinstance(app._frame, LoginFrame)  # noqa: SLF001  # pylint: disable=protected-access
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

                app.auth.credential_store.save(
                    {
                        "token": "tok-saved",
                        "sharepoint_storage_state": {"cookies": []},
                    }
                )
                app.switch_to_sync()
                assert isinstance(app._frame, SyncFrame)  # noqa: SLF001  # pylint: disable=protected-access
                assert app.get_state()["view"] == "sync"

                app.switch_to_login()
                assert isinstance(app._frame, LoginFrame)  # noqa: SLF001  # pylint: disable=protected-access
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
                assert isinstance(app_authed._frame, SyncFrame)  # noqa: SLF001  # pylint: disable=protected-access
                assert app_authed.get_state()["view"] == "sync"
            finally:
                app_authed.loop.close()

    def test_main_app_on_close_cancels_active_login_and_mfa(self):
        """Verify on_close cancels active login task and dismisses open MFA prompt."""

        async def exercise() -> None:
            with tempfile.TemporaryDirectory() as tmpdir:
                cfg = SyncConfig(session_file=Path(tmpdir) / "credentials.json")
                mock_window = MagicMock()
                app = MainApp(config=cfg, window=mock_window)
                try:
                    frame = app._frame  # noqa: SLF001  # type: ignore[attr-defined]  # pylint: disable=protected-access
                    assert isinstance(frame, LoginFrame)
                    frame.username = "jdoe"
                    frame.password = "secret"  # noqa: S105

                    mfa_ready = asyncio.Event()

                    async def slow_login(_username, _password, get_mfa_code):
                        mfa_task = asyncio.create_task(get_mfa_code())
                        await asyncio.sleep(0)
                        mfa_ready.set()
                        await mfa_task

                    with patch.object(app.auth, "login", side_effect=slow_login):
                        login_task = asyncio.create_task(frame.do_login())
                        await asyncio.wait_for(mfa_ready.wait(), timeout=2.0)
                        assert frame.mfa_open

                        await app.on_close()
                        assert not frame.mfa_open
                        mock_window.destroy.assert_called_once_with()
                        with pytest.raises(asyncio.CancelledError):
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
                app = MainApp(config=cfg, window=MagicMock())
                try:
                    frame = app._frame  # noqa: SLF001  # type: ignore[attr-defined]  # pylint: disable=protected-access
                    assert isinstance(frame, LoginFrame)
                    frame.username = "jdoe"
                    frame.password = "secret"  # noqa: S105

                    async def ok_login(_username, _password, _get_mfa_code):
                        app.auth.credential_store.save(
                            {
                                "token": "tok",
                                "sharepoint_storage_state": {"cookies": []},
                            }
                        )

                    with patch.object(app.auth, "login", side_effect=ok_login):
                        await frame.do_login()

                    assert isinstance(app._frame, SyncFrame)  # noqa: SLF001  # pylint: disable=protected-access
                finally:
                    app.loop.close()

        asyncio.run(exercise())

    def test_main_app_run_starts_webview_with_bundled_html_and_ua(self):
        """Load bundled index.html via importlib.resources and start webview."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = SyncConfig(session_file=Path(tmpdir) / "missing_credentials.json")
            app = MainApp(config=cfg)
            expected_html = (
                importlib.resources.files("idat_sync.ui")
                .joinpath("index.html")
                .read_text(encoding="utf-8")
            )
            fake_window = MagicMock()
            fake_window.events = MagicMock()
            loop_was_running = False

            def fake_start(**kwargs):
                nonlocal loop_was_running
                loop_was_running = app.loop.is_running()
                assert kwargs.get("user_agent") == app_module._GALAXY_A55_UA  # noqa: SLF001  # pylint: disable=protected-access

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
            assert create_kwargs["js_api"].get_state()["view"] == "login"
            fake_webview.start.assert_called_once_with(
                user_agent=app_module._GALAXY_A55_UA  # noqa: SLF001  # pylint: disable=protected-access
            )

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
                    with pytest.raises(asyncio.CancelledError):
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

                mock_service = MagicMock()
                mock_service.sync_courses = slow_sync_courses
                mock_service.aclose = AsyncMock()
                frame.idat_sync = mock_service

                sync_task = asyncio.create_task(frame.do_sync())
                await asyncio.wait_for(sync_started.wait(), timeout=2.0)
                await frame.aclose()
                assert sync_cancelled.is_set()
                mock_service.aclose.assert_awaited_once_with()
                with pytest.raises(asyncio.CancelledError):
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

                mock_service = MagicMock()
                mock_service.sync_courses = err_sync_courses
                frame.idat_sync = mock_service

                await frame.do_sync()
                assert frame.status == "Sync completed with errors."

                async def ok_sync_courses(_path, _selected, *, refresh=False):
                    _ = refresh
                    yield CourseSyncCompleted(c1, None)

                mock_service.sync_courses = ok_sync_courses
                await frame.do_sync()
                assert frame.status == "Sync done."

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

            with pytest.raises(RuntimeError, match="boom"):
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
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(exercise())


class TestCourseTree(unittest.TestCase):
    """Tests for pure-Python CourseTree selection, additive discovery, and progress."""

    def test_course_tree_lifecycle_and_progress(self):
        """Manage course rows, nested folders, additive batches, and to_dict."""
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

        ct.reset_for_sync([1])
        assert (ct.to_dict()[0]["status"], ct.to_dict()[0]["progress"]) == (
            RowStatus.QUEUED,
            "0%",
        )

        c_dir = Path("Course One")
        synced_p = c_dir / "S1" / "done.pdf"
        pending_p = c_dir / "S1" / "todo.pdf"
        extra_p = c_dir / "S2" / "extra.pdf"
        ct.show_files(1, c_dir, (synced_p,), (pending_p,))
        assert (ct.to_dict()[0]["status"], ct.to_dict()[0]["progress"]) == (
            RowStatus.SYNCING,
            "50%",
        )

        # Second batch appends additively without clearing S1
        ct.show_files(1, c_dir, (), (extra_p,))
        assert (ct.to_dict()[0]["status"], ct.to_dict()[0]["progress"]) == (
            RowStatus.SYNCING,
            "33%",
        )
        folder_names = [child["name"] for child in ct.to_dict()[0]["children"]]
        assert folder_names == ["S1", "S2"]

        ct.set_file_status(pending_p, RowStatus.DOWNLOADING)
        ct.set_file_status(pending_p, RowStatus.COMPLETED)
        ct.set_file_status(extra_p, RowStatus.COMPLETED)
        assert (ct.to_dict()[0]["status"], ct.to_dict()[0]["progress"]) == (
            RowStatus.SYNCING,
            "100%",
        )

        ct.set_course_status(1, RowStatus.COMPLETED)
        assert (ct.to_dict()[0]["status"], ct.to_dict()[0]["progress"]) == (
            RowStatus.COMPLETED,
            "100%",
        )

    def test_folder_nodes_emit_unique_ids_across_courses(self):
        """Folder nodes emit unique IDs so collapsing one does not toggle others."""
        ct = CourseTree()
        ct.set_courses(
            [Course(id=1, name="Course One"), Course(id=2, name="Course Two")],
            selected={1, 2},
        )
        ct.show_files(
            1,
            Path("Course One"),
            (Path("Course One/S1/Sub/a.pdf"),),
            (),
        )
        ct.show_files(
            2,
            Path("Course Two"),
            (Path("Course Two/S1/b.pdf"),),
            (),
        )

        rows = ct.to_dict()
        c1_s1 = rows[0]["children"][0]
        c1_sub = c1_s1["children"][0]
        c2_s1 = rows[1]["children"][0]
        assert c1_s1["id"]
        assert c1_sub["id"]
        assert c2_s1["id"]
        assert len({c1_s1["id"], c1_sub["id"], c2_s1["id"]}) == 3

    def test_fetching_status_initializes_progress_without_stale_counts(self):
        """Transitioning to FETCHING resets Progress even without reset_for_sync."""
        ct = CourseTree()
        ct.set_courses([Course(id=1, name="Course One")], selected={1})
        c_dir = Path("Course One")
        ct.show_files(1, c_dir, (c_dir / "old.pdf",), ())
        assert ct.to_dict()[0]["progress"] == "100%"

        ct.set_course_status(1, RowStatus.FETCHING)
        ct.show_files(1, c_dir, (), (c_dir / "new.pdf",))
        assert ct.to_dict()[0]["progress"] == "0%"


if __name__ == "__main__":
    unittest.main()
