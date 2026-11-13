from pathlib import Path
import unittest
from unittest.mock import MagicMock

from idat_sync.application.course_repository import CourseRepository
from idat_sync.application.ports import (
    CourseGateway,
    RecordingGateway,
    UrlContentStrategy,
)
from idat_sync.domain.content import (
    MoodleContent,
    RecordingContent,
    StringContent,
)
from idat_sync.domain.course import (
    Course,
    CourseModule,
    CourseSection,
    ModuleContent,
)


class TestCourseRepository(unittest.TestCase):
    def setUp(self):
        self.mock_course_gateway = MagicMock(spec=CourseGateway)
        self.mock_recording_gateway = MagicMock(spec=RecordingGateway)

        self.mock_moodle_strategy = MagicMock(spec=UrlContentStrategy)
        self.mock_moodle_strategy.can_handle.side_effect = (
            lambda url: "aulavirtual" in url
        )
        self.mock_moodle_strategy.list_url_content.side_effect = (
            lambda url, filename, last_mod: [
                MoodleContent(Path(filename), last_mod, url)
            ]
        )

        self.mock_sp_strategy = MagicMock(spec=UrlContentStrategy)
        self.mock_sp_strategy.can_handle.side_effect = (
            lambda url: "sharepoint" in url
        )

        self.strategies = [self.mock_moodle_strategy, self.mock_sp_strategy]
        self.repo = CourseRepository(
            course_gateway=self.mock_course_gateway,
            recording_gateway=self.mock_recording_gateway,
            url_strategies=self.strategies,
        )

    def test_get_courses_delegates_to_course_gateway(self):
        domain_courses = [
            Course(id=1, name="Algorithms"),
            Course(id=2, name="Databases"),
        ]
        self.mock_course_gateway.get_courses_with_contents.return_value = (
            domain_courses
        )

        result = self.repo.get_courses()

        self.assertEqual(result, domain_courses)
        for c in result:
            self.assertIsInstance(c, Course)
            self.assertNotIsInstance(c, dict)

    def test_get_recordings_delegates_to_recording_gateway_and_wraps_in_folder(
        self,
    ):
        rec_items = [
            RecordingContent(
                Path("Semana 1/video1.mp4"),
                1700000000,
                "https://idat628-my.sharepoint.com/:v:/r/video1.mp4",
            ),
            RecordingContent(
                Path("Semana 2/video2.mp4"),
                1700000100,
                "https://idat628-my.sharepoint.com/:v:/r/video2.mp4",
            ),
        ]
        self.mock_recording_gateway.fetch_recordings.return_value = rec_items

        results = self.repo.get_recordings(
            "Cloud Computing", recordings_folder="CloudComputingDir/Grabaciones"
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(
            results[0].path,
            Path("CloudComputingDir/Grabaciones/Semana 1/video1.mp4"),
        )
        self.assertEqual(
            results[1].path,
            Path("CloudComputingDir/Grabaciones/Semana 2/video2.mp4"),
        )

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

    def test_get_course_content_builds_all_content_types_from_domain_model(
        self,
    ):
        course = Course(
            id=10,
            name="Cloud Computing",
            sections=[
                CourseSection(
                    id=101,
                    name="Semana 01",
                    inline_contents=[
                        StringContent(
                            Path("Semana 01-[101]-resumen.html"),
                            0,
                            ["<p>Introduction to Cloud</p>"],
                        )
                    ],
                    modules=[
                        CourseModule(
                            id=201,
                            name="Lecture Notes",
                            inline_contents=[],
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
                            inline_contents=[
                                StringContent(
                                    Path("Important Notice.html"),
                                    0,
                                    ["<p>Please review syllabus</p>"],
                                )
                            ],
                            contents=[],
                        ),
                        CourseModule(
                            id=203,
                            name="External Guide",
                            inline_contents=[],
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
                            inline_contents=[
                                StringContent(
                                    Path("Project Submission-[55]-intro.html"),
                                    0,
                                    ["Submit your zip file here"],
                                )
                            ],
                            contents=[
                                ModuleContent(
                                    filename="spec.pdf",
                                    fileurl="https://aulavirtual.idat.edu.pe/spec.pdf",
                                    timemodified=1690002000,
                                )
                            ],
                        ),
                        CourseModule(
                            id=205,
                            name="",  # Empty name should be skipped
                            inline_contents=[],
                            contents=[],
                        ),
                    ],
                ),
                CourseSection(
                    id=102,
                    name="",  # Empty section name should be skipped
                    inline_contents=[],
                    modules=[],
                ),
            ],
        )

        contents = self.repo.get_course_content(
            course, course_dir="CloudComputingDir"
        )

        self.assertEqual(len(contents), 6)

        # 1. Section summary
        summary_content = contents[0]
        self.assertIsInstance(summary_content, StringContent)
        self.assertEqual(
            summary_content.path,
            Path("CloudComputingDir/Semana 01/Semana 01-[101]-resumen.html"),
        )
        self.assertEqual(
            summary_content.contents, ["<p>Introduction to Cloud</p>"]
        )

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
        self.assertEqual(
            label_content.contents, ["<p>Please review syllabus</p>"]
        )

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

    def test_get_all_courses_contents(self):
        course = Course(
            id=1,
            name="C1",
            sections=[
                CourseSection(
                    id=10,
                    name="S1",
                    inline_contents=[
                        StringContent(
                            Path("S1-[10]-resumen.html"), 0, ["Summary 1"]
                        )
                    ],
                    modules=[],
                )
            ],
        )
        self.mock_course_gateway.get_courses_with_contents.return_value = [
            course
        ]

        all_contents = self.repo.get_all_courses_contents()
        self.assertEqual(len(all_contents), 1)
        self.assertIsInstance(all_contents[0], StringContent)
        self.assertEqual(all_contents[0].path, Path("S1/S1-[10]-resumen.html"))

    def test_get_course_content_sanitizes_section_and_module_names_and_skips_empty(self):
        course = Course(
            id=1,
            name="C1",
            sections=[
                CourseSection(
                    id=1,
                    name="",
                    inline_contents=[StringContent(Path("skip.html"), 0, ["x"])],
                    modules=[],
                ),
                CourseSection(
                    id=2,
                    name="Sesión: 1/2?",
                    inline_contents=[],
                    modules=[
                        CourseModule(
                            id=10,
                            name="",
                            inline_contents=[StringContent(Path("skip2.html"), 0, ["x"])],
                        ),
                        CourseModule(
                            id=11,
                            name="CON",
                            inline_contents=[StringContent(Path("nota.html"), 0, ["ok"])],
                        ),
                    ],
                ),
            ],
        )

        items = self.repo.get_course_content(course, Path("CourseDir"))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].path, Path("CourseDir/Sesión 1 2/_CON/nota.html"))


if __name__ == "__main__":
    unittest.main()
