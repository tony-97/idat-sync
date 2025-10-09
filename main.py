from pathlib import Path
import unicodedata
import re
import requests
import os
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


WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(s: str, replacement: str = " ", max_len: int = 100) -> str:
    # Normalize (so accents become plain letters when possible)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    # Replace forbidden characters (Windows + Unix)
    s = re.sub(r'[<>:"/\\|?*\x00-\x1F]', replacement, s)
    # Collapse whitespace and separators
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[ .]+$", "", s)  # no trailing dot/space (Windows)
    s = re.sub(rf"{re.escape(replacement)}+", replacement, s)
    # Default name if empty
    if not s:
        s = "untitled"
    # Avoid reserved device names (Windows)
    base = s
    if base.upper() in WINDOWS_RESERVED:
        s = f"_{base}"
    # Enforce length (typical FS limit is 255 bytes; keep margin)
    s = s[:max_len].strip()
    return s


def save_content(path: str, content: str):
    content = content.strip()
    if not Path(path).exists() and content:
        with open(path, "w") as f:
            f.write(content)


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


def call_ws(token, function, **kwargs):
    url = f"{MOODLE_IDAT}/webservice/rest/server.php"
    payload = {
        "wstoken": token,
        "wsfunction": function,
        "moodlewsrestformat": "json",
        **kwargs,
    }
    response = requests.post(url, data=payload, timeout=45)
    response.raise_for_status()
    j = response.json()
    if isinstance(j, dict) and j.get("exception"):
        raise RuntimeError(f"{j.get('errorcode')}: {j.get('message')}")
    return j


def sync_courses(contents, assignments, token, course_path):
    for content in contents:
        if content_name := safe_filename(content.get("name", "")):
            content_path = os.path.join(course_path, content_name)
            Path(content_path).mkdir(parents=True, exist_ok=True)
            summary_name = f"{content_name}-[{content.get("id", "")}]-resumen.html"
            summary_path = os.path.join(content_path, summary_name)
            save_content(summary_path, content.get("summary", ""))
            for module in content.get("modules", []):
                if module_name := safe_filename(module.get("name", "")):
                    module_path: str = os.path.join(content_path, module_name)
                    modname = module.get("modname")
                    module_contents = module.get("contents", [])
                    if modname == "label" and (
                        description := module.get("description", ""),
                    ):
                        save_content(f"{module_path}.html", description)
                    elif modname == "url" and (len(module_contents) == 1):
                        ...
                    elif modname == "assign":
                        Path(module_path).mkdir(parents=True, exist_ok=True)
                        cmid = module.get("id", None)
                        assignment = next(
                            (
                                assign
                                for assign in assignments
                                if assign.get("cmid", None) == cmid
                            ),
                            None,
                        )
                        if assignment:
                            assignment_intro_name = f"{assignment.get("name", "")}-[{assignment.get("id", "")}]-intro.html"
                            assignment_intro_path = os.path.join(
                                module_path, assignment_intro_name
                            )
                            save_content(
                                assignment_intro_path, assignment.get("intro", "")
                            )
                    else:
                        Path(module_path).mkdir(parents=True, exist_ok=True)
                        for module_content in module_contents:
                            if (type := module_content.get("type")) == "url" or (
                                type == "file"
                            ):
                                if (file_url := module_content.get("fileurl")) and (
                                    file_name := module_content.get("filename")
                                ):
                                    with requests.get(
                                        file_url,
                                        params={"wstoken": token, "token": token},
                                        stream=True,
                                        allow_redirects=True,
                                        timeout=60,
                                        headers=RQ_HEADER,
                                    ) as r:
                                        if r.status_code == 403:
                                            raise RuntimeError(
                                                "403 Forbidden: your session cookie is missing/expired."
                                            )
                                        r.raise_for_status()
                                        with open(
                                            os.path.join(module_path, file_name), "wb"
                                        ) as f:
                                            for part in r.iter_content(
                                                chunk_size=1024 * 256
                                            ):
                                                if part:
                                                    f.write(part)


def print_courses(courses):
    print("\nYour Moodle courses:")
    for i, c in enumerate(courses, 1):
        fullname = c.get("fullname") or c.get("shortname")
        print(f"{i:>2}. [{c.get('id')}] {fullname}")


def choose_course(courses):
    while True:
        s = input("\nSelect course number: ").strip()
        if not s.isdigit():
            print("Enter a number from the list.")
            continue
        idx = int(s)
        if 1 <= idx <= len(courses):
            return courses[idx - 1]
        print("Out of range.")


def get_course_contents(token, course_id):
    return call_ws(
        token,
        "core_course_get_contents",
        courseid=f"{course_id}",
    )


def list_my_courses(token):
    try:
        return call_ws(
            token,
            "core_course_get_enrolled_courses_by_timeline_classification",
            classification="all",
            limit=100,
            offset=0,
        ).get("courses", [])
    except Exception as e:
        print("[!] Could not list courses via core_enrol_get_users_courses.")
        raise e


def main():
    print("== Moodle Course Contents Viewer ==")
    token = get_token()
    courses = list_my_courses(token)
    if not courses:
        print("No courses found for this user.")
        return
    print_courses(courses)
    chosen = choose_course(courses)
    cid = chosen.get("id")
    print(
        f"\nFetching contents for: {chosen.get('fullname') or chosen.get('shortname')} (id={cid}) ..."
    )
    contents = get_course_contents(token, cid)
    assignments_courses = call_ws(token, "mod_assign_get_assignments").get(
        "courses", []
    )
    assignments = (
        next(
            (course for course in assignments_courses if course.get("id") == cid), None
        )
        or {}
    ).get("assignments", [])
    sync_courses(contents, assignments, token, ROOT_FOLDER)
    if isinstance(contents, dict) and contents.get("exception"):
        print("[!] Error:", contents)
        return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted by user.")
    except Exception as e:
        print(f"\n[!] Error: {e}")
        sys.exit(1)
