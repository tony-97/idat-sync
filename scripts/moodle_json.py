import getpass
import json
from pathlib import Path
import sys

from idat_sync.config import load_config
from idat_sync.infrastructure.moodle_adapter import MoodleTokenAdapter
from idat_sync.infrastructure.moodle_client import MoodleClient, MoodleTokenClient


def main() -> int:
    """Prompt for Moodle credentials and write courses, contents, and assignments to ./all_courses_contents.json."""
    username = input("Username: ").strip().split("@", 1)[0]
    password = getpass.getpass("Password: ")
    if not username or not password:
        print("Username and password are required.", file=sys.stderr)
        return 1

    config = load_config()
    token = MoodleTokenAdapter(
        MoodleTokenClient(config.moodle_base_url)
    ).fetch_token(username, password)
    if not token:
        return 1

    client = MoodleClient(
        token,
        base_url=config.moodle_base_url,
        request_delay=config.request_delay,
    )
    courses = client.fetch_enrolled_courses()
    assignments_by_course = {
        int(item["id"]): item.get("assignments", [])
        for item in client.fetch_assignments()
        if isinstance(item, dict) and item.get("id") is not None
    }
    payload = [
        {
            "course": course,
            "contents": client.fetch_course_contents(int(course["id"]))
            if course.get("id") is not None
            else [],
            "assignments": assignments_by_course.get(int(course["id"]), [])
            if course.get("id") is not None
            else [],
        }
        for course in courses
    ]
    output_path = Path("./all_courses_contents.json")
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Saved {len(payload)} courses to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
