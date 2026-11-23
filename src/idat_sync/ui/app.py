"""Main pywebview application coordinator and JS API bridge."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.resources
import json
import mimetypes
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from idat_sync.bootstrap import create_auth_service
from idat_sync.config import WEBVIEW_USER_AGENT, is_android
from idat_sync.ui.base import AppFrame
from idat_sync.ui.login_frame import LoginFrame
from idat_sync.ui.sync_frame import SyncFrame

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from idat_sync.config import SyncConfig


def _folder_dialog_type() -> Any:  # noqa: ANN401
    webview = importlib.import_module("webview")
    return webview.FileDialog.FOLDER


def load_ui_html() -> str:
    """Load the bundled single-file SolidJS HTML UI asset."""
    return (
        importlib.resources.files("idat_sync.ui")
        .joinpath("index.html")
        .read_text(encoding="utf-8")
    )


class _JsApi:
    """JavaScript bridge exposing MainApp and active frame actions to pywebview."""

    def __init__(self, app: MainApp) -> None:
        self._app = app

    def _login_frame(self) -> LoginFrame | None:
        frame = self._app.active_frame
        return frame if isinstance(frame, LoginFrame) else None

    def _sync_frame(self) -> SyncFrame | None:
        frame = self._app.active_frame
        return frame if isinstance(frame, SyncFrame) else None

    def _schedule(self, coro: Coroutine[Any, Any, Any]) -> None:
        if self._app.loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._app.loop)
        else:
            coro.close()

    def _call_on_loop(self, func: Callable[..., Any], *args: Any) -> None:  # noqa: ANN401
        if self._app.loop.is_running():
            self._app.loop.call_soon_threadsafe(func, *args)
        else:
            func(*args)

    def get_state(self) -> dict[str, Any]:
        """Return the current serialized UI state snapshot."""
        return self._app.get_state()

    def login(self, username: str, password: str) -> None:
        """Start an async login attempt on the active LoginFrame."""
        if frame := self._login_frame():
            self._schedule(frame.do_login(username, password))

    def submit_mfa(self, code: str) -> None:
        """Resolve an open MFA prompt with the submitted code."""
        if frame := self._login_frame():
            self._call_on_loop(frame.submit_mfa, code)

    def cancel_mfa(self) -> None:
        """Cancel an open MFA prompt."""
        if frame := self._login_frame():
            self._call_on_loop(frame.cancel_mfa)

    def browse_folder(self) -> str:
        """Open a folder picker or apply the Android Downloads fallback."""
        if frame := self._sync_frame():
            return frame.browse_folder()
        return ""

    def select_in_progress(self) -> None:
        """Select only in-progress courses on SyncFrame."""
        if frame := self._sync_frame():
            frame.select_in_progress()

    def select_all(self) -> None:
        """Select all loaded courses on SyncFrame."""
        if frame := self._sync_frame():
            frame.select_all()

    def clear_selection(self) -> None:
        """Clear all selected courses on SyncFrame."""
        if frame := self._sync_frame():
            frame.clear_selection()

    def toggle_course(self, course_id: int | str) -> None:
        """Toggle selection for ``course_id`` on SyncFrame."""
        if frame := self._sync_frame():
            frame.toggle_course(int(course_id))

    def toggle_term(self, term: str) -> None:
        """Tri-state toggle all courses in ``term`` on SyncFrame."""
        if frame := self._sync_frame():
            frame.toggle_term(term)

    def logout(self) -> None:
        """Close SyncFrame resources and return to LoginFrame."""
        if frame := self._sync_frame():
            self._schedule(frame.do_logout())

    def sync(self) -> None:
        """Start course synchronization on SyncFrame."""
        if frame := self._sync_frame():
            self._schedule(frame.do_sync())

    def sync_course(self, course_id: int | str) -> None:
        """Start single-course synchronization on SyncFrame."""
        if frame := self._sync_frame():
            self._schedule(frame.sync_course(int(course_id)))

    def cancel_sync(self) -> None:
        """Cancel the active synchronization task on SyncFrame."""
        if frame := self._sync_frame():
            self._call_on_loop(frame.cancel_sync)

    def retry_load(self) -> None:
        """Retry startup course loading on SyncFrame."""
        if frame := self._sync_frame():
            self._schedule(frame.retry_load())

    def open_file(self, file_id: str) -> None:
        """Open a downloaded file in the native OS default application."""
        if frame := self._sync_frame():
            frame.open_file(file_id)


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
        return self._frame.to_dict() if self._frame is not None else {}

    def push_state(self) -> None:
        """Push the current JSON state snapshot to any attached WebView window."""
        if self.window is None:
            return
        payload = json.dumps(self.get_state())
        self.window.evaluate_js(f"window.__onPythonState({payload})")

    def push_progress(self, payload: dict[str, Any]) -> None:
        """Push a targeted per-file progress update to any attached WebView window."""
        if self.window is None:
            return
        encoded = json.dumps(payload)
        self.window.evaluate_js(f"window.__onFileProgress({encoded})")

    def pick_folder(self) -> str:
        """Open a desktop folder dialog or return the Android Downloads fallback."""
        if (
            is_android()
            or self.window is None
            or not hasattr(self.window, "create_file_dialog")
        ):
            return str(
                Path(os.environ.get("EXTERNAL_STORAGE", "/storage/emulated/0"))
                / "Download"
                / "idat-sync"
            )
        selected = self.window.create_file_dialog(
            _folder_dialog_type(),
            directory=str(Path.home()),
        )
        if not selected:
            return ""
        return selected[0] if isinstance(selected, (list, tuple)) else str(selected)

    def open_path(self, path: Path) -> None:
        """Launch ``path`` in the native OS default application."""
        if is_android():
            try:
                jnius = importlib.import_module("jnius")
                activity = jnius.autoclass("org.kivy.android.PythonActivity").mActivity
                java_file = jnius.autoclass("java.io.File")(str(path))
                file_provider = jnius.autoclass("androidx.core.content.FileProvider")
                uri = file_provider.getUriForFile(
                    activity,
                    f"{activity.getPackageName()}.fileprovider",
                    java_file,
                )
                intent_cls = jnius.autoclass("android.content.Intent")
                intent = intent_cls(intent_cls.ACTION_VIEW)
                mime_type = mimetypes.guess_type(path.name)[0] or "*/*"
                intent.setDataAndType(uri, mime_type)
                intent.addFlags(intent_cls.FLAG_GRANT_READ_URI_PERMISSION)
                activity.startActivity(intent)
            except Exception as exc:
                raise OSError(str(exc)) from exc
            return
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])  # noqa: S603, S607
        else:
            subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607

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

    async def on_close(self, *, destroy_window: bool = True) -> None:
        """Close the active frame cleanly before destroying the WebView window."""
        if self._closing:
            return
        self._closing = True
        try:
            if self._frame is not None:
                await self._frame.aclose()
        finally:
            if destroy_window and self.window is not None:
                self.window.destroy()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _on_window_closing(self) -> None:
        if self.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self.on_close(destroy_window=False), self.loop
            )
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
                    width=1100,
                    height=720,
                    min_size=(400, 500),
                )
            events = getattr(self.window, "events", None)
            if events is not None and hasattr(events, "closing"):
                events.closing += self._on_window_closing
            if isinstance(self._frame, SyncFrame) and not self._frame.courses_loaded:
                asyncio.run_coroutine_threadsafe(
                    self._frame.load_idat_sync(self._frame.credentials), self.loop
                )
            webview.start(user_agent=WEBVIEW_USER_AGENT)
        finally:
            self._on_window_closing()
            if self.loop.is_running():
                self.loop.call_soon_threadsafe(self.loop.stop)
            thread.join(timeout=5)
            if not self.loop.is_closed():
                self.loop.close()


__all__ = ["AppFrame", "MainApp", "load_ui_html"]
