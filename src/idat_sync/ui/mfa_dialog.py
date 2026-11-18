"""Modal Tkinter popup dialog for entering a 6-digit MFA code."""

from __future__ import annotations

import asyncio
import tkinter as tk

_DIALOG_GEOMETRY = "250x150"


async def ask_mfa_code(parent: tk.Misc) -> str:
    """Display a modal MFA popup and await the submitted 6-digit code."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str] = loop.create_future()

    popup = tk.Toplevel(parent)
    popup.title("MFA Required")
    popup.geometry(_DIALOG_GEOMETRY)

    tk.Label(popup, text="Enter your 6-digit MFA code:").pack(pady=10)

    entry = tk.Entry(popup)
    entry.pack(pady=5)

    def on_submit() -> None:
        if not future.done():
            future.set_result(entry.get())
        popup.destroy()

    def on_close() -> None:
        if not future.done():
            future.set_result("")
        popup.destroy()

    entry.bind("<Return>", lambda _e: on_submit())
    popup.protocol("WM_DELETE_WINDOW", on_close)
    tk.Button(popup, text="Submit", command=on_submit).pack(pady=10)

    try:
        return await future
    finally:
        if popup.winfo_exists():
            popup.destroy()


__all__ = ["ask_mfa_code"]
