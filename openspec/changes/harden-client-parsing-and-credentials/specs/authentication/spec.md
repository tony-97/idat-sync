## MODIFIED Requirements

### Requirement: JSON credential persistence and corrupt-file safety
`JsonCredentialStore` in `src/idat_sync/infrastructure/credential_store.py` SHALL accept `credentials_file: str | Path` (storing `self.credentials_file`) and log `"credentials file"` in status/error messages. `save(credentials)` SHALL ensure `Path(credentials_file).parent` exists, write JSON to a sibling `.part` file created with `0o600` permissions via `os.open`, atomically replace `self.credentials_file` via `os.replace`, unlink the `.part` file (`missing_ok=True`) if writing or replacing fails, and return `bool`; `load()` SHALL open `credentials_file` directly inside `try:` (without a pre-`try` `.exists()` check) and return `None` when the file is missing, unreadable, invalid JSON, or missing valid non-empty `token` (`str`) and `sharepoint_storage_state` (`dict`); `clear()` SHALL remove the file if present. Verified at the `JsonCredentialStore` (`src/idat_sync/infrastructure/credential_store.py`) seam.

#### Scenario: Saving, loading, and clearing valid Credentials on disk
- **WHEN** `JsonCredentialStore(path)` saves valid `Credentials`, calls `load()`, and calls `clear()`
- **THEN** `save` creates parent directories, writes to a sibling `.part` file opened with `0o600` permissions, atomically replaces `path` via `os.replace`, and returns `True`, `load()` returns the matching `Credentials`, and `clear()` deletes the file so subsequent `load()` returns `None`

#### Scenario: Preserving existing Credentials if atomic save fails mid-write
- **WHEN** `JsonCredentialStore(path).save(new_credentials)` fails during write or `os.replace` while an earlier `Credentials` file already exists at `path`
- **THEN** `save` removes the sibling `.part` file (`missing_ok=True`), returns `False`, and leaves the existing `path` file intact

#### Scenario: Returning None for corrupt, unreadable, or incomplete credential files
- **WHEN** `credentials_file` is unreadable (`OSError`/`PermissionError`), contains invalid JSON, `"{}"`, or an empty/non-string `"token"` or non-dict `"sharepoint_storage_state"`
- **THEN** `JsonCredentialStore.load()` catches `(OSError, json.JSONDecodeError)` and returns `None` without raising
