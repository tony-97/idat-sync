import requests
import sys

MOODLE_IDAT = "https://aulavirtual.idat.edu.pe"
RQ_HEADER = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 7.1.1; Moto G Play Build/NPIS26.48-43-2; wv) AppleWebKit/537.36"
        + " (KHTML, like Gecko) Version/4.0 Chrome/71.0.3578.99 Mobile Safari/537.36 MoodleMobile"
    ),
    "Content-Type": "application/x-www-form-urlencoded",
}
ROOT_FOLDER = "C:\\Users\\User\\Desktop\\src\\idat-sync\\syc_folder"


def get_token():
    response = requests.get(
        f"{MOODLE_IDAT}/login/token.php",
        params={
            "username": "iv71430260",
            "password": "-TS7dp^$9G>BR82",
            "service": "moodle_mobile_app",
        },
        timeout=60,
        headers=RQ_HEADER,
    )
    response.raise_for_status()
    login = response.json()
    if "error" in login:
        print(f"[!] Token error: {login.get('error')}")
        sys.exit(1)
    token = login.get("token")
    if not token:
        print(
            "[!] Could not obtain token. Check URL/credentials or if mobile service is enabled."
        )
        sys.exit(1)
    return token
