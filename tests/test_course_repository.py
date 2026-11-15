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
    get_in_progress_course_ids,
)


class TestCourseRepository(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_course_gateway = MagicMock(spec=CourseGateway)
        self.mock_recording_gateway = MagicMock(spec=RecordingGateway)

        async def _moodle_list_url(url, filename, last_mod):
            return [MoodleContent(Path(filename), last_mod, url)]

        self.mock_moodle_strategy = MagicMock(spec=UrlContentStrategy)
        self.mock_moodle_strategy.can_handle.side_effect = (
            lambda url: "aulavirtual" in url
        )
        self.mock_moodle_strategy.list_url_content.side_effect = _moodle_list_url

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

    async def test_list_courses_and_populate_course_sections_delegate_to_course_gateway(
        self,
    ):
        domain_courses = [
            Course(id=1, name="Algorithms", category="IV.01.2026-III"),
            Course(id=2, name="Databases", category="III.01.2026-II"),
        ]
        self.mock_course_gateway.list_courses.return_value = domain_courses

        async def _return_course(c):
            return c

        self.mock_course_gateway.populate_course_sections.side_effect = (
            _return_course
        )

        result = await self.repo.list_courses()

        self.assertEqual(result, domain_courses)
        for c in result:
            self.assertIsInstance(c, Course)
            self.assertNotIsInstance(c, dict)

        populated = await self.repo.populate_course_sections(domain_courses[0])
        self.assertIs(populated, domain_courses[0])
        self.mock_course_gateway.populate_course_sections.assert_called_once_with(
            domain_courses[0]
        )
        self.assertFalse(hasattr(self.repo, "get_courses"))
        self.assertFalse(hasattr(self.repo, "get_all_courses_contents"))

    async def test_get_recordings_delegates_to_recording_gateway_and_wraps_in_folder(
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

        results = await self.repo.get_recordings(
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

    async def test_list_url_content_with_domain_module_content(self):
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

        contents = await self.repo.list_url_content(items)

        self.assertEqual(len(contents), 2)
        self.assertIsInstance(contents[0], MoodleContent)
        self.assertEqual(contents[0].path, Path("document.pdf"))
        self.assertEqual(contents[1].path, Path("slides.pptx"))

    async def test_get_course_content_builds_all_content_types_from_domain_model(
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

        contents = await self.repo.get_course_content(
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

    async def test_get_course_content_sanitizes_section_and_module_names_and_skips_empty(self):
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

        items = await self.repo.get_course_content(course, Path("CourseDir"))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].path, Path("CourseDir/Sesión 1 2/_CON/nota.html"))

    def test_invalidate_cache_delegates_to_course_gateway(self):
        self.repo.invalidate_cache()
        self.mock_course_gateway.invalidate_cache.assert_called_once_with()

    async def test_get_course_content_projects_section_info_and_bibliografia_without_url_strategies(
        self,
    ):
        course = Course(
            id=1,
            name="C1",
            sections=[
                CourseSection(
                    id=10,
                    name="Semana 01",
                    inline_contents=[
                        StringContent(Path("Semana 01-[10]-resumen.html"), 0, ["<p>S</p>"]),
                        StringContent(Path("info.html"), 0, ["<p>L1</p>\n<p>L2</p>"]),
                    ],
                ),
                CourseSection(
                    id=11,
                    name="Bibliografía",
                    inline_contents=[
                        StringContent(
                            Path("bibliografia.html"),
                            0,
                            ['<p>E-Libro</p>\n<p><a href="https://elibro.net/1">Libro</a></p>'],
                        )
                    ],
                ),
            ],
        )

        items = await self.repo.get_course_content(course, Path("CourseDir"))

        self.assertEqual(
            [item.path for item in items],
            [
                Path("CourseDir/Semana 01/Semana 01-[10]-resumen.html"),
                Path("CourseDir/Semana 01/info.html"),
                Path("CourseDir/Bibliografía/bibliografia.html"),
            ],
        )
        self.mock_moodle_strategy.can_handle.assert_not_called()
        self.mock_sp_strategy.can_handle.assert_not_called()


class TestInProgressCourseDetection(unittest.TestCase):
    def test_ranks_academic_terms_and_groups_split_sections(self):
        courses = [
            Course(id=1, name="Course A", category="I.01.2025-IIA"),
            Course(id=2, name="Course B", category="I.03.2025-IIA"),
            Course(id=3, name="Course C", category="II.01.2025-III"),
            Course(id=4, name="Course D", category="III.01.2026-I"),
            Course(id=5, name="Course E", category="III.01.2026-II"),
            Course(id=6, name="Course F", category="IV.01.2026-III"),
        ]
        self.assertEqual(get_in_progress_course_ids(courses), {6})
        self.assertEqual(get_in_progress_course_ids(courses[:2]), {1, 2})

    def test_falls_back_to_name_prefix_and_returns_empty_when_unmatched(self):
        courses = [
            Course(id=10, name="IV.01.2026-III PATRONES", category=""),
            Course(id=11, name="III.01.2026-II REDES", category=""),
        ]
        self.assertEqual(get_in_progress_course_ids(courses), {10})
        self.assertEqual(
            get_in_progress_course_ids([Course(id=99, name="General", category="")]),
            set(),
        )
        self.assertEqual(get_in_progress_course_ids([]), set())


if __name__ == "__main__":
    unittest.main()
