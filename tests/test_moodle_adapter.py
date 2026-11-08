from pathlib import Path
import unittest
from unittest.mock import MagicMock

from idat_sync.models import Assignment, Course, CourseModule, CourseSection, ModuleContent
from idat_sync.services.downloaders import MoodleContent
from idat_sync.services.moodle_adapter import MoodleAdapter
from idat_sync.services.moodle_client import MoodleClient


class TestMoodleAdapter(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock(spec=MoodleClient)
        self.mock_client.token = "test_token_123"
        self.adapter = MoodleAdapter(client=self.mock_client)

    def test_can_handle(self):
        self.assertTrue(self.adapter.can_handle("https://aulavirtual.idat.edu.pe/mod/resource/view.php?id=123"))
        self.assertFalse(self.adapter.can_handle("https://idat628.sharepoint.com/sites/my_site"))

    def test_list_url_content(self):
        items = self.adapter.list_url_content("https://aulavirtual.idat.edu.pe/file.pdf", "file.pdf", 123456)
        self.assertEqual(len(items), 1)
        self.assertIsInstance(items[0], MoodleContent)
        self.assertEqual(items[0].path, Path("file.pdf"))
        self.assertEqual(items[0].filename, "file.pdf")
        self.assertEqual(items[0].last_modified, 123456)

    def test_list_courses_converts_to_domain_models(self):
        # Raw vendor data returned by MoodleClient
        self.mock_client.fetch_enrolled_courses.return_value = [
            {"id": 10, "fullname": "Software Engineering", "shortname": "SE"},
            {"id": 20, "fullname": "", "shortname": "DB101"},
        ]

        courses = self.adapter.list_courses()

        self.assertEqual(len(courses), 2)
        for c in courses:
            self.assertIsInstance(c, Course)
            # Ensure it is a domain object, NOT a raw dict
            self.assertNotIsInstance(c, dict)

        self.assertEqual(courses[0].id, 10)
        self.assertEqual(courses[0].name, "Software Engineering")
        self.assertEqual(courses[1].id, 20)
        self.assertEqual(courses[1].name, "DB101")

    def test_get_assignments_by_course_converts_to_domain_models(self):
        # Raw vendor data returned by MoodleClient
        self.mock_client.fetch_assignments.return_value = [
            {
                "id": 10,
                "assignments": [
                    {
                        "id": 501,
                        "cmid": 999,
                        "name": "Homework 1",
                        "intro": "<p>Read chapter 1</p>",
                        "introattachments": [
                            {
                                "filename": "rubric.pdf",
                                "fileurl": "https://aulavirtual/rubric.pdf",
                                "timemodified": 1700000000,
                                "filesize": 4096,
                            }
                        ],
                    }
                ],
            }
        ]

        assignments_by_course = self.adapter.get_assignments_by_course()

        self.assertIn(10, assignments_by_course)
        assignments = assignments_by_course[10]
        self.assertIn(999, assignments)

        assign = assignments[999]
        self.assertIsInstance(assign, Assignment)
        self.assertNotIsInstance(assign, dict)
        self.assertEqual(assign.id, 501)
        self.assertEqual(assign.cmid, 999)
        self.assertEqual(assign.name, "Homework 1")
        self.assertEqual(assign.intro, "<p>Read chapter 1</p>")
        self.assertEqual(len(assign.introattachments), 1)

        att = assign.introattachments[0]
        self.assertIsInstance(att, ModuleContent)
        self.assertNotIsInstance(att, dict)
        self.assertEqual(att.filename, "rubric.pdf")
        self.assertEqual(att.fileurl, "https://aulavirtual/rubric.pdf")
        self.assertEqual(att.timemodified, 1700000000)

    def test_get_course_sections_converts_to_domain_models(self):
        # Raw vendor sections returned by MoodleClient
        self.mock_client.fetch_course_contents.return_value = [
            {
                "id": 1,
                "name": "Week 1",
                "summary": "<p>Introduction</p>",
                "modules": [
                    {
                        "id": 101,
                        "name": "Lecture 1 Slides",
                        "modname": "resource",
                        "description": "",
                        "contents": [
                            {
                                "filename": "slides.pdf",
                                "fileurl": "https://aulavirtual/slides.pdf",
                                "timemodified": 1695000000,
                            }
                        ],
                    },
                    {
                        "id": 102,
                        "name": "Notice",
                        "modname": "label",
                        "description": "<p>Class starts at 8am</p>",
                        "contents": [],
                    },
                ],
            }
        ]

        sections = self.adapter.get_course_sections(course_id=10)

        self.assertEqual(len(sections), 1)
        section = sections[0]
        self.assertIsInstance(section, CourseSection)
        self.assertNotIsInstance(section, dict)
        self.assertEqual(section.id, 1)
        self.assertEqual(section.name, "Week 1")
        self.assertEqual(section.summary, "<p>Introduction</p>")
        self.assertEqual(len(section.modules), 2)

        mod1 = section.modules[0]
        self.assertIsInstance(mod1, CourseModule)
        self.assertNotIsInstance(mod1, dict)
        self.assertEqual(mod1.id, 101)
        self.assertEqual(mod1.name, "Lecture 1 Slides")
        self.assertEqual(mod1.modname, "resource")
        self.assertEqual(len(mod1.contents), 1)

        content = mod1.contents[0]
        self.assertIsInstance(content, ModuleContent)
        self.assertNotIsInstance(content, dict)
        self.assertEqual(content.filename, "slides.pdf")

        mod2 = section.modules[1]
        self.assertEqual(mod2.modname, "label")
        self.assertEqual(mod2.description, "<p>Class starts at 8am</p>")

    def test_get_courses_with_contents_orchestrates_full_domain_models(self):
        self.mock_client.fetch_enrolled_courses.return_value = [
            {"id": 42, "fullname": "Data Structures"}
        ]
        self.mock_client.fetch_assignments.return_value = [
            {
                "id": 42,
                "assignments": [
                    {"id": 1, "cmid": 201, "name": "Lab 1", "intro": "Lab info", "introattachments": []}
                ],
            }
        ]
        self.mock_client.fetch_course_contents.return_value = [
            {"id": 1, "name": "Topic 1", "summary": "Intro", "modules": []}
        ]

        courses = self.adapter.get_courses_with_contents()

        self.assertEqual(len(courses), 1)
        course = courses[0]
        self.assertIsInstance(course, Course)
        self.assertEqual(course.id, 42)
        self.assertEqual(course.name, "Data Structures")
        self.assertEqual(len(course.sections), 1)
        self.assertIsInstance(course.sections[0], CourseSection)
        self.assertIn(201, course.assignments)
        self.assertIsInstance(course.assignments[201], Assignment)


if __name__ == "__main__":
    unittest.main()
