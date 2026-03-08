"""
Domain ports — abstract interfaces that the application layer depends on.
Concrete implementations live in infrastructure/.
"""

from abc import ABC, abstractmethod
from pathlib import Path

from .models import ContentItem, Course, CourseFile, LoginCredentials, RefId, Semester, VideoItem


class IAuthPort(ABC):
    """Port for authenticating against the ILIAS SSO system."""

    @abstractmethod
    async def login(self, credentials: LoginCredentials) -> None:
        """
        Perform the full SSO login flow.
        Raises RuntimeError if login fails or an unexpected redirect occurs.
        """


class ICoursePort(ABC):
    """Port for reading courses from ILIAS."""

    @abstractmethod
    async def get_semesters(self) -> list[Semester]:
        """Return all available semesters from the ILIAS dashboard."""

    @abstractmethod
    async def get_courses(self, semester_label: str | None = None) -> list[Course]:
        """Return all courses for the given semester label (or current semester if None)."""

    @abstractmethod
    async def list_content(self, ref_id: RefId) -> list[ContentItem]:
        """Return the top-level items from the INHALT section of a course page."""

    @abstractmethod
    async def list_videos(self, ref_id: RefId) -> list[VideoItem]:
        """Return all Opencast recordings from an xoct series object."""


class IFilePort(ABC):
    """Port for listing and downloading files from ILIAS."""

    @abstractmethod
    async def list_files(self, ref_id: RefId) -> list[CourseFile]:
        """Recursively list all downloadable files inside a course container."""

    @abstractmethod
    async def download(self, file: CourseFile, output_dir: Path) -> Path:
        """Download a single file into output_dir and return the saved path."""

    @abstractmethod
    async def download_video(self, video: VideoItem, output_dir: Path) -> Path:
        """Download a single Opencast video into output_dir and return the saved path."""

    @abstractmethod
    async def get_remote_size(self, url: str) -> int | None:
        """Return the Content-Length of a remote file, or None if unavailable."""
