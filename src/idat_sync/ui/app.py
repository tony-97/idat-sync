"""Main pywebview application coordinator and JS API bridge."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.resources
import json
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from idat_sync.bootstrap import create_auth_service
from idat_sync.config import MOBILE_WEBVIEW_USER_AGENT
from idat_sync.ui.base import AppFrame
from idat_sync.ui.login_frame import LoginFrame
from idat_sync.ui.sync_frame import SyncFrame

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from idat_sync.config import SyncConfig

_GALAXY_A55_UA = MOBILE_WEBVIEW_USER_AGENT
_FALLBACK_FOLDER_DIALOG = 20


def _is_android() -> bool:
    return "ANDROID_ARGUMENT" in os.environ or "ANDROID_BOOTLOGO" in os.environ


def _folder_dialog_type() -> Any:  # noqa: ANN401
    try:
        webview = importlib.import_module("webview")
    except ImportError:
        return _FALLBACK_FOLDER_DIALOG
    file_dialog = getattr(webview, "FileDialog", None)
    if file_dialog is not None and hasattr(file_dialog, "FOLDER"):
        return file_dialog.FOLDER
    return getattr(webview, "FOLDER_DIALOG", _FALLBACK_FOLDER_DIALOG)


def load_ui_html() -> str:
    """Load the bundled single-file static Astro HTML UI asset."""
    return (
        importlib.resources.files("idat_sync.ui")
        .joinpath("index.html")
        .read_text(encoding="utf-8")
    )


class _JsApi:
    """JavaScript bridge exposing MainApp and active frame actions to pywebview."""

    def __init__(self, app: MainApp) -> None:
        self._app = app

    def _schedule(self, coro: Coroutine[Any, Any, Any]) -> None:
        if self._app.loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._app.loop)
        else:
            coro.close()

    def get_state(self) -> dict[str, Any]:
        """Return the current serialized UI state snapshot."""
        return self._app.get_state()

    def login(self, username: str, password: str) -> None:
        """Start an async login attempt on the active LoginFrame."""
        if isinstance(frame := self._app.active_frame, LoginFrame):
            self._schedule(frame.do_login(username, password))

    def submit_mfa(self, code: str) -> None:
        """Resolve an open MFA prompt with the submitted code."""
        if isinstance(frame := self._app.active_frame, LoginFrame):
            if self._app.loop.is_running():
                self._app.loop.call_soon_threadsafe(frame.submit_mfa, code)
            else:
                frame.submit_mfa(code)

    def cancel_mfa(self) -> None:
        """Cancel an open MFA prompt."""
        if isinstance(frame := self._app.active_frame, LoginFrame):
            if self._app.loop.is_running():
                self._app.loop.call_soon_threadsafe(frame.cancel_mfa)
            else:
                frame.cancel_mfa()

    def browse_folder(self) -> str:
        """Open a folder picker or apply the Android Downloads fallback."""
        if isinstance(frame := self._app.active_frame, SyncFrame):
            return frame.browse_folder()
        return ""

    def select_in_progress(self) -> None:
        """Select only in-progress courses on SyncFrame."""
        if isinstance(frame := self._app.active_frame, SyncFrame):
            frame.select_in_progress()

    def select_all(self) -> None:
        """Select all loaded courses on SyncFrame."""
        if isinstance(frame := self._app.active_frame, SyncFrame):
            frame.select_all()

    def clear_selection(self) -> None:
        """Clear all selected courses on SyncFrame."""
        if isinstance(frame := self._app.active_frame, SyncFrame):
            frame.clear_selection()

    def toggle_course(self, course_id: int | str) -> None:
        """Toggle selection for ``course_id`` on SyncFrame."""
        if isinstance(frame := self._app.active_frame, SyncFrame):
            frame.toggle_course(int(course_id))

    def logout(self) -> None:
        """Close SyncFrame resources and return to LoginFrame."""
        if isinstance(frame := self._app.active_frame, SyncFrame):
            self._schedule(frame.do_logout())

    def sync(self) -> None:
        """Start course synchronization on SyncFrame."""
        if isinstance(frame := self._app.active_frame, SyncFrame):
            self._schedule(frame.do_sync())


class MainApp:
    """Root application coordinator managing LoginFrame, SyncFrame, and WebView."""

    def __init__(self, config: SyncConfig, window: Any | None = None) -> None:  # noqa: ANN401
        """Initialize event loop, auth service, and initial frame."""
        self.loop = asyncio.new_event_loop()
        self.config = config
        self.auth = create_auth_service(self.config)
        self.window = window
        self._frame: AppFrame | None = None
        self._closing = False
        if self.auth.is_authenticated():
            self.switch_to_sync()
        else:
            self.switch_to_login()

    @property
    def active_frame(self) -> AppFrame | None:
        """Return the currently active component controller."""
        return self._frame

    def get_state(self) -> dict[str, Any]:
        """Serialize the active component controller's state for the frontend."""
        if self._frame is not None and hasattr(self._frame, "to_dict"):
            return self._frame.to_dict()
        return {}

    def push_state(self) -> None:
        """Push the current JSON state snapshot to any attached WebView window."""
        if self.window is None:
            return
        payload = json.dumps(self.get_state())
        self.window.evaluate_js(f"window.__onPythonState({payload})")

    def pick_folder(self) -> str | None:
        """Open a desktop folder dialog; return '' on cancel or None if unavailable."""
        if (
            _is_android()
            or self.window is None
            or not hasattr(self.window, "create_file_dialog")
        ):
            return None
        selected = self.window.create_file_dialog(
            _folder_dialog_type(),
            directory=str(Path.home()),
        )
        if not selected:
            return ""
        return selected[0] if isinstance(selected, (list, tuple)) else str(selected)

    def switch_to_sync(self) -> None:
        """Switch the active view to SyncFrame when Credentials exist."""
        if credentials := self.auth.get_credentials():
            frame = SyncFrame(self, credentials=credentials, config=self.config)
            self._frame = frame
            self.push_state()
            if self.loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    frame.load_idat_sync(credentials), self.loop
                )

    def switch_to_login(self) -> None:
        """Switch the active view to LoginFrame."""
        self._frame = LoginFrame(self, auth=self.auth)
        self.push_state()

    async def on_close(self) -> None:
        """Close the active frame cleanly before destroying the WebView window."""
        if self._closing:
            return
        self._closing = True
        try:
            if self._frame is not None:
                await self._frame.aclose()
        finally:
            if self.window is not None:
                self.window.destroy()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _on_window_closing(self) -> None:
        if self.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self.on_close(), self.loop)
            with contextlib.suppress(Exception):
                future.result(timeout=5)

    def run(self) -> None:
        """Start the background asyncio thread and run the pywebview GUI loop."""
        webview = importlib.import_module("webview")
        thread = threading.Thread(target=self._run_loop, daemon=True)
        thread.start()
        try:
            if self.window is None:
                self.window = webview.create_window(
                    "IDAT Sync",
                    html=load_ui_html(),
                    js_api=_JsApi(self),
                    width=760,
                    height=560,
                )
            events = getattr(self.window, "events", None)
            if events is not None and hasattr(events, "closing"):
                events.closing += self._on_window_closing
            if isinstance(self._frame, SyncFrame) and not self._frame.courses_loaded:
                asyncio.run_coroutine_threadsafe(
                    self._frame.load_idat_sync(self._frame.credentials), self.loop
                )
            webview.start(user_agent=_GALAXY_A55_UA)
        finally:
            self._on_window_closing()
            if self.loop.is_running():
                self.loop.call_soon_threadsafe(self.loop.stop)
            thread.join(timeout=5)
            if not self.loop.is_closed():
                self.loop.close()


__all__ = ["AppFrame", "MainApp", "load_ui_html"]
