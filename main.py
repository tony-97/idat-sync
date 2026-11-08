import requests

from idat_sync.utils import MOODLE_IDAT, RQ_HEADER


def get_token(user: str, password: str) -> str:
    response = requests.get(
        f"{MOODLE_IDAT}/login/token.php",
        params={
            "username": user,
            "password": password,
            "service": "moodle_mobile_app",
        },
        timeout=120,
        headers=RQ_HEADER,
    )
    response.raise_for_status()
    login = response.json()
    if "error" in login:
        print(f"[!] Token error: {login.get('error')}")
    token = login.get("token")
    if not token:
        print(
            "[!] Could not obtain token. Check URL/credentials or if mobile service is enabled."
        )
    return token


token = get_token("iv71430260", "t8C,,;_2@bB&~iQ")
print(token)
