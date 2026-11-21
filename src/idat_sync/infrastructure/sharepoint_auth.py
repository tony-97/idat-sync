"""WebView-driven SharePoint/Microsoft Entra ID SSO and MFA authentication."""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import importlib
import json
import logging
import subprocess
import sys
import time
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, TextIO
from urllib.parse import urlparse

from idat_sync.application.ports import (
    AuthenticationError,
    CookieData,
    StorageStateData,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_logger = logging.getLogger(__name__)

_GALAXY_A55_UA = (
    "Mozilla/5.0 (Linux; Android 14; SM-A556E) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)
_POLL_INTERVAL_S = 0.15
_TIMEOUT_S = 180.0
_SETTLE_SLEEP_S = 3


class WebViewDriver(Protocol):
    """Protocol abstracting a hidden WebView for SharePoint SSO automation."""

    async def goto(self, url: str) -> None:
        """Navigate the WebView to ``url``."""
        ...

    async def get_url(self) -> str:
        """Return the current WebView URL."""
        ...

    async def evaluate_js(self, script: str) -> object:
        """Execute ``script`` in the WebView and return the result."""
        ...

    async def get_cookies(self) -> list[CookieData]:
        """Return cookies scoped to the currently loaded URL."""
        ...

    async def aclose(self) -> None:
        """Release and destroy the underlying WebView."""
        ...


def _parse_simple_cookies(
    raw_cookies: list[Any], fallback_domain: str
) -> list[CookieData]:
    """Convert pywebview SimpleCookie items into CookieData entries."""
    result: list[CookieData] = []
    for simple_cookie in raw_cookies:
        for name, morsel in simple_cookie.items():
            domain = morsel["domain"] or fallback_domain
            path = morsel["path"] or "/"
            result.append(
                {
                    "name": name,
                    "value": morsel.value,
                    "domain": domain,
                    "path": path,
                    "secure": bool(morsel["secure"]),
                }
            )
    return result


def _cookies_for_url(raw_cookies: list[Any], url: str) -> list[CookieData]:
    return _parse_simple_cookies(raw_cookies, urlparse(url).hostname or "")


def _dispatch_worker_cmd(window: Any, req: dict[str, Any]) -> object:  # noqa: ANN401
    cmd = req.get("cmd")
    if cmd == "goto":
        window.load_url(req["url"])
        return None
    if cmd == "get_url":
        return str(window.get_current_url() or "")
    if cmd == "evaluate_js":
        return window.evaluate_js(req["script"])
    if cmd == "get_cookies":
        return _cookies_for_url(
            window.get_cookies() or [], str(window.get_current_url() or "")
        )
    raise ValueError(f"Unknown standalone worker command: {cmd!r}")


def _run_standalone_worker(initial_url: str, stdin: TextIO, stdout: TextIO) -> None:
    """Run a hidden pywebview window on the main thread controlled via JSON lines."""
    webview = importlib.import_module("webview")
    window = webview.create_window("", initial_url, hidden=True)

    def _serve(win: Any) -> None:  # noqa: ANN401
        try:
            win.hide()
            stdout.write(json.dumps({"ok": True}) + "\n")
            stdout.flush()
            for raw_line in stdin:
                line = raw_line.strip()
                if not line:
                    continue
                req = json.loads(line)
                if req.get("cmd") == "close":
                    stdout.write(json.dumps({"ok": True}) + "\n")
                    stdout.flush()
                    break
                try:
                    result = _dispatch_worker_cmd(win, req)
                    resp: dict[str, Any] = {"ok": True, "result": result}
                except Exception as exc:
                    resp = {"ok": False, "error": str(exc)}
                stdout.write(json.dumps(resp) + "\n")
                stdout.flush()
        finally:
            win.destroy()

    webview.start(_serve, window, user_agent=_GALAXY_A55_UA)


class _WindowBackend(Protocol):
    """Sync backend protocol for in-process vs subprocess pywebview control."""

    def goto(self, url: str) -> None:
        """Navigate to ``url``."""
        ...

    def get_url(self) -> str:
        """Return the current URL."""
        ...

    def evaluate_js(self, script: str) -> object:
        """Evaluate ``script`` and return its result."""
        ...

    def get_cookies(self) -> list[CookieData]:
        """Return cookies for the current URL."""
        ...

    def close(self) -> None:
        """Release the underlying window or worker."""
        ...


class _InProcessWindow:
    """Controls a hidden secondary window inside an active pywebview GUI loop."""

    def __init__(self, window: Any) -> None:  # noqa: ANN401
        self._window = window

    def goto(self, url: str) -> None:
        """Load ``url`` in the secondary window."""
        self._window.load_url(url)

    def get_url(self) -> str:
        """Return the current URL of the secondary window."""
        return str(self._window.get_current_url() or "")

    def evaluate_js(self, script: str) -> object:
        """Evaluate JavaScript in the secondary window."""
        return self._window.evaluate_js(script)

    def get_cookies(self) -> list[CookieData]:
        """Return cookies for the secondary window's current URL."""
        return _cookies_for_url(self._window.get_cookies() or [], self.get_url())

    def close(self) -> None:
        """Destroy the secondary window."""
        self._window.destroy()


class _SubprocessWorker:
    """Controls a standalone pywebview worker subprocess via JSON lines."""

    def __init__(self, initial_url: str) -> None:
        # pywebview.start() requires the main thread; isolate standalone CLI auth
        # in a child process so asyncio.run() can remain on the caller's main thread.
        self._proc = subprocess.Popen(  # noqa: S603  # pylint: disable=consider-using-with
            [sys.executable, "-m", __name__, initial_url],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self._read_response()

    def _read_response(self) -> Any:  # noqa: ANN401
        if self._proc.stdout is None:
            raise RuntimeError("Standalone pywebview worker is not running")
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("Standalone pywebview worker exited unexpectedly")
        resp = json.loads(line)
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "Standalone pywebview worker failed"))
        return resp.get("result")

    def _rpc(self, payload: dict[str, Any]) -> Any:  # noqa: ANN401
        if self._proc.stdin is None:
            raise RuntimeError("Standalone pywebview worker is not running")
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()
        return self._read_response()

    def goto(self, url: str) -> None:
        """Load ``url`` in the worker process."""
        self._rpc({"cmd": "goto", "url": url})

    def get_url(self) -> str:
        """Return the current URL from the worker process."""
        return str(self._rpc({"cmd": "get_url"}) or "")

    def evaluate_js(self, script: str) -> object:
        """Evaluate JavaScript in the worker process."""
        return self._rpc({"cmd": "evaluate_js", "script": script})

    def get_cookies(self) -> list[CookieData]:
        """Return cookies from the worker process."""
        return self._rpc({"cmd": "get_cookies"})

    def close(self) -> None:
        """Stop the standalone worker subprocess cleanly."""
        proc = self._proc
        try:
            if proc.stdin is not None and not proc.stdin.closed:
                with contextlib.suppress(OSError):
                    proc.stdin.write(json.dumps({"cmd": "close"}) + "\n")
                    proc.stdin.flush()
                    proc.stdin.close()
            if proc.stdout is not None and not proc.stdout.closed:
                with contextlib.suppress(OSError):
                    proc.stdout.close()
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)


class PyWebviewDriver:
    """Desktop WebViewDriver backed by a hidden pywebview window."""

    def __init__(self) -> None:
        """Initialize the desktop driver with no active window."""
        self._backend: _WindowBackend | None = None
        self._current_url = ""

    def _ensure_backend(self, url: str) -> None:
        if self._backend is not None:
            self._backend.goto(url)
            return
        webview = importlib.import_module("webview")
        if getattr(webview, "windows", None):
            window = webview.create_window("", url, hidden=True)
            # Linux GTK dynamic secondary windows ignore hidden=True once running.
            window.hide()
            self._backend = _InProcessWindow(window)
            return
        self._backend = _SubprocessWorker(url)

    async def goto(self, url: str) -> None:
        """Open or navigate the hidden pywebview window to ``url``."""
        self._current_url = url
        await asyncio.to_thread(self._ensure_backend, url)

    async def get_url(self) -> str:
        """Return the current URL of the pywebview window."""
        if self._backend is None:
            return self._current_url
        url = await asyncio.to_thread(self._backend.get_url)
        if url:
            self._current_url = url
        return self._current_url

    async def evaluate_js(self, script: str) -> object:
        """Evaluate JavaScript in the hidden pywebview window."""
        if self._backend is None:
            return None
        return await asyncio.to_thread(self._backend.evaluate_js, script)

    async def get_cookies(self) -> list[CookieData]:
        """Return cookies for the current URL from pywebview."""
        if self._backend is None:
            return []
        return await asyncio.to_thread(self._backend.get_cookies)

    async def aclose(self) -> None:
        """Destroy the hidden pywebview window or stop the standalone worker."""
        backend, self._backend = self._backend, None
        if backend is not None:
            await asyncio.to_thread(backend.close)


def _parse_cookie_header(header: str, domain: str) -> list[CookieData]:
    """Parse an Android CookieManager header string into CookieData entries."""
    cookies: list[CookieData] = []
    for raw_pair in header.split(";"):
        pair = raw_pair.strip()
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        cookies.append(
            {
                "name": name.strip(),
                "value": value.strip(),
                "domain": domain,
                "path": "/",
                "secure": True,
            }
        )
    return cookies


class AndroidWebViewDriver:
    """Android WebViewDriver using a 1x1 INVISIBLE native WebView via pyjnius."""

    def __init__(self) -> None:
        """Initialize the Android driver with no attached WebView."""
        self._webview: Any | None = None
        self._current_url = ""

    @staticmethod
    def _run_on_ui_thread(func: Callable[[], object]) -> object:
        runnable_mod = importlib.import_module("android.runnable")
        done: Future[object] = Future()

        def _wrapped() -> None:
            try:
                done.set_result(func())
            except Exception as exc:
                done.set_exception(exc)

        runnable_mod.run_on_ui_thread(_wrapped)()
        return done.result(timeout=_TIMEOUT_S)

    def _create_webview_on_ui(self) -> object:
        jnius = importlib.import_module("jnius")
        autoclass = jnius.autoclass
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        webview_cls = autoclass("android.webkit.WebView")
        client_cls = autoclass("android.webkit.WebViewClient")
        view_cls = autoclass("android.view.View")
        params_cls = autoclass("android.view.ViewGroup$LayoutParams")

        wv = webview_cls(activity)
        settings = wv.getSettings()
        settings.setJavaScriptEnabled(True)
        settings.setDomStorageEnabled(True)
        settings.setUserAgentString(_GALAXY_A55_UA)
        # Standard WebViewClient avoids PyWebViewClient POST body stripping.
        wv.setWebViewClient(client_cls())
        # Keep View.INVISIBLE (not View.GONE) so JS timers stay active.
        wv.setVisibility(view_cls.INVISIBLE)
        activity.addContentView(wv, params_cls(1, 1))
        return wv

    async def goto(self, url: str) -> None:
        """Ensure the 1x1 invisible WebView exists and load ``url``."""
        self._current_url = url

        def _load() -> None:
            if self._webview is None:
                self._webview = self._create_webview_on_ui()
            self._webview.loadUrl(url)

        await asyncio.to_thread(self._run_on_ui_thread, _load)

    async def get_url(self) -> str:
        """Return the current URL from the Android WebView."""
        if self._webview is None:
            return self._current_url
        wv = self._webview
        url = await asyncio.to_thread(self._run_on_ui_thread, wv.getUrl)
        if url:
            self._current_url = str(url)
        return self._current_url

    async def evaluate_js(self, script: str) -> object:
        """Evaluate JavaScript via WebView.evaluateJavascript on the UI thread."""
        if self._webview is None:
            return None
        wv = self._webview
        result_future: Future[object] = Future()
        # Hold a strong reference so pyjnius cannot GC the Java proxy before callback.
        callback_ref: list[object] = []

        def _eval() -> None:
            jnius = importlib.import_module("jnius")

            class _Callback(jnius.PythonJavaClass):  # pylint: disable=too-few-public-methods
                __javainterfaces__: ClassVar[list[str]] = [
                    "android/webkit/ValueCallback"
                ]

                @jnius.java_method("(Ljava/lang/Object;)V")
                def onReceiveValue(self, value: object) -> None:  # noqa: N802  # pylint: disable=invalid-name
                    """Resolve the future when Android returns a JS result."""
                    if not result_future.done():
                        result_future.set_result(value)

            callback = _Callback()
            callback_ref.append(callback)
            wv.evaluateJavascript(script, callback)

        await asyncio.to_thread(self._run_on_ui_thread, _eval)
        raw = await asyncio.to_thread(result_future.result, _TIMEOUT_S)
        callback_ref.clear()
        if raw is None or raw in {"null", "undefined"}:
            return None
        try:
            return json.loads(str(raw))
        except json.JSONDecodeError:
            return str(raw)

    async def get_cookies(self) -> list[CookieData]:
        """Read HttpOnly cookies for the current URL via Android CookieManager."""
        current_url = await self.get_url()
        if not current_url:
            return []

        def _read_cookie_header() -> str:
            jnius = importlib.import_module("jnius")
            cm = jnius.autoclass("android.webkit.CookieManager").getInstance()
            return str(cm.getCookie(current_url) or "")

        header = await asyncio.to_thread(self._run_on_ui_thread, _read_cookie_header)
        domain = urlparse(current_url).hostname or ""
        return _parse_cookie_header(header, domain)

    async def aclose(self) -> None:
        """Detach and destroy the Android WebView on the UI thread."""
        wv = self._webview
        self._webview = None
        if wv is None:
            return

        def _destroy() -> None:
            parent = wv.getParent()
            if parent is not None:
                parent.removeView(wv)
            wv.destroy()

        await asyncio.to_thread(self._run_on_ui_thread, _destroy)


def _js_visible_el(selector: str, body: str) -> str:
    sel_json = json.dumps(selector)
    return (
        f"(() => {{"
        f"for (const el of document.querySelectorAll({sel_json})) {{"
        f"const r = el.getBoundingClientRect();"
        f"if (r.width > 0 && r.height > 0) {{"
        f"{body}"
        f"return true;"
        f"}}"
        f"}}"
        f"return false;"
        f"}})()"
    )


def _js_is_visible(selector: str) -> str:
    return _js_visible_el(selector, "")


def _js_fill(selector: str, value: str) -> str:
    val_json = json.dumps(value)
    return _js_visible_el(
        selector,
        f"const setter = Object.getOwnPropertyDescriptor("
        f"window.HTMLInputElement.prototype, 'value').set;"
        f"setter.call(el, {val_json});"
        f"el.dispatchEvent(new Event('input', {{ bubbles: true }}));"
        f"el.dispatchEvent(new Event('change', {{ bubbles: true }}));",
    )


def _js_click(selector: str) -> str:
    return _js_visible_el(selector, "el.click();")


def _url_matches(url: str, pattern: str) -> bool:
    if not url:
        return False
    base_url = url.split("?", 1)[0]
    glob = pattern.replace("**", "*")
    return fnmatch.fnmatch(url, glob) or fnmatch.fnmatch(base_url, glob)


def _merge_cookies(
    academic: list[CookieData], personal: list[CookieData]
) -> list[CookieData]:
    merged: dict[tuple[str, str, str], CookieData] = {}
    for cookie in (*academic, *personal):
        key = (
            cookie.get("domain", ""),
            cookie.get("path", "/"),
            cookie.get("name", ""),
        )
        merged[key] = cookie
    return list(merged.values())


async def _poll_until(
    predicate: Callable[[], Awaitable[bool]], description: str
) -> None:
    deadline = time.monotonic() + _TIMEOUT_S
    while True:
        if await predicate():
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for {description}")
        await asyncio.sleep(_POLL_INTERVAL_S)


async def _wait_for_url(driver: WebViewDriver, pattern: str) -> None:
    await _poll_until(
        lambda: _check_url(driver, pattern),
        f"URL {pattern}",
    )


async def _check_url(driver: WebViewDriver, pattern: str) -> bool:
    return _url_matches(await driver.get_url(), pattern)


async def _wait_for_selector(driver: WebViewDriver, selector: str) -> None:
    script = _js_is_visible(selector)
    await _poll_until(
        lambda: _eval_bool(driver, script),
        f"selector {selector}",
    )


async def _eval_bool(driver: WebViewDriver, script: str) -> bool:
    return bool(await driver.evaluate_js(script))


async def _fill(driver: WebViewDriver, selector: str, value: str) -> None:
    script = _js_fill(selector, value)
    await _poll_until(lambda: _eval_bool(driver, script), f"fill {selector}")


async def _click(driver: WebViewDriver, selector: str) -> None:
    script = _js_click(selector)
    await _poll_until(lambda: _eval_bool(driver, script), f"click {selector}")


class SharepointBrowserAuth:  # pylint: disable=too-few-public-methods
    """WebView SSO/MFA authenticator implementing SharepointAuthPort."""

    def __init__(
        self,
        sharepoint_host: str,
        sharepoint_personal_host: str,
        email_domain: str,
        driver_factory: Callable[[], WebViewDriver],
    ) -> None:
        """Initialize the authenticator with SharePoint hosts and email domain."""
        self.sharepoint_host = sharepoint_host
        self.sharepoint_personal_host = sharepoint_personal_host
        self.email_domain = email_domain
        self._driver_factory = driver_factory

    async def _complete_login(
        self,
        driver: WebViewDriver,
        site_url: str,
        login_email: str,
        password: str,
        get_mfa_code: Callable[[], Awaitable[str]],
    ) -> StorageStateData:
        await driver.goto(site_url)
        await _wait_for_url(driver, "https://login.microsoftonline.com/**")
        await _fill(driver, "#i0116", login_email)
        await _click(driver, "#idSIButton9")

        await _fill(driver, "#i0118", password)
        await _click(driver, "#idSIButton9")
        await _wait_for_url(driver, "https://login.microsoftonline.com/*/login")
        await _click(driver, "#idDiv_SAOTCS_Proofs > div:nth-child(2)")

        mfa_code = (await get_mfa_code()).strip()
        if not mfa_code:
            raise AuthenticationError("MFA code is required")
        await _fill(driver, "#idTxtBx_SAOTCC_OTC", mfa_code)
        await _click(driver, "#idChkBx_SAOTCC_TD")
        await _click(driver, "#idSubmit_SAOTCC_Continue")

        await _wait_for_url(
            driver, "https://login.microsoftonline.com/common/SAS/ProcessAuth"
        )
        await _wait_for_selector(driver, "#skipMfaRegistrationLink, #KmsiCheckboxField")
        if await _eval_bool(driver, _js_is_visible("#skipMfaRegistrationLink")):
            await _click(driver, "#skipMfaRegistrationLink")
        await _click(driver, "#KmsiCheckboxField")
        await _click(driver, "#idSIButton9")

        await _wait_for_url(driver, site_url)
        await asyncio.sleep(_SETTLE_SLEEP_S)
        academic_cookies = await driver.get_cookies()

        await driver.goto(f"https://{self.sharepoint_personal_host}/shared")
        await asyncio.sleep(_SETTLE_SLEEP_S)
        personal_cookies = await driver.get_cookies()
        return {"cookies": _merge_cookies(academic_cookies, personal_cookies)}

    async def fetch_storage_state(
        self,
        username: str,
        password: str,
        get_mfa_code: Callable[[], Awaitable[str]],
    ) -> StorageStateData:
        """Authenticate via hidden WebView/MFA and return SharePoint storage state."""
        site_url = f"https://{self.sharepoint_host}/_layouts/15/sharepoint.aspx"
        login_email = username if "@" in username else f"{username}@{self.email_domain}"
        driver: WebViewDriver | None = None
        cancelled = False
        try:
            try:
                driver = self._driver_factory()
                return await self._complete_login(
                    driver, site_url, login_email, password, get_mfa_code
                )
            except asyncio.CancelledError:
                cancelled = True
                raise
            finally:
                if driver is not None:
                    try:
                        await driver.aclose()
                    except Exception:
                        if not cancelled:
                            raise
        except AuthenticationError:
            raise
        except TimeoutError as exc:
            _logger.exception("SharePoint sign-in timed out")
            raise AuthenticationError("SharePoint sign-in timed out.") from exc
        except Exception as exc:
            _logger.exception("SharePoint browser authentication failed")
            raise AuthenticationError(
                "SharePoint browser authentication failed"
            ) from exc


if __name__ == "__main__" and len(sys.argv) > 1:
    _run_standalone_worker(sys.argv[1], sys.stdin, sys.stdout)
