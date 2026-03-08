"""
Unit tests for the application services (AuthService, CourseService, DownloadService).
Ports are replaced with AsyncMock — no infrastructure dependencies.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from application.auth_service import AuthService
from application.course_service import CourseService
from application.download_service import DownloadService
from domain.models import ContentItem, Course, CourseFile, ExpandedContentItem, LoginCredentials, RefId, VideoItem
from domain.ports import IAuthPort, ICoursePort, IFilePort

CREDENTIALS = LoginCredentials(
    email="test@unibe.ch",
    password="secret",
    institution_idp="https://aai-idp.unibe.ch/idp/shibboleth",
    base_url="https://ilias.unibe.ch",
)


# ---------------------------------------------------------------------------
# AuthService
# ---------------------------------------------------------------------------

class TestAuthService:

    async def test_login_sets_authenticated(self):
        port = AsyncMock(spec=IAuthPort)
        service = AuthService(port)
        assert service.is_authenticated is False

        result = await service.login(CREDENTIALS)

        port.login.assert_awaited_once_with(CREDENTIALS)
        assert service.is_authenticated is True
        assert result == "Login successful."

    async def test_login_failure_does_not_set_authenticated(self):
        port = AsyncMock(spec=IAuthPort)
        port.login.side_effect = RuntimeError("login failed")
        service = AuthService(port)

        with pytest.raises(RuntimeError, match="login failed"):
            await service.login(CREDENTIALS)

        assert service.is_authenticated is False

    def test_require_authenticated_passes_when_logged_in(self):
        service = AuthService(AsyncMock(spec=IAuthPort))
        service.is_authenticated = True
        service.require_authenticated()  # must not raise

    def test_require_authenticated_raises_when_not_logged_in(self):
        service = AuthService(AsyncMock(spec=IAuthPort))
        with pytest.raises(RuntimeError, match="Not authenticated"):
            service.require_authenticated()


# ---------------------------------------------------------------------------
# CourseService
# ---------------------------------------------------------------------------

class TestCourseService:

    def _make_auth(self, authenticated: bool = True) -> AuthService:
        auth = MagicMock(spec=AuthService)
        auth.is_authenticated = authenticated
        if not authenticated:
            auth.require_authenticated.side_effect = RuntimeError("Not authenticated")
        return auth

    async def test_list_courses_returns_courses(self):
        courses = [Course("Mathe I", "42", "https://ilias.unibe.ch/...")]
        port = AsyncMock(spec=ICoursePort)
        port.get_courses.return_value = courses

        service = CourseService(port, self._make_auth(True))
        result = await service.list_courses()

        assert result == courses
        port.get_courses.assert_awaited_once()

    async def test_list_courses_requires_auth(self):
        port = AsyncMock(spec=ICoursePort)
        service = CourseService(port, self._make_auth(False))

        with pytest.raises(RuntimeError, match="Not authenticated"):
            await service.list_courses()

        port.get_courses.assert_not_awaited()


# ---------------------------------------------------------------------------
# DownloadService
# ---------------------------------------------------------------------------

class TestDownloadService:

    def _make_services(self, authenticated: bool = True):
        auth = MagicMock(spec=AuthService)
        auth.is_authenticated = authenticated
        if not authenticated:
            auth.require_authenticated.side_effect = RuntimeError("Not authenticated")

        course_port = AsyncMock(spec=ICoursePort)
        file_port = AsyncMock(spec=IFilePort)
        course_service = CourseService(course_port, auth)
        return DownloadService(course_service, file_port, auth), course_port, file_port, auth

    async def test_list_course_files_returns_files(self):
        service, _, file_port, _ = self._make_services()
        ref_id = RefId("42")
        files = [CourseFile("Vorlesung.pdf", "https://...")]
        file_port.list_files.return_value = files

        result = await service.list_course_files(ref_id)

        assert result == files
        file_port.list_files.assert_awaited_once_with(ref_id)

    async def test_list_course_files_requires_auth(self):
        service, _, file_port, _ = self._make_services(authenticated=False)
        with pytest.raises(RuntimeError, match="Not authenticated"):
            await service.list_course_files(RefId("42"))

    async def test_download_all_returns_summary(self):
        service, course_port, file_port, _ = self._make_services()
        course_port.get_courses.return_value = [
            Course("Mathe I", "42", "https://ilias.unibe.ch/..."),
        ]
        # Provide a folder section so download_course finds files via list_course_content
        course_port.list_content.return_value = [
            ContentItem("Unterlagen", "99", "https://ilias.unibe.ch/folder", "Ordner"),
        ]
        file_port.list_files.return_value = [
            CourseFile("Blatt1.pdf", "https://..."),
            CourseFile("Blatt2.pdf", "https://..."),
        ]
        file_port.download.return_value = Path("/tmp/Blatt1.pdf")

        result = await service.download_all(Path("/tmp/downloads"))

        assert "New files:  2" in result
        assert "Courses:    1" in result
        assert file_port.download.await_count == 2

    async def test_download_all_requires_auth(self):
        service, _, _, _ = self._make_services(authenticated=False)
        with pytest.raises(RuntimeError, match="Not authenticated"):
            await service.download_all(Path("/tmp"))

    async def test_list_course_content_expands_folder(self):
        service, course_port, file_port, _ = self._make_services()
        folder = ContentItem("Unterlagen", "10", "https://ilias.unibe.ch/folder", "Ordner")
        course_port.list_content.return_value = [folder]
        files = [CourseFile("Blatt1.pdf", "https://ilias.unibe.ch/file1", "pdf")]
        file_port.list_files.return_value = files

        result = await service.list_course_content(RefId("99"))

        assert len(result) == 1
        assert result[0].item == folder
        assert result[0].files == tuple(files)
        assert result[0].videos == ()
        file_port.list_files.assert_awaited_once_with(RefId("10"))

    async def test_list_course_content_expands_opencast(self):
        service, course_port, file_port, _ = self._make_services()
        series = ContentItem("Vorlesungen FS26", "20", "https://ilias.unibe.ch/oc", "Video (Opencast Serie)")
        course_port.list_content.return_value = [series]
        videos = [VideoItem("Lektion 1", "evt1", "2026-03-01", "https://play", "https://dl")]
        course_port.list_videos.return_value = videos

        result = await service.list_course_content(RefId("99"))

        assert len(result) == 1
        assert result[0].item == series
        assert result[0].videos == tuple(videos)
        assert result[0].files == ()
        file_port.list_files.assert_not_awaited()

    async def test_list_course_content_passthrough_for_other_types(self):
        service, course_port, file_port, _ = self._make_services()
        other = ContentItem("Forum", "30", "https://ilias.unibe.ch/forum", "Forum")
        course_port.list_content.return_value = [other]

        result = await service.list_course_content(RefId("99"))

        assert len(result) == 1
        assert result[0] == ExpandedContentItem(item=other)
        file_port.list_files.assert_not_awaited()

    async def test_list_course_content_requires_auth(self):
        service, _, _, _ = self._make_services(authenticated=False)
        with pytest.raises(RuntimeError, match="Not authenticated"):
            await service.list_course_content(RefId("99"))

    async def test_download_all_empty_courses(self):
        service, course_port, file_port, _ = self._make_services()
        course_port.get_courses.return_value = []

        result = await service.download_all(Path("/tmp"))

        assert "New files:  0" in result
        assert "Courses:    0" in result
        file_port.download.assert_not_awaited()
