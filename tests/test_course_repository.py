"""Unit tests for CourseRepository and academic term detection."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from idat_sync.application.course_repository import CourseRepository
from idat_sync.application.ports import (
    CourseGateway,
    CredentialsExpiredError,
    RecordingGateway,
    RemoteServiceError,
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
    """Tests for CourseRepository delegation, sanitization, and error handling."""

    def setUp(self):
        """Set up mock gateways and URL content strategies."""
        self.mock_course_gateway = MagicMock(spec=CourseGateway)
        self.mock_recording_gateway = MagicMock(spec=RecordingGateway)

        async def _moodle_list_url(url, filename, last_mod):
            return [MoodleContent(Path(filename), last_mod, url)]

        self.mock_moodle_strategy = MagicMock(spec=UrlContentStrategy)
        self.mock_moodle_strategy.can_handle.side_effect = lambda url: (
            "aulavirtual" in url
        )
        self.mock_moodle_strategy.list_url_content.side_effect = _moodle_list_url

        self.mock_sp_strategy = MagicMock(spec=UrlContentStrategy)
        self.mock_sp_strategy.can_handle.side_effect = lambda url: "sharepoint" in url

        self.strategies = [self.mock_moodle_strategy, self.mock_sp_strategy]
        self.repo = CourseRepository(
            course_gateway=self.mock_course_gateway,
            recording_gateway=self.mock_recording_gateway,
            url_strategies=self.strategies,
        )

    async def test_list_courses_and_populate_course_sections_delegate(
        self,
    ):
        """Delegate list_courses and populate_course_sections to CourseGateway."""
        domain_courses = [
            Course(id=1, name="Algorithms", category="IV.01.2026-III"),
            Course(id=2, name="Databases", category="III.01.2026-II"),
        ]
        self.mock_course_gateway.list_courses.return_value = domain_courses

        async def _return_course(c):
            return c

        self.mock_course_gateway.populate_course_sections.side_effect = _return_course

        result = await self.repo.list_courses()

        assert result == domain_courses
        for c in result:
            assert isinstance(c, Course)
            assert not isinstance(c, dict)

        populated = await self.repo.populate_course_sections(domain_courses[0])
        assert populated is domain_courses[0]
        self.mock_course_gateway.populate_course_sections.assert_called_once_with(
            domain_courses[0]
        )
        assert not hasattr(self.repo, "get_courses")
        assert not hasattr(self.repo, "get_all_courses_contents")

    async def test_get_recordings_delegates_to_gateway_and_wraps_folder(
        self,
    ):
        """Delegate recording lookup and nest items under recordings_folder."""
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

        assert len(results) == 2
        assert results[0].path == Path(
            "CloudComputingDir/Grabaciones/Semana 1/video1.mp4"
        )
        assert results[1].path == Path(
            "CloudComputingDir/Grabaciones/Semana 2/video2.mp4"
        )

    async def test_list_url_content_with_domain_module_content(self):
        """Resolve ModuleContent URLs via matching UrlContentStrategy."""
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

        assert len(contents) == 2
        assert isinstance(contents[0], MoodleContent)
        assert contents[0].path == Path("document.pdf")
        assert contents[1].path == Path("slides.pptx")

    async def test_list_url_content_preserves_subfolders_and_rejects_traversal(
        self,
    ):
        """Sanitize ModuleContent subfolder paths and reject '..' traversal."""
        items = [
            ModuleContent(
                filename="guia.pdf",
                fileurl="https://aulavirtual.idat.edu.pe/1/guia.pdf",
                timemodified=10,
                filepath="/Parte 1/",
            ),
            ModuleContent(
                filename="guia.pdf",
                fileurl="https://aulavirtual.idat.edu.pe/2/guia.pdf",
                timemodified=20,
                filepath="/Tema: 1/./CON/",
            ),
            ModuleContent(
                filename="flat.pdf",
                fileurl="https://aulavirtual.idat.edu.pe/flat.pdf",
                timemodified=30,
                filepath="/",
            ),
        ]

        contents = await self.repo.list_url_content(items)

        assert [c.path for c in contents] == [
            Path("Parte 1/guia.pdf"),
            Path("Tema 1/_CON/guia.pdf"),
            Path("flat.pdf"),
        ]
        self.mock_moodle_strategy.list_url_content.assert_any_call(
            "https://aulavirtual.idat.edu.pe/1/guia.pdf", "guia.pdf", 10
        )

        with pytest.raises(ValueError, match="Unsafe content filepath"):
            await self.repo.list_url_content(
                [
                    ModuleContent(
                        filename="secret.pdf",
                        fileurl="https://aulavirtual.idat.edu.pe/secret.pdf",
                        timemodified=40,
                        filepath="/../outside/",
                    )
                ]
            )

    async def test_get_course_content_builds_all_content_types(
        self,
    ):
        """Build StringContent and MoodleContent trees from Course models."""
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

        assert len(contents) == 6

        # 1. Section summary
        summary_content = contents[0]
        assert isinstance(summary_content, StringContent)
        assert summary_content.path == Path(
            "CloudComputingDir/Semana 01/Semana 01-[101]-resumen.html"
        )
        assert summary_content.contents == ["<p>Introduction to Cloud</p>"]

        # 2. Resource file
        res_file = contents[1]
        assert isinstance(res_file, MoodleContent)
        assert res_file.path == Path(
            "CloudComputingDir/Semana 01/Lecture Notes-[201]/notes.pdf"
        )

        # 3. Label content
        label_content = contents[2]
        assert isinstance(label_content, StringContent)
        assert label_content.path == Path(
            "CloudComputingDir/Semana 01/Important Notice-[202]/Important Notice.html"
        )
        assert label_content.contents == ["<p>Please review syllabus</p>"]

        # 4. URL content
        url_content = contents[3]
        assert isinstance(url_content, MoodleContent)
        assert url_content.path == Path(
            "CloudComputingDir/Semana 01/External Guide-[203]/guide_link"
        )

        # 5. Assignment intro
        assign_intro = contents[4]
        assert isinstance(assign_intro, StringContent)
        expected_intro_path = Path(
            "CloudComputingDir/Semana 01/Project Submission-[204]/"
            "Project Submission-[55]-intro.html"
        )
        assert assign_intro.path == expected_intro_path
        assert assign_intro.contents == ["Submit your zip file here"]

        # 6. Assignment attachment
        assign_att = contents[5]
        assert isinstance(assign_att, MoodleContent)
        assert assign_att.path == Path(
            "CloudComputingDir/Semana 01/Project Submission-[204]/spec.pdf"
        )

    async def test_get_course_content_sanitizes_names_and_skips_empty(
        self,
    ):
        """Sanitize section/module names and skip sections or modules with no name."""
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
                            inline_contents=[
                                StringContent(Path("skip2.html"), 0, ["x"])
                            ],
                        ),
                        CourseModule(
                            id=11,
                            name="CON",
                            inline_contents=[
                                StringContent(Path("nota.html"), 0, ["ok"])
                            ],
                        ),
                    ],
                ),
            ],
        )

        items = await self.repo.get_course_content(course, Path("CourseDir"))
        assert len(items) == 1
        assert items[0].path == Path("CourseDir/Sesión 1 2/_CON-[11]/nota.html")

    async def test_get_course_content_preserves_module_id_suffix(
        self,
    ):
        """Keep module ID suffix intact even when module name exceeds max_len."""
        prefix = "M" * 95
        course = Course(
            id=1,
            name="C1",
            sections=[
                CourseSection(
                    id=10,
                    name="Semana 01",
                    modules=[
                        CourseModule(
                            id=1001,
                            name=f"{prefix}A",
                            contents=[
                                ModuleContent(
                                    filename="guia.pdf",
                                    fileurl="https://aulavirtual.idat.edu.pe/1/guia.pdf",
                                    timemodified=10,
                                    filepath="/Parte 1/",
                                )
                            ],
                        ),
                        CourseModule(
                            id=1002,
                            name=f"{prefix}B",
                            contents=[
                                ModuleContent(
                                    filename="guia.pdf",
                                    fileurl="https://aulavirtual.idat.edu.pe/2/guia.pdf",
                                    timemodified=20,
                                    filepath="/Parte 2/",
                                )
                            ],
                        ),
                    ],
                )
            ],
        )

        items = await self.repo.get_course_content(course, Path("CourseDir"))
        mod_seg_1 = items[0].path.parts[2]
        mod_seg_2 = items[1].path.parts[2]
        assert len(mod_seg_1) <= 100
        assert len(mod_seg_2) <= 100
        assert mod_seg_1.endswith("-[1001]")
        assert mod_seg_2.endswith("-[1002]")
        assert mod_seg_1 != mod_seg_2
        assert items[0].path.parts[-2:] == ("Parte 1", "guia.pdf")
        assert items[1].path.parts[-2:] == ("Parte 2", "guia.pdf")

    def test_invalidate_cache_delegates_to_course_gateway(self):
        """Forward cache invalidation to CourseGateway."""
        self.repo.invalidate_cache()
        self.mock_course_gateway.invalidate_cache.assert_called_once_with()

    async def test_get_course_content_projects_inline_sections_without_strategies(
        self,
    ):
        """Emit section info and bibliografia HTML without invoking URL strategies."""
        course = Course(
            id=1,
            name="C1",
            sections=[
                CourseSection(
                    id=10,
                    name="Semana 01",
                    inline_contents=[
                        StringContent(
                            Path("Semana 01-[10]-resumen.html"), 0, ["<p>S</p>"]
                        ),
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
                            [
                                (
                                    "<p>E-Libro</p>\n"
                                    '<p><a href="https://elibro.net/1">Libro</a></p>'
                                )
                            ],
                        )
                    ],
                ),
            ],
        )

        items = await self.repo.get_course_content(course, Path("CourseDir"))

        assert [item.path for item in items] == [
            Path("CourseDir/Semana 01/Semana 01-[10]-resumen.html"),
            Path("CourseDir/Semana 01/info.html"),
            Path("CourseDir/Bibliografía/bibliografia.html"),
        ]
        self.mock_moodle_strategy.can_handle.assert_not_called()
        self.mock_sp_strategy.can_handle.assert_not_called()

    async def test_list_url_content_skips_broken_link_and_propagates_auth_error(
        self,
    ):
        """Warn and skip RemoteServiceError links, propagating auth and bug errors."""
        self.mock_sp_strategy.list_url_content.side_effect = [
            RemoteServiceError("404 not found"),
            [MoodleContent(Path("valid.pdf"), 20, "https://sharepoint/valid.pdf")],
        ]
        items = [
            ModuleContent("broken.pdf", "https://sharepoint/broken.pdf", 10),
            ModuleContent("valid.pdf", "https://sharepoint/valid.pdf", 20),
        ]

        with self.assertLogs(
            "idat_sync.application.course_repository", level="WARNING"
        ) as cm:
            resolved = await self.repo.list_url_content(items)

        assert [c.path for c in resolved] == [Path("valid.pdf")]
        assert any("404 not found" in msg for msg in cm.output)

        self.mock_sp_strategy.list_url_content.side_effect = CredentialsExpiredError(
            "401 expired"
        )
        with pytest.raises(CredentialsExpiredError):
            await self.repo.list_url_content(items)

        self.mock_sp_strategy.list_url_content.side_effect = RuntimeError("bug")
        with pytest.raises(RuntimeError, match="bug"):
            await self.repo.list_url_content(items)


class TestInProgressCourseDetection(unittest.TestCase):
    """Tests for get_in_progress_course_ids term ranking."""

    def test_ranks_academic_terms_and_groups_split_sections(self):
        """Select all courses sharing the highest academic term."""
        courses = [
            Course(id=1, name="Course A", category="I.01.2025-IIA"),
            Course(id=2, name="Course B", category="I.03.2025-IIA"),
            Course(id=3, name="Course C", category="II.01.2025-III"),
            Course(id=4, name="Course D", category="III.01.2026-I"),
            Course(id=5, name="Course E", category="III.01.2026-II"),
            Course(id=6, name="Course F", category="IV.01.2026-III"),
        ]
        assert get_in_progress_course_ids(courses) == {6}
        assert get_in_progress_course_ids(courses[:2]) == {1, 2}

    def test_falls_back_to_name_prefix_and_returns_empty_when_unmatched(self):
        """Fall back to course name prefix and return empty set when unmatched."""
        courses = [
            Course(id=10, name="IV.01.2026-III PATRONES", category=""),
            Course(id=11, name="III.01.2026-II REDES", category=""),
        ]
        assert get_in_progress_course_ids(courses) == {10}
        assert (
            get_in_progress_course_ids([Course(id=99, name="General", category="")])
            == set()
        )
        assert get_in_progress_course_ids([]) == set()


if __name__ == "__main__":
    unittest.main()
