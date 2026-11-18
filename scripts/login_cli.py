import asyncio
import getpass
import sys

from idat_sync.application.ports import IDATSyncError
from idat_sync.bootstrap import create_auth_service
from idat_sync.config import load_config


async def _get_mfa_code() -> str:
    return (await asyncio.to_thread(input, "Enter your 6-digit MFA code: ")).strip()


def main() -> int:
    """Prompt for login inputs, authenticate Moodle and SharePoint, and save Credentials JSON."""
    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")
    if not username or not password:
        print("Username and password are required.", file=sys.stderr)
        return 1

    config = load_config()
    auth_service = create_auth_service(config)
    try:
        asyncio.run(auth_service.login(username, password, _get_mfa_code))
    except IDATSyncError as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1

    print(f"Credentials saved to {config.session_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
