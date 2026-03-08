"""
Application service — course listing use case.
"""

from application.auth_service import AuthService
from domain.models import ContentItem, Course, RefId, VideoItem
from domain.ports import ICoursePort


class CourseService:
    """Lists courses from ILIAS, enforcing that the user is authenticated."""

    def __init__(self, course_port: ICoursePort, auth_service: AuthService) -> None:
        self._course_port = course_port
        self._auth = auth_service

    async def list_courses(self) -> list[Course]:
        """Return all courses visible on the dashboard."""
        self._auth.require_authenticated()
        return await self._course_port.get_courses()

    async def list_course_content_docs(self, ref_id: RefId) -> list[ContentItem]:
        """Return the top-level INHALT items of a course (folders, podcasts, etc.)."""
        self._auth.require_authenticated()
        return await self._course_port.list_content(ref_id)

    async def list_course_content_video(self, ref_id: RefId) -> list[VideoItem]:
        """Return all Opencast recordings from an xoct series object."""
        self._auth.require_authenticated()
        return await self._course_port.list_videos(ref_id)
