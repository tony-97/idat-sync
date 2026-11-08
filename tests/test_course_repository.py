from pathlib import Path
import unittest
from unittest.mock import MagicMock

from idat_sync.models import (
    Assignment,
    Course,
    CourseModule,
    CourseSection,
    ModuleContent,
)
from idat_sync.services.course_repository import CourseRepository
from idat_sync.services.downloaders import (
    MoodleContent,
    SharepointContent,
    StringContent,
)
from idat_sync.services.moodle_adapter import MoodleAdapter
from idat_sync.services.sharepoint_adapter import SharepointApiAdapter


class TestCourseRepository(unittest.TestCase):
    def setUp(self):
        self.mock_moodle = MagicMock(spec=MoodleAdapter)
        self.mock_moodle.can_handle.side_effect = lambda url: "aulavirtual" in url
        self.mock_moodle.list_url_content.side_effect = (
            lambda url, filename, last_mod: [
                MoodleContent(Path(filename), last_mod, url)
            ]
        )

        self.mock_sharepoint = MagicMock(spec=SharepointApiAdapter)
        self.mock_sharepoint.can_handle.side_effect = lambda url: "sharepoint" in url

        self.repo = CourseRepository(
            moodle_adapter=self.mock_moodle,
            sharepoint_adapter=self.mock_sharepoint,
        )

    def test_get_courses_delegates_to_moodle_adapter(self):
        domain_courses = [
            Course(id=1, name="Algorithms"),
            Course(id=2, name="Databases"),
        ]
        self.mock_moodle.get_courses_with_contents.return_value = domain_courses

        result = self.repo.get_courses()

        self.assertEqual(result, domain_courses)
        for c in result:
            self.assertIsInstance(c, Course)
            self.assertNotIsInstance(c, dict)

    def test_list_url_content_with_domain_module_content(self):
        items = [
            ModuleContent(
                filename="document.pdf",
                fileurl="https://aulavirtual.idat.edu.pe/document.pdf",
                timemodified=1700000000,
            ),
            ModuleContent(
                filename="slides.pptx",
                fileurl="https://aulavirtual.idat.edu.pe/slides.pptx",
                timemodified=1700000100,
            ),
        ]

        contents = self.repo.list_url_content(items)

        self.assertEqual(len(contents), 2)
        self.assertIsInstance(contents[0], MoodleContent)
        self.assertEqual(contents[0].path, Path("document.pdf"))
        self.assertEqual(contents[1].path, Path("slides.pptx"))

    def test_get_course_content_builds_all_content_types_from_domain_model(self):
        # Construct domain Course with sections, modules, and assignment
        course = Course(
            id=10,
            name="Cloud Computing",
            sections=[
                CourseSection(
                    id=101,
                    name="Semana 01",
                    summary="<p>Introduction to Cloud</p>",
                    modules=[
                        CourseModule(
                            id=201,
                            name="Lecture Notes",
                            modname="resource",
                            description="",
                            contents=[
                                ModuleContent(
                                    filename="notes.pdf",
                                    fileurl="https://aulavirtual.idat.edu.pe/notes.pdf",
                                    timemodified=1690000000,
                                )
                            ],
                        ),
                        CourseModule(
                            id=202,
                            name="Important Notice",
                            modname="label",
                            description="<p>Please review syllabus</p>",
                            contents=[],
                        ),
                        CourseModule(
                            id=203,
                            name="External Guide",
                            modname="url",
                            description="",
                            contents=[
                                ModuleContent(
                                    filename="guide_link",
                                    fileurl="https://aulavirtual.idat.edu.pe/guide",
                                    timemodified=1690001000,
                                )
                            ],
                        ),
                        CourseModule(
                            id=204,
                            name="Project Submission",
                            modname="assign",
                            description="",
                            contents=[],
                        ),
                    ],
                )
            ],
            assignments={
                204: Assignment(
                    id=55,
                    cmid=204,
                    name="Project Submission",
                    intro="Submit your zip file here",
                    introattachments=[
                        ModuleContent(
                            filename="spec.pdf",
                            fileurl="https://aulavirtual.idat.edu.pe/spec.pdf",
                            timemodified=1690002000,
                        )
                    ],
                )
            },
        )

        contents = self.repo.get_course_content(course, course_dir="CloudComputingDir")

        self.assertEqual(len(contents), 6)

        # 1. Section summary
        summary_content = contents[0]
        self.assertIsInstance(summary_content, StringContent)
        self.assertEqual(
            summary_content.path,
            Path("CloudComputingDir/Semana 01/Semana 01-[101]-resumen.html"),
        )
        self.assertEqual(summary_content.contents, ["<p>Introduction to Cloud</p>"])

        # 2. Resource file
        res_file = contents[1]
        self.assertIsInstance(res_file, MoodleContent)
        self.assertEqual(
            res_file.path,
            Path("CloudComputingDir/Semana 01/Lecture Notes/notes.pdf"),
        )

        # 3. Label content
        label_content = contents[2]
        self.assertIsInstance(label_content, StringContent)
        self.assertEqual(
            label_content.path,
            Path(
                "CloudComputingDir/Semana 01/Important Notice/Important Notice.html"
            ),
        )
        self.assertEqual(label_content.contents, ["<p>Please review syllabus</p>"])

        # 4. URL content
        url_content = contents[3]
        self.assertIsInstance(url_content, MoodleContent)
        self.assertEqual(
            url_content.path,
            Path("CloudComputingDir/Semana 01/External Guide/guide_link"),
        )

        # 5. Assignment intro
        assign_intro = contents[4]
        self.assertIsInstance(assign_intro, StringContent)
        self.assertEqual(
            assign_intro.path,
            Path(
                "CloudComputingDir/Semana 01/Project Submission/Project Submission-[55]-intro.html"
            ),
        )
        self.assertEqual(assign_intro.contents, ["Submit your zip file here"])

        # 6. Assignment attachment
        assign_att = contents[5]
        self.assertIsInstance(assign_att, MoodleContent)
        self.assertEqual(
            assign_att.path,
            Path("CloudComputingDir/Semana 01/Project Submission/spec.pdf"),
        )

    def test_get_course_content_preserves_moodle_structure_for_sharepoint_folder_url(
        self,
    ):
        mock_sp_file1 = MagicMock()
        mock_sp_file2 = MagicMock()
        # Adapter returns paths relative to the shared folder root (no Moodle parents)
        self.mock_sharepoint.list_url_content.return_value = [
            SharepointContent(Path("guia.pdf"), 1700000000, mock_sp_file1),
            SharepointContent(Path("Lab1/demo.py"), 1700000100, mock_sp_file2),
        ]

        course = Course(
            id=10,
            name="Cloud Computing",
            sections=[
                CourseSection(
                    id=101,
                    name="Semana 01",
                    summary="",
                    modules=[
                        CourseModule(
                            id=301,
                            name="Material de Clase",
                            modname="url",
                            description="",
                            contents=[
                                ModuleContent(
                                    filename="sharepoint_folder",
                                    fileurl="https://idat628.sharepoint.com/:f:/s/site/folder",
                                    timemodified=1700000000,
                                )
                            ],
                        )
                    ],
                )
            ],
        )

        contents = self.repo.get_course_content(course, course_dir="CloudComputingDir")

        sp_items = [c for c in contents if isinstance(c, SharepointContent)]
        self.assertEqual(len(sp_items), 2)
        self.assertEqual(
            sp_items[0].path,
            Path("CloudComputingDir/Semana 01/Material de Clase/guia.pdf"),
        )
        self.assertEqual(
            sp_items[1].path,
            Path("CloudComputingDir/Semana 01/Material de Clase/Lab1/demo.py"),
        )

    def test_get_all_courses_contents(self):
        course = Course(
            id=1,
            name="C1",
            sections=[
                CourseSection(
                    id=10,
                    name="S1",
                    summary="Summary 1",
                    modules=[],
                )
            ],
        )
        self.mock_moodle.get_courses_with_contents.return_value = [course]

        all_contents = self.repo.get_all_courses_contents()
        self.assertEqual(len(all_contents), 1)
        self.assertIsInstance(all_contents[0], StringContent)
        self.assertEqual(all_contents[0].path, Path("S1/S1-[10]-resumen.html"))


if __name__ == "__main__":
    unittest.main()
