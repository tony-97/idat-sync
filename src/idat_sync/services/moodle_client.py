import requests
import time

from idat_sync.utils import REQUEST_DELAY, MOODLE_IDAT


class MoodleClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def __call_ws(self, function: str, **kwargs):
        url = f"{MOODLE_IDAT}/webservice/rest/server.php"
        payload = {
            "wstoken": self.token,
            "wsfunction": function,
            "moodlewsrestformat": "json",
            **kwargs,
        }
        response = requests.post(url, data=payload, timeout=45)
        response.raise_for_status()
        j = response.json()
        if isinstance(j, dict) and j.get("exception"):
            raise RuntimeError(f"{j.get('errorcode')}: {j.get('message')}")
        time.sleep(REQUEST_DELAY)
        return j

    def list_my_courses(self):
        try:
            return self.__call_ws(
                "core_course_get_enrolled_courses_by_timeline_classification",
                classification="all",
                limit=100,
                offset=0,
            ).get("courses", [])
        except Exception as e:
            print("[!] Could not list courses via core_enrol_get_users_courses.")
            raise e

    def get_assignements_from_courses(self):
        courses = self.__call_ws("mod_assign_get_assignments").get("courses", [])
        courses_assignments: dict[int, dict[int, dict]] = {}
        for course in courses:
            assignments = course.get("assignments", [])
            module_assignments: dict[int, dict] = {}
            for assignment in assignments:
                module_assignments[assignment.get("cmid")] = assignment
            courses_assignments[course.get("id")] = module_assignments
        return courses_assignments

    def get_course_contents(self, course_id):
        return self.__call_ws(
            "core_course_get_contents",
            courseid=f"{course_id}",
        )
