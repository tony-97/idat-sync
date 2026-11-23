"""Unit tests for WebView-driven SharePoint SSO/MFA authentication."""

from __future__ import annotations

import asyncio
import gc
import inspect
import io
import json
import os
import runpy
import sys
import threading
import types
import unittest
import weakref
from http.cookies import SimpleCookie
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from idat_sync.application.ports import AuthenticationError, CookieData
from idat_sync.bootstrap import create_auth_service
from idat_sync.config import WEBVIEW_USER_AGENT, SyncConfig
from idat_sync.infrastructure import sharepoint_auth as sp_auth_mod
from idat_sync.infrastructure.sharepoint_auth import (
    AndroidWebViewDriver,
    PyWebviewDriver,
    SharepointBrowserAuth,
)


async def _mfa_code() -> str:
    return " 123456 "


_ACADEMIC_COOKIES: tuple[CookieData, ...] = (
    {
        "name": "FedAuth",
        "value": "acad-fed",
        "domain": "contoso.sharepoint.com",
        "path": "/",
        "secure": True,
    },
    {
        "name": "rtFa",
        "value": "shared-old",
        "domain": ".sharepoint.com",
        "path": "/",
        "secure": True,
    },
)
_PERSONAL_COOKIES: tuple[CookieData, ...] = (
    {
        "name": "rtFa",
        "value": "shared-new",
        "domain": ".sharepoint.com",
        "path": "/",
        "secure": True,
    },
    {
        "name": "SPOIDCRL",
        "value": "pers-spo",
        "domain": "contoso-my.sharepoint.com",
        "path": "/",
        "secure": True,
    },
)


class FakeWebViewDriver:  # pylint: disable=too-many-instance-attributes
    """In-memory WebViewDriver double simulating Microsoft Entra ID SSO/MFA."""

    def __init__(self, *, skip_visible: bool = False) -> None:
        """Initialize the fake driver with configurable MFA skip state."""
        self.skip_visible = skip_visible
        self.url = ""
        self.visited_urls: list[str] = []
        self.scripts: list[str] = []
        self.closed = False
        self._step = 0

    async def goto(self, url: str) -> None:
        """Record navigation and simulate initial Entra ID redirect."""
        self.visited_urls.append(url)
        if "_layouts/15/sharepoint.aspx" in url:
            self.url = "https://login.microsoftonline.com/tenant/oauth2/authorize"
        else:
            self.url = url

    async def get_url(self) -> str:
        """Return the current simulated URL."""
        return self.url

    async def evaluate_js(self, script: str) -> Any:
        """Simulate DOM visibility checks, fills, clicks, and URL transitions."""
        self.scripts.append(script)
        if "#skipMfaRegistrationLink" in script and "getBoundingClientRect" in script:
            if "#KmsiCheckboxField" in script:
                return True
            return self.skip_visible
        if "#i0118" in script and "HTMLInputElement" in script:
            self._step = 1
        elif "#idSIButton9" in script and ".click()" in script:
            if self._step == 1:
                self.url = "https://login.microsoftonline.com/tenant/login"
                self._step = 2
            elif self._step == 3:
                self.url = "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx"
        elif "#idSubmit_SAOTCC_Continue" in script and ".click()" in script:
            self.url = "https://login.microsoftonline.com/common/SAS/ProcessAuth"
            self._step = 3
        return True

    async def get_cookies(self) -> list[CookieData]:
        """Return host-scoped cookies for the current URL."""
        if "contoso-my.sharepoint.com" in self.url:
            return list(_PERSONAL_COOKIES)
        return list(_ACADEMIC_COOKIES)

    async def aclose(self) -> None:
        """Mark the driver closed."""
        self.closed = True


class TestSharepointBrowserAuth(unittest.TestCase):
    """Tests for SharepointBrowserAuth WebView SSO/MFA flow."""

    def test_requires_tenant_hosts_and_omits_login_flow_ended(self) -> None:
        """Require tenant host args and omit login_flow_ended parameter."""
        with pytest.raises(TypeError, match="sharepoint_host"):
            SharepointBrowserAuth()  # type: ignore[call-arg]  # pylint: disable=no-value-for-parameter

        sig = inspect.signature(SharepointBrowserAuth.fetch_storage_state)
        assert "login_flow_ended" not in sig.parameters
        assert list(sig.parameters) == ["self", "username", "password", "get_mfa_code"]

    def test_fetch_storage_state_completes_sso_mfa_and_deduplicates_cookies(
        self,
    ) -> None:
        """Drive SSO/MFA, normalize email, merge cookies, and close driver."""
        for input_user, expected_email, skip_visible in [
            ("jdoe", "jdoe@example.edu", True),
            ("jdoe@other.edu", "jdoe@other.edu", False),
        ]:
            with self.subTest(input_user=input_user, skip_visible=skip_visible):
                driver = FakeWebViewDriver(skip_visible=skip_visible)
                auth = SharepointBrowserAuth(
                    sharepoint_host="contoso.sharepoint.com",
                    sharepoint_personal_host="contoso-my.sharepoint.com",
                    email_domain="example.edu",
                    driver=driver,
                )
                with patch(
                    "idat_sync.infrastructure.sharepoint_auth.asyncio.sleep",
                    new=AsyncMock(),
                ) as mock_sleep:
                    state = asyncio.run(
                        auth.fetch_storage_state(input_user, "pw", _mfa_code)
                    )

                assert driver.closed is True
                assert driver.visited_urls == [
                    "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx",
                    "https://contoso-my.sharepoint.com/shared",
                ]
                assert any(
                    "#i0116" in s and expected_email in s for s in driver.scripts
                )
                assert any("#i0118" in s and "pw" in s for s in driver.scripts)
                assert any(
                    "#idTxtBx_SAOTCC_OTC" in s and "123456" in s for s in driver.scripts
                )
                kmsi_wait_scripts = [
                    s
                    for s in driver.scripts
                    if "#skipMfaRegistrationLink, #KmsiCheckboxField" in s
                ]
                assert kmsi_wait_scripts
                assert all(
                    "document.querySelectorAll(" in s
                    and "document.querySelector(" not in s
                    for s in kmsi_wait_scripts
                )
                skip_clicked = any(
                    "#skipMfaRegistrationLink" in s and ".click()" in s
                    for s in driver.scripts
                )
                assert skip_clicked is skip_visible
                assert mock_sleep.await_count >= 2
                cookies = state["cookies"]
                assert len(cookies) == 3
                by_key = {(c["domain"], c["path"], c["name"]): c for c in cookies}
                assert by_key[("contoso.sharepoint.com", "/", "FedAuth")]["value"] == (
                    "acad-fed"
                )
                assert by_key[(".sharepoint.com", "/", "rtFa")]["value"] == (
                    "shared-new"
                )
                assert by_key[("contoso-my.sharepoint.com", "/", "SPOIDCRL")][
                    "value"
                ] == ("pers-spo")

    def test_waits_for_personal_host_sso_redirect_and_host_auth_cookie(self) -> None:
        """Poll past personal-host SSO redirect until host auth cookie is set."""

        class _RedirectingPersonalDriver(FakeWebViewDriver):
            def __init__(self) -> None:
                super().__init__()
                self._personal_polls = 0

            async def goto(self, url: str) -> None:
                await super().goto(url)
                if "contoso-my.sharepoint.com" in url:
                    self._personal_polls = 1

            async def get_url(self) -> str:
                if self._personal_polls == 2:
                    self._personal_polls = 3
                    return "https://login.microsoftonline.com/tenant/oauth2/authorize"
                if self._personal_polls >= 3:
                    return "https://contoso-my.sharepoint.com/personal/jdoe"
                return self.url

            async def get_cookies(self) -> list[CookieData]:
                if self._personal_polls == 1:
                    self._personal_polls = 2
                    return [
                        {
                            "name": "rtFa",
                            "value": "shared-old",
                            "domain": ".sharepoint.com",
                            "path": "/",
                            "secure": True,
                        }
                    ]
                if self._personal_polls >= 3:
                    return [
                        {
                            "name": "FedAuth",
                            "value": "pers-fed",
                            "domain": "contoso-my.sharepoint.com",
                            "path": "/",
                            "secure": True,
                        },
                        {
                            "name": "rtFa",
                            "value": "shared-new",
                            "domain": ".sharepoint.com",
                            "path": "/",
                            "secure": True,
                        },
                    ]
                return await super().get_cookies()

        driver = _RedirectingPersonalDriver()
        auth = SharepointBrowserAuth(
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            email_domain="example.edu",
            driver=driver,
        )
        with patch(
            "idat_sync.infrastructure.sharepoint_auth.asyncio.sleep",
            new=AsyncMock(),
        ):
            state = asyncio.run(auth.fetch_storage_state("jdoe", "pw", _mfa_code))

        by_key = {(c["domain"], c["path"], c["name"]): c for c in state["cookies"]}
        assert by_key[("contoso-my.sharepoint.com", "/", "FedAuth")]["value"] == (
            "pers-fed"
        )

    def test_dom_actions_require_visible_opaque_enabled_elements(self) -> None:
        """Gate fill/click JS on visibility, opacity > 0, and enabled state."""
        driver = FakeWebViewDriver()
        auth = SharepointBrowserAuth(
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            email_domain="example.edu",
            driver=driver,
        )
        with patch(
            "idat_sync.infrastructure.sharepoint_auth.asyncio.sleep",
            new=AsyncMock(),
        ):
            asyncio.run(auth.fetch_storage_state("jdoe", "pw", _mfa_code))

        action_scripts = [
            s for s in driver.scripts if "#i0118" in s or "#idSIButton9" in s
        ]
        assert action_scripts
        for script in action_scripts:
            assert "getComputedStyle(el)" in script
            assert "visibility !== 'hidden'" in script
            assert "parseFloat(cs.opacity) > 0" in script
            assert "!el.disabled" in script
            assert "getAttribute('aria-disabled') !== 'true'" in script
        assert any(
            '#idDiv_SAOTCS_Proofs > div:nth-child(2) [role=\\"button\\"]' in s
            and ".click()" in s
            for s in driver.scripts
        )

    def test_blank_mfa_raises_authentication_error_and_closes_driver(self) -> None:
        """Raise AuthenticationError without filling OTP when MFA code is blank."""
        driver = FakeWebViewDriver()
        auth = SharepointBrowserAuth(
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            email_domain="example.edu",
            driver=driver,
        )

        async def _blank_mfa() -> str:
            return "   "

        with (
            patch(
                "idat_sync.infrastructure.sharepoint_auth.asyncio.sleep",
                new=AsyncMock(),
            ),
            pytest.raises(AuthenticationError, match="MFA code is required") as ctx,
        ):
            asyncio.run(auth.fetch_storage_state("jdoe", "pw", _blank_mfa))

        assert ctx.value.reason == "mfa"
        assert not any("#idTxtBx_SAOTCC_OTC" in s for s in driver.scripts)
        assert driver.closed is True

    def test_translates_timeout_and_runtime_errors_and_propagates_cancellation(
        self,
    ) -> None:
        """Translate TimeoutError and runtime errors; propagate CancelledError."""
        # TimeoutError
        timeout_driver = FakeWebViewDriver()
        timeout_driver.goto = AsyncMock(side_effect=TimeoutError("timed out"))  # type: ignore[method-assign]
        auth_timeout = SharepointBrowserAuth(
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            email_domain="example.edu",
            driver=timeout_driver,
        )
        with (
            self.assertLogs(
                "idat_sync.infrastructure.sharepoint_auth", level="ERROR"
            ) as log_cm,
            pytest.raises(
                AuthenticationError, match=r"SharePoint sign-in timed out\."
            ) as exc_info,
        ):
            asyncio.run(auth_timeout.fetch_storage_state("jdoe", "pw", _mfa_code))
        assert isinstance(exc_info.value.__cause__, TimeoutError)
        assert exc_info.value.reason == "timeout"
        assert any(r.exc_info is not None for r in log_cm.records)
        assert timeout_driver.closed is True

        # Runtime error
        err_driver = FakeWebViewDriver()
        err_driver.goto = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
        auth_err = SharepointBrowserAuth(
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            email_domain="example.edu",
            driver=err_driver,
        )
        with (
            self.assertLogs(
                "idat_sync.infrastructure.sharepoint_auth", level="ERROR"
            ) as log_cm,
            pytest.raises(
                AuthenticationError, match="SharePoint browser authentication failed"
            ) as exc_info,
        ):
            asyncio.run(auth_err.fetch_storage_state("jdoe", "pw", _mfa_code))
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert exc_info.value.reason == "sharepoint"
        assert any(r.exc_info is not None for r in log_cm.records)
        assert err_driver.closed is True

        # CancelledError propagates even if aclose raises
        cancel_driver = FakeWebViewDriver()
        cancel_driver.goto = AsyncMock(side_effect=asyncio.CancelledError())  # type: ignore[method-assign]
        cancel_driver.aclose = AsyncMock(side_effect=RuntimeError("close failed"))  # type: ignore[method-assign]
        auth_cancel = SharepointBrowserAuth(
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            email_domain="example.edu",
            driver=cancel_driver,
        )
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(auth_cancel.fetch_storage_state("jdoe", "pw", _mfa_code))
        cancel_driver.aclose.assert_awaited_once()


class TestDriverSelectionAndAdapters(unittest.TestCase):
    """Tests for default driver selection, PyWebviewDriver, and AndroidWebViewDriver."""

    def test_create_auth_service_wires_platform_webview_driver(self) -> None:
        """Select AndroidWebViewDriver on Android env markers, PyWebviewDriver else."""
        cfg = SyncConfig()
        with patch.dict(os.environ, {}, clear=True):
            auth_service = create_auth_service(cfg)
            assert isinstance(auth_service.sharepoint_auth, SharepointBrowserAuth)
            assert isinstance(auth_service.sharepoint_auth.driver, PyWebviewDriver)
        with patch.dict(os.environ, {"ANDROID_ARGUMENT": "1"}, clear=True):
            auth_service = create_auth_service(cfg)
            assert isinstance(auth_service.sharepoint_auth, SharepointBrowserAuth)
            assert isinstance(auth_service.sharepoint_auth.driver, AndroidWebViewDriver)
        with patch.dict(os.environ, {"ANDROID_BOOTLOGO": "1"}, clear=True):
            auth_service = create_auth_service(cfg)
            assert isinstance(auth_service.sharepoint_auth, SharepointBrowserAuth)
            assert isinstance(auth_service.sharepoint_auth.driver, AndroidWebViewDriver)

    def test_pywebview_driver_lifecycle_and_cookie_conversion(self) -> None:
        """Create hidden window, call hide(), convert SimpleCookie, and destroy."""
        mock_window = MagicMock()
        mock_window.get_current_url.return_value = (
            "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx"
        )
        mock_window.evaluate_js.return_value = True

        cookie_with_domain = SimpleCookie()
        cookie_with_domain["FedAuth"] = "fed-val"
        cookie_with_domain["FedAuth"]["domain"] = ".sharepoint.com"
        cookie_with_domain["FedAuth"]["path"] = "/"
        cookie_with_domain["FedAuth"]["secure"] = True

        cookie_without_domain = SimpleCookie()
        cookie_without_domain["rtFa"] = "rt-val"

        mock_window.get_cookies.return_value = [
            cookie_with_domain,
            cookie_without_domain,
        ]

        fake_webview = types.SimpleNamespace(
            windows=[MagicMock()],
            create_window=MagicMock(return_value=mock_window),
            start=MagicMock(),
        )

        async def _exercise() -> list[CookieData]:
            driver = PyWebviewDriver()
            await driver.goto(
                "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx"
            )
            await driver.goto("https://contoso-my.sharepoint.com/shared")
            assert await driver.get_url() == (
                "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx"
            )
            assert await driver.evaluate_js("1 + 1") is True
            cookies = await driver.get_cookies()
            await driver.aclose()
            return cookies

        with (
            patch(
                "idat_sync.infrastructure.sharepoint_auth.importlib.import_module",
                return_value=fake_webview,
            ),
            patch.object(sys, "platform", "linux"),
        ):
            cookies = asyncio.run(_exercise())

        fake_webview.create_window.assert_called_once_with(
            "",
            "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx",
            hidden=True,
        )
        assert "user_agent" not in fake_webview.create_window.call_args.kwargs
        fake_webview.start.assert_not_called()
        mock_window.hide.assert_called_once()
        mock_window.load_url.assert_called_once_with(
            "https://contoso-my.sharepoint.com/shared"
        )
        mock_window.destroy.assert_called_once()
        assert cookies == [
            {
                "name": "FedAuth",
                "value": "fed-val",
                "domain": ".sharepoint.com",
                "path": "/",
                "secure": True,
            },
            {
                "name": "rtFa",
                "value": "rt-val",
                "domain": "contoso.sharepoint.com",
                "path": "/",
                "secure": False,
            },
        ]

    def test_pywebview_driver_cloaks_win32_window_without_hide(self) -> None:
        """On Windows, cloak via Win32 layered style instead of Form.Hide()."""
        mock_handle = MagicMock()
        mock_handle.ToInt64.return_value = 12345
        mock_window = MagicMock()
        mock_window.native = types.SimpleNamespace(Handle=mock_handle)
        mock_window.events = types.SimpleNamespace(before_show=MagicMock())
        fake_webview = types.SimpleNamespace(
            windows=[MagicMock()],
            create_window=MagicMock(return_value=mock_window),
        )
        fake_user32 = MagicMock()
        fake_user32.GetWindowLongW.return_value = 0x00040000
        fake_ctypes = types.SimpleNamespace(
            windll=types.SimpleNamespace(user32=fake_user32)
        )

        async def _exercise() -> None:
            driver = PyWebviewDriver()
            await driver.goto(
                "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx"
            )
            await driver.aclose()

        with (
            patch(
                "idat_sync.infrastructure.sharepoint_auth.importlib.import_module",
                return_value=fake_webview,
            ),
            patch.object(sys, "platform", "win32"),
            patch.object(sp_auth_mod, "ctypes", fake_ctypes),
        ):
            asyncio.run(_exercise())

        fake_webview.create_window.assert_called_once_with(
            "",
            "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx",
            focus=False,
            frameless=True,
            shadow=False,
        )
        mock_window.hide.assert_not_called()
        fake_user32.SetWindowLongW.assert_called_once_with(
            12345, -20, 0x00000080 | 0x00000020 | 0x00080000 | 0x08000000
        )
        fake_user32.SetLayeredWindowAttributes.assert_called_once_with(12345, 0, 0, 0x2)

    def test_pywebview_driver_standalone_mode_uses_subprocess_worker(
        self,
    ) -> None:
        """Spawn a standalone subprocess worker when no GUI windows exist."""
        responses = io.StringIO(
            json.dumps({"ok": True})
            + "\n"
            + json.dumps({"ok": True, "result": "https://contoso.sharepoint.com/"})
            + "\n"
            + json.dumps({"ok": True, "result": True})
            + "\n"
            + json.dumps({"ok": True, "result": []})
            + "\n"
        )
        sent = io.StringIO()
        fake_proc = MagicMock()
        fake_proc.stdin = sent
        fake_proc.stdout = responses
        fake_proc.wait = MagicMock(return_value=0)
        fake_webview = types.SimpleNamespace(windows=[])

        async def _exercise() -> None:
            driver = PyWebviewDriver()
            await driver.goto(
                "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx"
            )
            assert await driver.get_url() == "https://contoso.sharepoint.com/"
            assert await driver.evaluate_js("1 + 1") is True
            assert await driver.get_cookies() == []
            await driver.aclose()

        with (
            patch(
                "idat_sync.infrastructure.sharepoint_auth.subprocess.Popen",
                return_value=fake_proc,
            ) as mock_popen,
            patch.dict("sys.modules", {"webview": fake_webview}),
        ):
            asyncio.run(_exercise())

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args.args[0]
        assert cmd[1:3] == ["-m", "idat_sync.infrastructure.sharepoint_auth"]
        assert cmd[3] == "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx"
        fake_proc.wait.assert_called_once()

    def test_standalone_worker_runs_start_on_main_thread_and_hides_window(
        self,
    ) -> None:
        """Run webview.start on the main thread, hide the window, and serve RPCs."""
        mock_window = MagicMock()
        mock_window.get_current_url.return_value = (
            "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx"
        )
        mock_window.evaluate_js.return_value = True
        cookie = SimpleCookie()
        cookie["FedAuth"] = "val"
        mock_window.get_cookies.return_value = [cookie]

        start_calls: list[tuple[bool, dict[str, Any]]] = []

        def _fake_start(func: Any, *args: Any, **kwargs: Any) -> None:
            start_calls.append(
                (threading.current_thread() is threading.main_thread(), kwargs)
            )
            if callable(func):
                func(*args)

        fake_webview = types.SimpleNamespace(
            create_window=MagicMock(return_value=mock_window),
            start=MagicMock(side_effect=_fake_start),
        )
        stdin = io.StringIO(
            "\n".join(
                [
                    json.dumps({"cmd": "get_url"}),
                    json.dumps({"cmd": "evaluate_js", "script": "true"}),
                    json.dumps({"cmd": "get_cookies"}),
                    json.dumps({"cmd": "close"}),
                ]
            )
            + "\n"
        )
        stdout = io.StringIO()

        with (
            patch.dict(sys.modules, {"webview": fake_webview}),
            patch.object(sys, "platform", "linux"),
            patch.object(
                sys,
                "argv",
                [
                    "sharepoint_auth",
                    "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx",
                ],
            ),
            patch.object(sys, "stdin", stdin),
            patch.object(sys, "stdout", stdout),
        ):
            runpy.run_path(sp_auth_mod.__file__, run_name="__main__")

        assert start_calls == [(True, {"user_agent": WEBVIEW_USER_AGENT})]
        mock_window.hide.assert_called_once_with()
        mock_window.destroy.assert_called_once_with()
        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        assert lines[0] == {"ok": True}
        assert lines[1]["result"] == (
            "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx"
        )
        assert lines[2]["result"] is True
        assert lines[3]["result"][0]["name"] == "FedAuth"

    def test_android_webview_driver_lifecycle_and_cookie_manager(self) -> None:
        """Configure 1x1 INVISIBLE WebView, read CookieManager, and destroy."""
        mocks, fake_import = _make_fake_android_env()
        cb_weakref: list[weakref.ReferenceType[Any]] = []

        def _delayed_eval(_script: str, cb: Any) -> None:
            cb_weakref.append(weakref.ref(cb))

            def _deliver() -> None:
                gc.collect()
                target = cb_weakref[0]()
                assert target is not None, "Android ValueCallback was garbage-collected"
                target.onReceiveValue("true")

            threading.Timer(0.01, _deliver).start()

        mocks.wv.evaluateJavascript.side_effect = _delayed_eval

        async def _exercise() -> list[CookieData]:
            driver = AndroidWebViewDriver()
            await driver.goto(
                "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx"
            )
            assert await driver.evaluate_js("true") is True
            cookies = await driver.get_cookies()
            await driver.aclose()
            return cookies

        with patch(
            "idat_sync.infrastructure.sharepoint_auth.importlib.import_module",
            side_effect=fake_import,
        ):
            cookies = asyncio.run(_exercise())

        assert mocks.settings.setJavaScriptEnabled.call_args.args == (True,)
        assert mocks.settings.setDomStorageEnabled.call_args.args == (True,)
        mocks.settings.setUserAgentString.assert_called_once_with(WEBVIEW_USER_AGENT)
        mocks.wv.setWebViewClient.assert_called_once_with(mocks.client_cls.return_value)
        mocks.wv.setVisibility.assert_called_once_with(4)
        mocks.params_cls.assert_called_once_with(1, 1)
        mocks.activity.addContentView.assert_called_once_with(mocks.wv, "params-1x1")
        mocks.parent.removeView.assert_called_once_with(mocks.wv)
        mocks.wv.destroy.assert_called_once()
        assert cookies == [
            {
                "name": "FedAuth",
                "value": "a=1",
                "domain": "contoso.sharepoint.com",
                "path": "/",
                "secure": True,
            },
            {
                "name": "rtFa",
                "value": "b2",
                "domain": "contoso.sharepoint.com",
                "path": "/",
                "secure": True,
            },
        ]


def _make_fake_android_env():
    mock_settings = MagicMock()
    mock_parent = MagicMock()
    mock_wv = MagicMock()
    mock_wv.getSettings.return_value = mock_settings
    mock_wv.getUrl.return_value = (
        "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx"
    )
    mock_wv.getParent.return_value = mock_parent
    mock_wv.evaluateJavascript.side_effect = lambda _s, cb: cb.onReceiveValue("true")

    mock_activity = MagicMock()
    mock_cookie_mgr = MagicMock()
    mock_cookie_mgr.getCookie.return_value = "FedAuth=a=1; rtFa=b2"
    mock_client_cls = MagicMock()
    mock_params_cls = MagicMock(return_value="params-1x1")
    classes = {
        "org.kivy.android.PythonActivity": types.SimpleNamespace(
            mActivity=mock_activity
        ),
        "android.webkit.WebView": MagicMock(return_value=mock_wv),
        "android.webkit.WebViewClient": mock_client_cls,
        "android.view.View": types.SimpleNamespace(INVISIBLE=4),
        "android.view.ViewGroup$LayoutParams": mock_params_cls,
        "android.webkit.CookieManager": types.SimpleNamespace(
            getInstance=MagicMock(return_value=mock_cookie_mgr)
        ),
    }

    class _FakePythonJavaClass:  # pylint: disable=too-few-public-methods
        pass

    fake_jnius = types.SimpleNamespace(
        autoclass=lambda name: classes[name],
        PythonJavaClass=_FakePythonJavaClass,
        java_method=lambda _sig: lambda fn: fn,
    )

    def _fake_import(name: str) -> Any:
        if name == "jnius":
            return fake_jnius
        if name == "android.runnable":
            return types.SimpleNamespace(run_on_ui_thread=lambda fn: fn)
        raise ImportError(name)

    mocks = types.SimpleNamespace(
        settings=mock_settings,
        parent=mock_parent,
        wv=mock_wv,
        activity=mock_activity,
        client_cls=mock_client_cls,
        params_cls=mock_params_cls,
    )
    return mocks, _fake_import
