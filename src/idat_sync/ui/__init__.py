"""Tkinter desktop UI package for idat_sync."""

from idat_sync.ui.app import AppFrame, MainApp, main
from idat_sync.ui.login_frame import LoginFrame
from idat_sync.ui.sync_frame import SyncFrame

__all__ = [
    "AppFrame",
    "LoginFrame",
    "MainApp",
    "SyncFrame",
    "main",
]
