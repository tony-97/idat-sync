"""Unit tests for WebView-driven SharePoint SSO/MFA authentication."""

from __future__ import annotations

import asyncio
import inspect
import os
import types
import unittest
from http.cookies import SimpleCookie
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from idat_sync.application.ports import AuthenticationError, CookieData
from idat_sync.infrastructure.sharepoint_auth import (
    _GALAXY_A55_UA,
    AndroidWebViewDriver,
    PyWebviewDriver,
    SharepointBrowserAuth,
    _create_default_driver,
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
                    driver_factory=lambda d=driver: d,
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

    def test_blank_mfa_raises_authentication_error_and_closes_driver(self) -> None:
        """Raise AuthenticationError without filling OTP when MFA code is blank."""
        driver = FakeWebViewDriver()
        auth = SharepointBrowserAuth(
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            email_domain="example.edu",
            driver_factory=lambda: driver,
        )

        async def _blank_mfa() -> str:
            return "   "

        with (
            patch(
                "idat_sync.infrastructure.sharepoint_auth.asyncio.sleep",
                new=AsyncMock(),
            ),
            pytest.raises(AuthenticationError, match="MFA code is required"),
        ):
            asyncio.run(auth.fetch_storage_state("jdoe", "pw", _blank_mfa))

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
            driver_factory=lambda: timeout_driver,
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
        assert any(r.exc_info is not None for r in log_cm.records)
        assert timeout_driver.closed is True

        # Runtime error
        err_driver = FakeWebViewDriver()
        err_driver.goto = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
        auth_err = SharepointBrowserAuth(
            sharepoint_host="contoso.sharepoint.com",
            sharepoint_personal_host="contoso-my.sharepoint.com",
            email_domain="example.edu",
            driver_factory=lambda: err_driver,
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
            driver_factory=lambda: cancel_driver,
        )
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(auth_cancel.fetch_storage_state("jdoe", "pw", _mfa_code))
        cancel_driver.aclose.assert_awaited_once()


class TestDriverSelectionAndAdapters(unittest.TestCase):
    """Tests for default driver selection, PyWebviewDriver, and AndroidWebViewDriver."""

    def test_create_default_driver_selects_platform_adapter(self) -> None:
        """Select AndroidWebViewDriver on Android env markers, PyWebviewDriver else."""
        with patch.dict(os.environ, {}, clear=True):
            assert isinstance(_create_default_driver(), PyWebviewDriver)
        with patch.dict(os.environ, {"ANDROID_ARGUMENT": "1"}, clear=True):
            assert isinstance(_create_default_driver(), AndroidWebViewDriver)
        with patch.dict(os.environ, {"ANDROID_BOOTLOGO": "1"}, clear=True):
            assert isinstance(_create_default_driver(), AndroidWebViewDriver)

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
            create_window=MagicMock(return_value=mock_window)
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

        with patch(
            "idat_sync.infrastructure.sharepoint_auth.importlib.import_module",
            return_value=fake_webview,
        ):
            cookies = asyncio.run(_exercise())

        fake_webview.create_window.assert_called_once_with(
            "",
            "https://contoso.sharepoint.com/_layouts/15/sharepoint.aspx",
            hidden=True,
            user_agent=_GALAXY_A55_UA,
        )
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

    def test_android_webview_driver_lifecycle_and_cookie_manager(self) -> None:
        """Configure 1x1 INVISIBLE WebView, read CookieManager, and destroy."""
        mocks, fake_import = _make_fake_android_env()

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
        mocks.settings.setUserAgentString.assert_called_once_with(_GALAXY_A55_UA)
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
