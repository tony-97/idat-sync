"""Application and port-boundary exceptions for IDAT Sync."""

from __future__ import annotations

from typing import Literal

ServiceName = Literal["Moodle", "SharePoint"]
AuthFailureReason = Literal["credentials", "mfa", "timeout", "sharepoint"]
FileSystemOperation = Literal["access", "write"]


class IDATSyncError(Exception):
    """Base exception for application and port-boundary failures."""


class AuthenticationError(IDATSyncError):
    """Raised when Moodle or SharePoint login credentials or MFA fail."""

    def __init__(
        self, message: str, *, reason: AuthFailureReason = "credentials"
    ) -> None:
        """Store diagnostic ``message`` and structured ``reason``."""
        super().__init__(message)
        self.reason: AuthFailureReason = reason


class CredentialsExpiredError(IDATSyncError):
    """Raised when stored Moodle or SharePoint Credentials are rejected or expired."""

    def __init__(self, message: str, *, service: ServiceName | None = None) -> None:
        """Store diagnostic ``message`` and optional ``service`` name."""
        super().__init__(message)
        self.service: ServiceName | None = service


class CredentialStoreError(IDATSyncError):
    """Raised when persisting or clearing stored Credentials fails on disk."""


class RemoteServiceError(IDATSyncError):
    """Raised when a remote Moodle, SharePoint, or recording operation fails."""

    def __init__(
        self,
        message: str,
        *,
        service: ServiceName | None = None,
        status_code: int | None = None,
        is_network: bool = False,
    ) -> None:
        """Store diagnostic ``message`` and structured transport metadata."""
        super().__init__(message)
        self.service: ServiceName | None = service
        self.status_code: int | None = status_code
        self.is_network: bool = is_network


class FileSystemError(IDATSyncError):
    """Raised when a local filesystem operation fails."""

    def __init__(
        self, message: str, *, operation: FileSystemOperation = "access"
    ) -> None:
        """Store diagnostic ``message`` and structured ``operation`` kind."""
        super().__init__(message)
        self.operation: FileSystemOperation = operation


__all__ = [
    "AuthFailureReason",
    "AuthenticationError",
    "CredentialStoreError",
    "CredentialsExpiredError",
    "FileSystemError",
    "FileSystemOperation",
    "IDATSyncError",
    "RemoteServiceError",
    "ServiceName",
]
