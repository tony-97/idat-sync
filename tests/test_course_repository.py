"""Unit tests for CourseRepository and academic term detection."""

import asyncio
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
        """Resolve a single ModuleContent URL via matching UrlContentStrategy."""
        doc = ModuleContent(
            filename="document.pdf",
            fileurl="https://aulavirtual.idat.edu.pe/document.pdf",
            timemodified=1700000000,
        )
        slides = ModuleContent(
            filename="slides.pptx",
            fileurl="https://aulavirtual.idat.edu.pe/slides.pptx",
            timemodified=1700000100,
        )

        doc_contents = await self.repo.list_url_content(doc)
        slides_contents = await self.repo.list_url_content(slides)

        assert len(doc_contents) == 1
        assert isinstance(doc_contents[0], MoodleContent)
        assert doc_contents[0].path == Path("document.pdf")
        assert len(slides_contents) == 1
        assert slides_contents[0].path == Path("slides.pptx")

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

        contents = [
            resolved
            for item in items
            for resolved in await self.repo.list_url_content(item)
        ]

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
                ModuleContent(
                    filename="secret.pdf",
                    fileurl="https://aulavirtual.idat.edu.pe/secret.pdf",
                    timemodified=40,
                    filepath="/../outside/",
                )
            )

    async def test_stream_course_content_builds_all_content_types(
        self,
    ):
        """Stream inline StringContent items first, then resolved module Content."""
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

        contents = [
            item
            async for item in self.repo.stream_course_content(
                course, course_dir="CloudComputingDir"
            )
        ]

        assert len(contents) == 6
        assert not hasattr(self.repo, "get_course_content")
        assert not hasattr(self.repo, "iter_course_remote_tree")

        # Inline items yielded first in section/module order
        assert [item.path for item in contents[:3]] == [
            Path("CloudComputingDir/Semana 01/Semana 01-[101]-resumen.html"),
            Path(
                "CloudComputingDir/Semana 01/Important Notice-[202]/"
                "Important Notice.html"
            ),
            Path(
                "CloudComputingDir/Semana 01/Project Submission-[204]/"
                "Project Submission-[55]-intro.html"
            ),
        ]
        assert all(isinstance(item, StringContent) for item in contents[:3])

        resolved_by_path = {item.path: item for item in contents[3:]}
        assert set(resolved_by_path) == {
            Path("CloudComputingDir/Semana 01/Lecture Notes-[201]/notes.pdf"),
            Path("CloudComputingDir/Semana 01/External Guide-[203]/guide_link"),
            Path("CloudComputingDir/Semana 01/Project Submission-[204]/spec.pdf"),
        }
        assert all(
            isinstance(item, MoodleContent) for item in resolved_by_path.values()
        )

    async def test_stream_course_content_sanitizes_names_and_skips_empty(
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

        items = [
            item
            async for item in self.repo.stream_course_content(course, Path("CourseDir"))
        ]
        assert len(items) == 1
        assert items[0].path == Path("CourseDir/Sesión 1 2/_CON-[11]/nota.html")

    async def test_stream_course_content_preserves_module_id_suffix(
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

        items = [
            item
            async for item in self.repo.stream_course_content(course, Path("CourseDir"))
        ]
        by_part = {item.path.parts[-2]: item for item in items}
        mod_seg_1 = by_part["Parte 1"].path.parts[2]
        mod_seg_2 = by_part["Parte 2"].path.parts[2]
        assert len(mod_seg_1) <= 100
        assert len(mod_seg_2) <= 100
        assert mod_seg_1.endswith("-[1001]")
        assert mod_seg_2.endswith("-[1002]")
        assert mod_seg_1 != mod_seg_2

    def test_invalidate_cache_delegates_to_course_gateway(self):
        """Forward cache invalidation to CourseGateway."""
        self.repo.invalidate_cache()
        self.mock_course_gateway.invalidate_cache.assert_called_once_with()

    async def test_stream_course_content_projects_inline_sections_without_strategies(
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

        items = [
            item
            async for item in self.repo.stream_course_content(course, Path("CourseDir"))
        ]

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
        broken = ModuleContent("broken.pdf", "https://sharepoint/broken.pdf", 10)
        valid = ModuleContent("valid.pdf", "https://sharepoint/valid.pdf", 20)

        with self.assertLogs(
            "idat_sync.application.course_repository", level="WARNING"
        ) as cm:
            broken_resolved = await self.repo.list_url_content(broken)
        valid_resolved = await self.repo.list_url_content(valid)

        assert broken_resolved == []
        assert [c.path for c in valid_resolved] == [Path("valid.pdf")]
        assert any("404 not found" in msg for msg in cm.output)

        self.mock_sp_strategy.list_url_content.side_effect = CredentialsExpiredError(
            "401 expired"
        )
        with pytest.raises(CredentialsExpiredError):
            await self.repo.list_url_content(broken)

        self.mock_sp_strategy.list_url_content.side_effect = RuntimeError("bug")
        with pytest.raises(RuntimeError, match="bug"):
            await self.repo.list_url_content(broken)

    async def test_stream_course_content_overlaps_modules_and_preserves_intra_order(
        self,
    ):
        """Yield fast modules before slow ones while keeping intra-module order."""
        release_slow = asyncio.Event()
        call_order: list[str] = []

        async def _sp_list_url(url, filename, last_mod):
            call_order.append(filename)
            if "slow" in url:
                await release_slow.wait()
            return [MoodleContent(Path(filename), last_mod, url)]

        self.mock_sp_strategy.list_url_content.side_effect = _sp_list_url
        course = Course(
            id=1,
            name="C1",
            sections=[
                CourseSection(
                    id=10,
                    name="S1",
                    inline_contents=[StringContent(Path("inline.html"), 0, ["ok"])],
                    modules=[
                        CourseModule(
                            id=1,
                            name="SlowMod",
                            contents=[
                                ModuleContent("slow.pdf", "https://sharepoint/slow", 1)
                            ],
                        ),
                        CourseModule(
                            id=2,
                            name="FastMod",
                            contents=[
                                ModuleContent(
                                    "first.pdf", "https://sharepoint/first", 2
                                ),
                                ModuleContent(
                                    "second.pdf", "https://sharepoint/second", 3
                                ),
                            ],
                        ),
                    ],
                )
            ],
        )

        yielded: list[Path] = []
        async for item in self.repo.stream_course_content(course, Path("Course")):
            yielded.append(item.path)
            if len(yielded) == 3:
                release_slow.set()

        assert yielded == [
            Path("Course/S1/inline.html"),
            Path("Course/S1/FastMod-[2]/first.pdf"),
            Path("Course/S1/FastMod-[2]/second.pdf"),
            Path("Course/S1/SlowMod-[1]/slow.pdf"),
        ]
        assert call_order.index("first.pdf") < call_order.index("second.pdf")

    async def test_stream_course_content_cancels_unfinished_tasks_cleanly(self):  # pylint: disable=too-many-statements
        """Cancel unfinished module tasks on early exit, auth error, or bad plan."""
        other_mod_cancelled = asyncio.Event()
        other_started = asyncio.Event()
        after_auth_called = False

        async def _sp_cancel_on_auth(url, filename, last_mod):
            nonlocal after_auth_called
            if "auth" in url:
                await other_started.wait()
                raise CredentialsExpiredError("401 expired")
            if "after" in url:
                after_auth_called = True
                return []
            other_started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                other_mod_cancelled.set()
                raise
            return [MoodleContent(Path(filename), last_mod, url)]

        self.mock_sp_strategy.list_url_content.side_effect = _sp_cancel_on_auth
        course = Course(
            id=1,
            name="C1",
            sections=[
                CourseSection(
                    id=10,
                    name="S1",
                    inline_contents=[StringContent(Path("inline.html"), 0, ["ok"])],
                    modules=[
                        CourseModule(
                            id=1,
                            name="ModA",
                            contents=[
                                ModuleContent("auth.pdf", "https://sharepoint/auth", 1),
                                ModuleContent(
                                    "after.pdf", "https://sharepoint/after", 2
                                ),
                            ],
                        ),
                        CourseModule(
                            id=2,
                            name="ModB",
                            contents=[
                                ModuleContent(
                                    "other.pdf", "https://sharepoint/other", 3
                                ),
                            ],
                        ),
                    ],
                )
            ],
        )

        # Early break cancels all module tasks without leaking
        early_gen = self.repo.stream_course_content(course, Path("Course"))
        first_item = await anext(early_gen)
        assert first_item.path == Path("Course/S1/inline.html")
        await other_started.wait()
        await early_gen.aclose()
        assert other_mod_cancelled.is_set()

        # Cancelling the consuming task cancels unfinished module tasks
        other_mod_cancelled.clear()
        other_started.clear()
        slow_only_course = Course(
            id=3,
            name="C3",
            sections=[
                CourseSection(
                    id=30,
                    name="S1",
                    modules=[
                        CourseModule(
                            id=2,
                            name="ModB",
                            contents=[
                                ModuleContent(
                                    "other.pdf", "https://sharepoint/other", 3
                                ),
                            ],
                        ),
                    ],
                )
            ],
        )

        async def _consume_until_cancelled() -> None:
            async for _ in self.repo.stream_course_content(
                slow_only_course, Path("Course")
            ):
                pass

        consumer = asyncio.create_task(_consume_until_cancelled())
        await other_started.wait()
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        assert other_mod_cancelled.is_set()

        # CredentialsExpiredError cancels other module tasks and stops ModA
        other_mod_cancelled.clear()
        other_started.clear()
        with pytest.raises(CredentialsExpiredError):
            _ = [
                item
                async for item in self.repo.stream_course_content(
                    course, Path("Course")
                )
            ]
        assert other_mod_cancelled.is_set()
        assert not after_auth_called

        # Bad inline path in a later section raises before spawning any tasks
        self.mock_sp_strategy.list_url_content.reset_mock()
        bad_course = Course(
            id=2,
            name="C2",
            sections=[
                CourseSection(
                    id=1,
                    name="S1",
                    modules=[
                        CourseModule(
                            id=1,
                            name="Mod1",
                            contents=[
                                ModuleContent("ok.pdf", "https://sharepoint/ok", 1)
                            ],
                        )
                    ],
                ),
                CourseSection(
                    id=2,
                    name="S2",
                    inline_contents=[StringContent(Path("../escape.html"), 0, ["bad"])],
                ),
            ],
        )
        with pytest.raises(ValueError, match="Unsafe content path"):
            _ = [
                item
                async for item in self.repo.stream_course_content(
                    bad_course, Path("Course")
                )
            ]
        await asyncio.sleep(0)
        self.mock_sp_strategy.list_url_content.assert_not_called()


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
