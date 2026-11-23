"""Pure-Python formatter translating IDATSyncError exceptions into UI summaries."""

from __future__ import annotations

from idat_sync.application.exceptions import (
    AuthenticationError,
    AuthFailureReason,
    CredentialsExpiredError,
    CredentialStoreError,
    FileSystemError,
    IDATSyncError,
    RemoteServiceError,
)

_AUTH_MESSAGES: dict[AuthFailureReason, str] = {
    "mfa": "Verification code is required",
    "timeout": "Sign-in timed out — try again",
    "sharepoint": "SharePoint sign-in failed — try again",
    "credentials": "Sign-in failed — check your username and password",
}


def _format_remote_error(exc: RemoteServiceError) -> str:
    if exc.status_code is not None:
        return f"{exc.service or 'Server'} error (HTTP {exc.status_code})"
    if exc.is_network:
        return f"{exc.service or 'Network'} connection error"
    return f"{exc.service} error" if exc.service else "Remote service error"


def format_ui_error(exc: IDATSyncError) -> str:
    """Return a concise, user-friendly English summary for ``exc``."""
    if isinstance(exc, AuthenticationError):
        summary = _AUTH_MESSAGES.get(exc.reason, _AUTH_MESSAGES["credentials"])
    elif isinstance(exc, CredentialsExpiredError):
        summary = (
            f"{exc.service} credentials expired — sign in again"
            if exc.service
            else "Credentials expired — sign in again"
        )
    elif isinstance(exc, CredentialStoreError):
        summary = "Could not update stored credentials"
    elif isinstance(exc, FileSystemError):
        summary = (
            "Could not save file to disk"
            if exc.operation == "write"
            else "Could not access local folder"
        )
    elif isinstance(exc, RemoteServiceError):
        summary = _format_remote_error(exc)
    else:
        summary = "Unexpected sync error"
    return summary


__all__ = ["format_ui_error"]
