"""Application and port-boundary exceptions for IDAT Sync."""


class IDATSyncError(Exception):
    """Base exception for application and port-boundary failures."""


class AuthenticationError(IDATSyncError):
    """Raised when Moodle or SharePoint login credentials or MFA fail."""


class CredentialsExpiredError(IDATSyncError):
    """Raised when stored Moodle or SharePoint Credentials are rejected or expired."""


class CredentialStoreError(IDATSyncError):
    """Raised when persisting or clearing stored Credentials fails on disk."""


class RemoteServiceError(IDATSyncError):
    """Raised when a remote Moodle, SharePoint, or recording operation fails."""


class FileSystemError(IDATSyncError):
    """Raised when a local filesystem operation fails."""


__all__ = [
    "AuthenticationError",
    "CredentialStoreError",
    "CredentialsExpiredError",
    "FileSystemError",
    "IDATSyncError",
    "RemoteServiceError",
]
