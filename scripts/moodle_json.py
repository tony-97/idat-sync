import asyncio
import getpass
import json
from pathlib import Path
import sys

from idat_sync.config import load_config
from idat_sync.infrastructure.credential_store import JsonCredentialStore
from idat_sync.infrastructure.moodle_adapter import MoodleTokenAdapter
from idat_sync.infrastructure.moodle_client import MoodleClient, MoodleTokenClient


async def _get_token(config) -> str | None:
    if creds := JsonCredentialStore(config.session_file).load():
        return creds["token"]

    username = input("Username: ").strip().split("@", 1)[0]
    password = getpass.getpass("Password: ")
    if not username or not password:
        print("Username and password are required.", file=sys.stderr)
        return None

    return await MoodleTokenAdapter(
        MoodleTokenClient(config.moodle_base_url)
    ).fetch_token(username, password)


async def main() -> int:
    """Fetch Moodle courses, contents, and assignments and write them to ./all_courses_contents.json."""
    config = load_config()
    token = await _get_token(config)
    if not token:
        return 1

    client = MoodleClient(
        token,
        base_url=config.moodle_base_url,
        request_delay=config.request_delay,
    )
    print("Fetching enrolled courses...")
    courses = await client.fetch_enrolled_courses()
    print(f"Found {len(courses)} courses. Fetching assignments...")
    assignments_by_course = {
        int(item["id"]): item.get("assignments", [])
        for item in await client.fetch_assignments()
        if isinstance(item, dict) and item.get("id") is not None
    }
    payload = []
    total = len(courses)
    for idx, course in enumerate(courses, start=1):
        cid = course.get("id")
        name = course.get("fullname") or course.get("shortname") or "<unnamed>"
        print(f"[{idx}/{total}] Fetching contents for: {name} (id={cid})...")
        payload.append(
            {
                "course": course,
                "contents": await client.fetch_course_contents(int(cid))
                if cid is not None
                else [],
                "assignments": assignments_by_course.get(int(cid), [])
                if cid is not None
                else [],
            }
        )
    output_path = Path("./all_courses_contents.json")
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Saved {len(payload)} courses to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
