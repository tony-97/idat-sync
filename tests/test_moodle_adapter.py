from pathlib import Path
import unittest
from unittest.mock import MagicMock

from idat_sync.domain.content import MoodleContent, StringContent
from idat_sync.domain.course import Course, CourseModule, CourseSection, ModuleContent
from idat_sync.infrastructure.moodle_adapter import MoodleAdapter
from idat_sync.infrastructure.moodle_client import MoodleClient


class TestMoodleAdapter(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock(spec=MoodleClient)
        self.mock_client.token = "test_token_123"
        self.adapter = MoodleAdapter(client=self.mock_client)

    def test_can_handle(self):
        self.assertTrue(
            self.adapter.can_handle(
                "https://aulavirtual.idat.edu.pe/mod/resource/view.php?id=123"
            )
        )
        self.assertFalse(
            self.adapter.can_handle(
                "https://idat628.sharepoint.com/sites/my_site"
            )
        )

    def test_list_url_content(self):
        items = self.adapter.list_url_content(
            "https://aulavirtual.idat.edu.pe/file.pdf", "file.pdf", 123456
        )
        self.assertEqual(len(items), 1)
        self.assertIsInstance(items[0], MoodleContent)
        self.assertEqual(items[0].path, Path("file.pdf"))
        self.assertEqual(items[0].filename, "file.pdf")
        self.assertEqual(items[0].last_modified, 123456)

    def test_list_courses_converts_to_domain_models(self):
        self.mock_client.fetch_enrolled_courses.return_value = [
            {"id": 10, "fullname": "Software Engineering", "shortname": "SE"},
            {"id": 20, "fullname": "", "shortname": "DB101"},
        ]

        courses = self.adapter.list_courses()

        self.assertEqual(len(courses), 2)
        for c in courses:
            self.assertIsInstance(c, Course)
            self.assertNotIsInstance(c, dict)

        self.assertEqual(courses[0].id, 10)
        self.assertEqual(courses[0].name, "Software Engineering")
        self.assertEqual(courses[1].id, 20)
        self.assertEqual(courses[1].name, "DB101")

    def test_section_summary_generates_inline_content(self):
        raw_section = {
            "id": 1,
            "name": "Week 1",
            "summary": "<p>Introduction</p>",
            "modules": [],
        }

        section = self.adapter._to_course_section(raw_section)

        self.assertIsInstance(section, CourseSection)
        self.assertEqual(section.id, 1)
        self.assertEqual(section.name, "Week 1")
        self.assertFalse(hasattr(section, "summary"))
        self.assertEqual(len(section.inline_contents), 1)
        summary_content = section.inline_contents[0]
        self.assertIsInstance(summary_content, StringContent)
        self.assertEqual(summary_content.path, Path("Week 1-[1]-resumen.html"))
        self.assertEqual(summary_content.contents, ["<p>Introduction</p>"])

    def test_label_module_with_non_empty_and_empty_descriptions(self):
        raw_label_non_empty = {
            "id": 101,
            "name": "Notice",
            "modname": "label",
            "description": "<p>Class starts at 8am</p>",
            "contents": [],
        }
        mod_with_desc = self.adapter._to_course_module(raw_label_non_empty)
        self.assertIsInstance(mod_with_desc, CourseModule)
        self.assertFalse(hasattr(mod_with_desc, "modname"))
        self.assertFalse(hasattr(mod_with_desc, "description"))
        self.assertEqual(len(mod_with_desc.inline_contents), 1)
        self.assertEqual(mod_with_desc.contents, [])
        self.assertEqual(mod_with_desc.inline_contents[0].path, Path("Notice.html"))
        self.assertEqual(
            mod_with_desc.inline_contents[0].contents, ["<p>Class starts at 8am</p>"]
        )

        raw_label_empty = {
            "id": 102,
            "name": "Blank Notice",
            "modname": "label",
            "description": "",
            "contents": [],
        }
        mod_empty = self.adapter._to_course_module(raw_label_empty)
        self.assertEqual(mod_empty.inline_contents, [])
        self.assertEqual(mod_empty.contents, [])

    def test_assign_module_merges_intro_and_attachments_by_cmid(self):
        raw_assign_module = {
            "id": 999,
            "name": "Homework Module",
            "modname": "assign",
            "contents": [],
        }
        assignments_map = {
            999: {
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
        }

        mod = self.adapter._to_course_module(
            raw_assign_module, assignments=assignments_map
        )
        self.assertEqual(len(mod.inline_contents), 1)
        self.assertEqual(
            mod.inline_contents[0].path, Path("Homework 1-[501]-intro.html")
        )
        self.assertEqual(
            mod.inline_contents[0].contents, ["<p>Read chapter 1</p>"]
        )
        self.assertEqual(len(mod.contents), 1)
        self.assertEqual(mod.contents[0].filename, "rubric.pdf")
        self.assertEqual(mod.contents[0].fileurl, "https://aulavirtual/rubric.pdf")
        self.assertEqual(mod.contents[0].timemodified, 1700000000)

        # Unmatched assign module
        mod_unmatched = self.adapter._to_course_module(
            raw_assign_module, assignments={}
        )
        self.assertEqual(mod_unmatched.inline_contents, [])
        self.assertEqual(mod_unmatched.contents, [])

    def test_get_courses_with_contents_orchestrates_full_domain_models(self):
        self.mock_client.fetch_enrolled_courses.return_value = [
            {"id": 42, "fullname": "Data Structures"}
        ]
        self.mock_client.fetch_assignments.return_value = [
            {
                "id": 42,
                "assignments": [
                    {
                        "id": 501,
                        "cmid": 201,
                        "name": "Lab 1",
                        "intro": "<p>Lab info</p>",
                        "introattachments": [
                            {
                                "filename": "instructions.pdf",
                                "fileurl": "https://aulavirtual/instructions.pdf",
                                "timemodified": 1690000000,
                            }
                        ],
                    }
                ],
            }
        ]
        self.mock_client.fetch_course_contents.return_value = [
            {
                "id": 1,
                "name": "Topic 1",
                "summary": "Intro",
                "modules": [
                    {
                        "id": 201,
                        "name": "Lab 1 Module",
                        "modname": "assign",
                        "contents": [],
                    }
                ],
            }
        ]

        courses = self.adapter.get_courses_with_contents()

        self.assertEqual(len(courses), 1)
        course = courses[0]
        self.assertIsInstance(course, Course)
        self.assertEqual(course.id, 42)
        self.assertEqual(course.name, "Data Structures")
        self.assertFalse(hasattr(course, "assignments"))

        self.assertEqual(len(course.sections), 1)
        section = course.sections[0]
        self.assertEqual(section.name, "Topic 1")
        self.assertEqual(len(section.inline_contents), 1)
        self.assertEqual(section.inline_contents[0].path, Path("Topic 1-[1]-resumen.html"))

        self.assertEqual(len(section.modules), 1)
        module = section.modules[0]
        self.assertEqual(len(module.inline_contents), 1)
        self.assertEqual(module.inline_contents[0].path, Path("Lab 1-[501]-intro.html"))
        self.assertEqual(len(module.contents), 1)
        self.assertEqual(module.contents[0].filename, "instructions.pdf")

    def test_moodle_adapter_public_seam_does_not_expose_raw_vendor_assignments(self):
        self.assertFalse(hasattr(self.adapter, "get_assignments_by_course"))
        self.assertTrue(hasattr(self.adapter, "_get_assignments_by_course"))


if __name__ == "__main__":
    unittest.main()
