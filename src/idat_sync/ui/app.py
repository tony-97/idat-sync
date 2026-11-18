"""Main Tkinter application window and frame navigation."""

from __future__ import annotations

import asyncio
import tkinter as tk
from typing import TYPE_CHECKING, Any

from async_tkinter_loop import async_handler, async_mainloop

from idat_sync.bootstrap import create_auth_service
from idat_sync.ui.base import AppFrame
from idat_sync.ui.login_frame import LoginFrame
from idat_sync.ui.sync_frame import SyncFrame

if TYPE_CHECKING:
    from idat_sync.config import SyncConfig


class MainApp(tk.Tk):
    """Root application window managing LoginFrame and SyncFrame transitions."""

    def __init__(self, config: SyncConfig) -> None:
        """Initialize the main window and navigate to the initial frame."""
        tk.Tk.__init__(self)
        self.loop = asyncio.new_event_loop()
        self.config = config
        self.auth = create_auth_service(self.config)
        self._frame: AppFrame | None = None
        self._closing = False
        self.protocol("WM_DELETE_WINDOW", async_handler(self.on_close))
        if self.auth.is_authenticated():
            self.switch_to_sync()
        else:
            self.switch_to_login()

    async def on_close(self) -> None:
        """Close the active frame cleanly before destroying the root window."""
        if self._closing:
            return
        self._closing = True
        try:
            if self._frame is not None:
                await self._frame.aclose()
        finally:
            self.destroy()

    def run(self) -> None:
        """Start the async Tkinter main event loop."""
        async_mainloop(self, self.loop)

    def center_window(self, window_width: int, window_height: int) -> None:
        """Center the window on screen with the given dimensions."""
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        center_x = int(screen_width / 2 - window_width / 2)
        center_y = int(screen_height / 2 - window_height / 2)
        self.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")

    def _show(self, frame: type[AppFrame], **kwargs: Any) -> None:  # noqa: ANN401
        if self._frame:
            self._frame.destroy()
        self._frame = frame(self, **kwargs)
        self._frame.pack()

    def switch_to_sync(self) -> None:
        """Switch the active view to SyncFrame when Credentials exist."""
        if credentials := self.auth.get_credentials():
            self._show(SyncFrame, credentials=credentials, config=self.config)

    def switch_to_login(self) -> None:
        """Switch the active view to LoginFrame."""
        self._show(LoginFrame, auth=self.auth)


__all__ = ["AppFrame", "MainApp"]
