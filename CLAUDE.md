# ilias-mcp — Complete Reconstruction Specification

An MCP server that connects to the ILIAS university learning platform (ilias.unibe.ch, Uni Bern)
via Switch edu-ID SSO. Allows an AI assistant to list courses, explore content, and download
files/videos from ILIAS through a headless Chromium browser (Playwright).

---

## Project layout

```
ilias-mcp/
├── server.py                          # MCP entry point, lifespan wiring
├── pyproject.toml
├── .env.example
├── domain/
│   ├── __init__.py
│   ├── models.py                      # Frozen dataclass value objects
│   ├── ports.py                       # Abstract interfaces (ABCs)
│   └── utils.py                       # sanitize_filename helper
├── application/
│   ├── __init__.py
│   ├── auth_service.py
│   ├── course_service.py
│   └── download_service.py
├── infrastructure/
│   ├── __init__.py
│   ├── browser.py                     # BrowserManager (Playwright lifecycle)
│   └── ilias_adapters.py              # Concrete port implementations
├── interface/
│   ├── __init__.py
│   ├── context.py                     # AppContext, RateLimiter
│   └── tools/
│       ├── __init__.py
│       ├── auth_tools.py
│       ├── course_tools.py
│       └── download_tools.py
└── tests/
    ├── __init__.py
    ├── test_server.py                 # RefId, RateLimiter unit tests
    ├── test_application.py            # Service layer unit tests (AsyncMock ports)
    ├── test_infrastructure.py         # Adapter unit tests (mocked Playwright)
    └── test_ilias_client.py           # (legacy, kept for reference)
```

**Dependency flow (DDD):**
```
server.py → interface/ → application/ → domain/ports ← infrastructure/
```
Infrastructure depends on domain ports (inward). Application depends on ports. Interface depends
on application. Nothing in domain/ depends on any other layer.

---

## pyproject.toml

```toml
[project]
name = "ilias-mcp"
version = "0.1.0"
description = "MCP server for ILIAS university platform via Switch edu-ID"
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.0.0",
    "playwright>=1.40.0",
    "python-dotenv>=1.0.0",
]

[project.scripts]
ilias-mcp = "server:main"

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-mock>=3.12.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[dependency-groups]
dev = [
    "mcp[cli]>=1.26.0",
]

[tool.hatch.build.targets.wheel]
packages = ["."]
```

---

## .env.example

```
ILIAS_URL=https://ilias.unibe.ch
ILIAS_INSTITUTION=https://aai-idp.unibe.ch/idp/shibboleth
SWITCH_EDU_ID_EMAIL=your.name@students.unibe.ch
SWITCH_EDU_ID_PASSWORD=yourpassword
DOWNLOAD_DIR=./ilias_downloads
MAX_FILENAME_LEN=128
MAX_DIRNAME_LEN=64
VIDEO_QUALITY=lowest
```

`ILIAS_INSTITUTION` is the Shibboleth IdP entity-ID passed to the `select#user_idp` dropdown.
`VIDEO_QUALITY` accepts `lowest` (smallest file) or `highest` (best resolution).
`RATE_LIMIT_CALLS_PER_MIN` (optional, default 20) controls the per-tool rate limiter.

---

## domain/models.py

Pure frozen dataclasses; no external dependencies.

```python
"""
Domain models — pure value objects with no external dependencies.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LoginCredentials:
    """Value object that carries all data needed to authenticate against ILIAS."""
    email: str
    password: str
    institution_idp: str  # Shibboleth IdP entity-ID, e.g. https://aai-idp.unibe.ch/idp/shibboleth
    base_url: str         # ILIAS root URL, e.g. https://ilias.unibe.ch


@dataclass(frozen=True)
class Course:
    """Represents a single ILIAS course."""
    title: str
    ref_id: str
    url: str


@dataclass(frozen=True)
class VideoItem:
    """Represents a single Opencast video/recording in a course."""
    title: str
    event_id: str
    date: str
    url: str           # stream/play URL
    download_url: str  # direct download URL at selected resolution
    subtitle_url: str = ""  # subtitle download URL (empty if not available)


@dataclass(frozen=True)
class ContentItem:
    """Represents a top-level item in a course's INHALT section (folder, podcast, etc.)."""
    title: str
    ref_id: str
    url: str
    item_type: str  # human-readable type label from the icon alt text, e.g. "Ordner"


@dataclass(frozen=True)
class CourseFile:
    """Represents a downloadable file inside a course."""
    title: str
    url: str
    file_type: str = ""   # e.g. "pdf", "doc", "xlsx" — empty if unknown
    file_name: str = ""   # actual filename with extension, e.g. "report.pdf" — empty if unknown


@dataclass(frozen=True)
class ExpandedContentItem:
    """A course content item with its expanded sub-items (files or videos)."""
    item: ContentItem
    files: tuple["CourseFile", ...] = ()   # populated for folder items
    videos: tuple["VideoItem", ...] = ()   # populated for Opencast series


@dataclass(frozen=True)
class RefId:
    """
    Value object for an ILIAS repository reference ID.
    Validates on construction — raises ValueError for invalid values.
    """
    value: str

    def __post_init__(self) -> None:
        if not self.value.isdigit() or int(self.value) <= 0:
            raise ValueError(
                f"Invalid ref_id '{self.value}': must be a positive integer string."
            )

    def __str__(self) -> str:
        return self.value
```

---

## domain/ports.py

Abstract interfaces (ABCs) that the application layer depends on.

```python
"""
Domain ports — abstract interfaces that the application layer depends on.
Concrete implementations live in infrastructure/.
"""

from abc import ABC, abstractmethod
from pathlib import Path

from .models import ContentItem, Course, CourseFile, LoginCredentials, RefId, VideoItem


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
    async def get_courses(self) -> list[Course]:
        """Return all courses visible on the ILIAS dashboard."""

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
```

---

## domain/utils.py

```python
"""
Domain utilities — pure helper functions with no external dependencies.
"""

import re


def sanitize_filename(name: str, max_len: int) -> str:
    """
    Sanitize a filename (replace forbidden chars) and truncate so the total
    length does not exceed max_len characters, while preserving the extension.
    """
    safe = re.sub(r'[<>:"/\\|?*]', "_", name).strip()
    stem, dot, ext = safe.rpartition(".")
    if stem:
        ext_part = dot + ext
        return stem[: max(0, max_len - len(ext_part))] + ext_part
    return safe[:max_len]
```

---

## application/auth_service.py

```python
"""
Application service — authentication use case.
"""

from domain.models import LoginCredentials
from domain.ports import IAuthPort


class AuthService:
    """
    Orchestrates the login use case and tracks authentication state.
    Does not know how login is performed — that is the responsibility of IAuthPort.
    """

    def __init__(self, auth_port: IAuthPort) -> None:
        self._auth_port = auth_port
        self.is_authenticated: bool = False

    async def login(self, credentials: LoginCredentials) -> str:
        """
        Execute the login flow via the injected port.
        Sets is_authenticated = True on success, raises on failure.
        """
        await self._auth_port.login(credentials)
        self.is_authenticated = True
        return "Login successful."

    def require_authenticated(self) -> None:
        """Raise RuntimeError if the user has not logged in yet."""
        if not self.is_authenticated:
            raise RuntimeError(
                "Not authenticated. Call the 'login' tool before using other tools."
            )
```

---

## application/course_service.py

```python
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
```

---

## application/download_service.py

```python
"""
Application service — file download use case.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from application.auth_service import AuthService
from application.course_service import CourseService
from domain.models import CourseFile, ExpandedContentItem, RefId, VideoItem
from domain.ports import IFilePort
from domain.utils import sanitize_filename

# Async or sync progress callback: receives a single status string.
ProgressCB = Callable[[str], Any] | None


@dataclass
class FileEntry:
    course: str
    name: str
    status: str        # "pending" | "active" | "done" | "skipped" | "error"
    size_bytes: int = 0
    error: str = ""


@dataclass
class DownloadTracker:
    entries: list[FileEntry] = field(default_factory=list)
    total_done: int = 0
    total_skipped: int = 0
    total_error: int = 0
    active_name: str = ""

    def reset(self) -> None:
        self.entries.clear()
        self.total_done = 0
        self.total_skipped = 0
        self.total_error = 0
        self.active_name = ""


class DownloadService:
    """
    Orchestrates listing and downloading files from ILIAS courses.
    Delegates storage concerns to IFilePort; business rules live here.
    """

    def __init__(
        self,
        course_service: CourseService,
        file_port: IFilePort,
        auth_service: AuthService,
        max_filename_len: int = 64,
        max_dirname_len: int = 64,
    ) -> None:
        self._course_service = course_service
        self._file_port = file_port
        self._auth = auth_service
        self._max_filename_len = max_filename_len
        self._max_dirname_len = max_dirname_len

    async def list_course_content(self, ref_id: RefId) -> list[ExpandedContentItem]:
        """
        List all content items of a course with sub-items expanded:
        - Folder items include their files (with download URLs).
        - Opencast series items include their video recordings.
        - Top-level document items are wrapped as a single-file entry.
        """
        self._auth.require_authenticated()
        items = await self._course_service.list_course_content_docs(ref_id)
        result: list[ExpandedContentItem] = []
        for item in items:
            t = item.item_type.lower()
            if "ordner" in t:
                files = await self._file_port.list_files(RefId(item.ref_id))
                result.append(ExpandedContentItem(item=item, files=tuple(files)))
            elif "opencast" in t:
                videos = await self._course_service.list_course_content_video(RefId(item.ref_id))
                result.append(ExpandedContentItem(item=item, videos=tuple(videos)))
            elif "datei" in t or "file" in t:
                # Top-level document — treat as a single downloadable file.
                # Try to derive the file extension:
                #   1. Title already ends with one ("Report.pdf") → use as-is.
                #   2. item_type prefix carries it ("pdf Datei" → "pdf") → append.
                #   3. Neither → leave file_name empty; download falls back to suggested_filename.
                type_prefix = re.sub(r"[\s\-]*(datei|file)", "", t).strip()
                file_type = type_prefix if re.match(r"^[a-z0-9]{2,5}$", type_prefix) else ""
                if re.search(r"\.\w{2,5}$", item.title):
                    file_name = item.title
                elif file_type:
                    file_name = f"{item.title}.{file_type}"
                else:
                    file_name = ""
                doc_file = CourseFile(title=item.title, url=item.url, file_type=file_type, file_name=file_name)
                result.append(ExpandedContentItem(item=item, files=(doc_file,)))
            else:
                result.append(ExpandedContentItem(item=item))
        return result

    async def list_course_files(self, ref_id: RefId) -> list[CourseFile]:
        """Recursively list all downloadable files in a course."""
        self._auth.require_authenticated()
        return await self._file_port.list_files(ref_id)

    async def download_course(
        self,
        ref_id: RefId,
        output_dir: Path,
        tracker: DownloadTracker | None = None,
        log: ProgressCB = None,
        sections: list[ExpandedContentItem] | None = None,
    ) -> tuple[int, int]:
        """
        Download all files of a single course into output_dir/<course_title>/.
        Returns (downloaded, skipped) counts.
        If *sections* is provided (pre-fetched via list_course_content), the
        content scan is skipped; otherwise list_course_content is called here.
        """
        self._auth.require_authenticated()
        courses = await self._course_service.list_courses()
        course = next((c for c in courses if c.ref_id == ref_id.value), None)
        if course is None:
            raise ValueError(f"Course with ref_id={ref_id.value} not found.")

        course_dir = output_dir / _safe_name(course.title, self._max_dirname_len)
        course_dir.mkdir(parents=True, exist_ok=True)
        if sections is None:
            if log:
                result = log(f"[{course.title}] Scanning course content...")
                if isinstance(result, Awaitable):
                    await result
            sections = await self.list_course_content(ref_id)

        # Determine per-section target directory:
        # - Folder (ordner) and Opencast (video) sections get a named subfolder.
        # - Top-level documents and other items go directly into the course directory.
        def _section_dir(section: ExpandedContentItem) -> Path:
            t = section.item.item_type.lower()
            if "ordner" in t or "opencast" in t:
                return course_dir / _safe_name(section.item.title, self._max_dirname_len)
            return course_dir

        total_files = sum(len(s.files) for s in sections)
        total_videos = sum(len(s.videos) for s in sections)
        if log:
            result = log(
                f"[{course.title}] Found {total_files} file(s) and {total_videos} video(s)."
            )
            if isinstance(result, Awaitable):
                await result

        # Pre-register all entries in the tracker
        if tracker is not None:
            for section in sections:
                target_dir = _section_dir(section)
                for file in section.files:
                    expected_path = target_dir / _safe_name(file.file_name or file.title, self._max_filename_len)
                    exists = expected_path.exists()
                    entry = FileEntry(
                        course=course.title,
                        name=file.file_name or file.title,
                        status="skipped" if exists else "pending",
                    )
                    if exists:
                        entry.size_bytes = expected_path.stat().st_size
                        tracker.total_skipped += 1
                    tracker.entries.append(entry)
                for video in section.videos:
                    existing_path = _video_expected_path(video, target_dir, self._max_filename_len)
                    exists = existing_path is not None
                    entry = FileEntry(
                        course=course.title,
                        name=video.title,
                        status="skipped" if exists else "pending",
                    )
                    if exists:
                        entry.size_bytes = existing_path.stat().st_size
                        tracker.total_skipped += 1
                    tracker.entries.append(entry)

        downloaded = 0
        skipped = 0

        for section in sections:
            target_dir = _section_dir(section)
            target_dir.mkdir(parents=True, exist_ok=True)

            for file in section.files:
                raw_name = file.file_name or file.title
                expected_path = target_dir / _safe_name(raw_name, self._max_filename_len)
                if expected_path.exists():
                    remote_size = await self._file_port.get_remote_size(file.url)
                    local_size = expected_path.stat().st_size
                    if remote_size is None or remote_size == local_size:
                        skipped += 1
                        if log:
                            result = log(f"[{course.title}] Skipping (exists): {raw_name}")
                            if isinstance(result, Awaitable):
                                await result
                        continue
                    # Size mismatch — re-download and overwrite.

                entry = None
                if tracker is not None:
                    tracker.active_name = raw_name
                    entry = next(
                        (e for e in tracker.entries if e.name == raw_name and e.course == course.title),
                        None,
                    )
                    if entry:
                        entry.status = "active"

                if log:
                    result = log(f"[{course.title}] Downloading: {raw_name}")
                    if isinstance(result, Awaitable):
                        await result

                try:
                    save_path = await self._file_port.download(file, target_dir)
                    downloaded += 1
                    size = save_path.stat().st_size if save_path.exists() else 0
                    if log:
                        size_str = f" ({size / 1_048_576:.2f} MB)" if size else ""
                        result = log(f"[{course.title}] Saved: {raw_name}{size_str}")
                        if isinstance(result, Awaitable):
                            await result
                    if tracker is not None:
                        if entry:
                            entry.status = "done"
                            entry.size_bytes = size
                        tracker.total_done += 1
                except Exception as exc:
                    if tracker is not None:
                        if entry:
                            entry.status = "error"
                            entry.error = str(exc)
                        tracker.total_error += 1
                    raise
                finally:
                    if tracker is not None:
                        tracker.active_name = ""

            for video in section.videos:
                if _video_expected_path(video, target_dir, self._max_filename_len) is not None:
                    skipped += 1
                    if log:
                        result = log(f"[{course.title}] Skipping (exists): {video.title}")
                        if isinstance(result, Awaitable):
                            await result
                    continue

                entry = None
                if tracker is not None:
                    tracker.active_name = video.title
                    entry = next(
                        (e for e in tracker.entries if e.name == video.title and e.course == course.title),
                        None,
                    )
                    if entry:
                        entry.status = "active"

                if log:
                    result = log(f"[{course.title}] Downloading video: {video.title}")
                    if isinstance(result, Awaitable):
                        await result

                try:
                    save_path = await self._file_port.download_video(video, target_dir)
                    downloaded += 1
                    size = save_path.stat().st_size if save_path.exists() else 0
                    if log:
                        size_str = f" ({size / 1_048_576:.2f} MB)" if size else ""
                        result = log(f"[{course.title}] Saved video: {video.title}{size_str}")
                        if isinstance(result, Awaitable):
                            await result
                    if tracker is not None:
                        if entry:
                            entry.status = "done"
                            entry.size_bytes = size
                        tracker.total_done += 1
                except Exception as exc:
                    if tracker is not None:
                        if entry:
                            entry.status = "error"
                            entry.error = str(exc)
                        tracker.total_error += 1
                    raise
                finally:
                    if tracker is not None:
                        tracker.active_name = ""

        return downloaded, skipped

    async def download_all(
        self,
        output_dir: Path,
        tracker: DownloadTracker | None = None,
        log: ProgressCB = None,
    ) -> str:
        """
        Download every file from every course into output_dir/<course_title>/.
        Skips files that already exist. Returns a human-readable summary report.

        Two-phase approach:
        1. Scan all courses via list_course_content to discover their content.
        2. Download each course via download_course, reusing the pre-fetched content.
        """
        self._auth.require_authenticated()
        if tracker is not None:
            tracker.reset()

        courses = await self._course_service.list_courses()

        # Phase 1: discover content of every course
        if log:
            result = log(f"Scanning {len(courses)} course(s) for content...")
            if isinstance(result, Awaitable):
                await result

        course_sections: dict[int, list[ExpandedContentItem]] = {}
        for course in courses:
            sections = await self.list_course_content(RefId(course.ref_id))
            course_sections[course.ref_id] = sections
            if log:
                file_count = sum(len(s.files) for s in sections)
                video_count = sum(len(s.videos) for s in sections)
                result = log(
                    f"[{course.title}] Found {file_count} file(s) and {video_count} video(s)."
                )
                if isinstance(result, Awaitable):
                    await result

        # Phase 2: download each course using the pre-fetched content
        total_downloaded = 0
        total_skipped = 0

        for course in courses:
            downloaded, skipped = await self.download_course(
                RefId(course.ref_id),
                output_dir,
                tracker,
                log=log,
                sections=course_sections[course.ref_id],
            )
            total_downloaded += downloaded
            total_skipped += skipped

        return _build_summary(total_downloaded, total_skipped, len(courses), output_dir, tracker)


def _safe_name(name: str, max_len: int = 64) -> str:
    return sanitize_filename(name, max_len)


def _video_expected_path(video: VideoItem, course_dir: Path, max_len: int) -> Path | None:
    """
    Return the local path where a video would be saved.
    Mirrors the filename logic in IliasFileAdapter.download_video, assuming .mp4
    as the default suffix.  Returns None if course_dir does not exist yet and no
    glob match is found (the download has to happen first).
    """
    safe_title = re.sub(r'[<>:"/\\|?*]', "_", video.title)
    date_part = f"_{video.date}" if video.date else ""
    raw_stem = f"{safe_title}{date_part}"
    # Primary guess: .mp4 (default in download_video)
    expected = course_dir / sanitize_filename(f"{raw_stem}.mp4", max_len)
    if expected.exists():
        return expected
    # Fallback: any file with the same stem but different extension
    if course_dir.exists():
        stem = Path(sanitize_filename(f"{raw_stem}.mp4", max_len)).stem
        matches = list(course_dir.glob(f"{glob_escape(stem)}.*"))
        if matches:
            return matches[0]
    return None


def glob_escape(s: str) -> str:
    """Escape glob special characters in a literal path segment."""
    return re.sub(r"([\[\]*?])", r"[\1]", s)


def _fmt_size(size_bytes: int) -> str:
    if size_bytes >= 1_048_576:
        return f"{size_bytes / 1_048_576:.2f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def _build_summary(
    total_downloaded: int,
    total_skipped: int,
    num_courses: int,
    output_dir: Path,
    tracker: DownloadTracker | None,
) -> str:
    lines: list[str] = [
        f"=== Download Summary ===",
        f"Courses:    {num_courses}",
        f"New files:  {total_downloaded}",
        f"Skipped:    {total_skipped} (already existed)",
        f"Errors:     {tracker.total_error if tracker else 0}",
        f"Output dir: {output_dir}",
    ]

    if tracker:
        new_entries = [e for e in tracker.entries if e.status == "done"]
        if new_entries:
            lines.append("\nNewly downloaded:")
            for e in new_entries:
                size_str = f"  {_fmt_size(e.size_bytes)}" if e.size_bytes else ""
                lines.append(f"  [{e.course}] {e.name}{size_str}")

        skipped_entries = [e for e in tracker.entries if e.status == "skipped"]
        if skipped_entries:
            lines.append("\nAlready existed (skipped):")
            for e in skipped_entries:
                size_str = f"  {_fmt_size(e.size_bytes)}" if e.size_bytes else ""
                lines.append(f"  [{e.course}] {e.name}{size_str}")

        error_entries = [e for e in tracker.entries if e.status == "error"]
        if error_entries:
            lines.append("\nErrors:")
            for e in error_entries:
                lines.append(f"  [{e.course}] {e.name}  ERROR: {e.error}")

    return "\n".join(lines)
```

---

## infrastructure/browser.py

```python
"""
Infrastructure — Playwright browser lifecycle management.
"""

import logging
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    Page,
    Playwright,
)

logger = logging.getLogger(__name__)


class BrowserManager:
    """
    Manages a single headless Chromium browser instance.
    One shared instance is created per MCP server lifespan and injected
    into all infrastructure adapters.
    """

    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    async def start(self) -> None:
        """Launch the browser. Must be called before accessing .page."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._page = await self._browser.new_page()
        logger.debug("Browser started.")

    async def stop(self) -> None:
        """Close the browser and release Playwright resources."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.debug("Browser stopped.")

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("BrowserManager not started. Call start() first.")
        return self._page
```

---

## infrastructure/ilias_adapters.py

Three concrete port implementations. Critical browser selectors documented inline.

```python
"""
Infrastructure — concrete Playwright implementations of the domain ports.

Three adapters, each responsible for exactly one bounded context:
  IliasAuthAdapter   — SSO login via Switch edu-ID
  IliasCourseAdapter — reading courses from the ILIAS dashboard
  IliasFileAdapter   — listing and downloading files from ILIAS
"""

import logging
import re
from pathlib import Path

from playwright.async_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

from domain.models import ContentItem, Course, CourseFile, LoginCredentials, RefId, VideoItem
from domain.ports import IAuthPort, ICoursePort, IFilePort
from domain.utils import sanitize_filename
from infrastructure.browser import BrowserManager

logger = logging.getLogger(__name__)

# Hostname of the Switch edu-ID login service
_EDU_ID_HOST = "login.eduid.ch"


# ---------------------------------------------------------------------------
# Auth adapter
# ---------------------------------------------------------------------------

class IliasAuthAdapter(IAuthPort):
    """
    Drives the Switch edu-ID SSO login flow through a headless browser.

    Confirmed selectors (ilias.unibe.ch, ILIAS v9.17 / unibe.login.eduid.ch):
      ILIAS page:   select#user_idp, input#wayf_submit_button
      edu-ID page:  input#username (name=j_username), button#button-submit
    """

    def __init__(self, browser: BrowserManager) -> None:
        self._browser = browser

    async def login(self, credentials: LoginCredentials) -> None:
        """
        Full login flow:
          1. ILIAS /login.php  →  select institution  →  click Fortfahren
          2. edu-ID page       →  email  →  Weiter  →  password  →  Anmelden
          3. Consent page      →  accept if present
          4. Verify redirect back to ILIAS
        """
        page = self._browser.page

        # Step 1 – institution selection
        await page.goto(f"{credentials.base_url.rstrip('/')}/login.php")
        await page.wait_for_load_state("networkidle")
        await self._select_institution(page, credentials)
        await page.wait_for_load_state("networkidle")

        # Step 2 – edu-ID credentials
        if _EDU_ID_HOST not in page.url:
            raise RuntimeError(
                f"Expected to land on '{_EDU_ID_HOST}' after institution selection, "
                f"but got: {page.url}"
            )
        logger.info("On Switch edu-ID login page (%s).", page.url)
        await self._enter_email(page, credentials.email)
        await page.wait_for_load_state("networkidle")
        await self._enter_password(page, credentials.password)
        await page.wait_for_load_state("networkidle")

        # Step 3 – consent
        await self._accept_consent(page)
        await page.wait_for_load_state("networkidle")

        # Step 4 – verify
        if credentials.base_url.rstrip("/") not in page.url:
            raise RuntimeError(
                f"Login failed — not redirected back to ILIAS. "
                f"Current URL: {page.url}"
            )
        logger.info("Login successful.")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _select_institution(self, page, credentials: LoginCredentials) -> None:
        try:
            await page.select_option(
                "select#user_idp",
                value=credentials.institution_idp,
                timeout=10_000,
            )
            logger.debug("Selected institution '%s'.", credentials.institution_idp)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                f"Institution dropdown (select#user_idp) not found on {page.url}"
            ) from exc

        try:
            await page.click("input#wayf_submit_button", timeout=5_000)
            logger.debug("Clicked 'Fortfahren'.")
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                "Submit button (input#wayf_submit_button) not found on login page."
            ) from exc

    async def _enter_email(self, page, email: str) -> None:
        try:
            field = page.locator("input#username, input[name='j_username']").first
            await field.wait_for(state="visible", timeout=10_000)
            await field.fill(email)
            logger.debug("E-mail filled.")
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                f"E-mail field not found on edu-ID page. URL: {page.url}"
            ) from exc

        try:
            await page.click("button#button-submit", timeout=5_000)
            logger.debug("Clicked 'Weiter'.")
        except PlaywrightTimeoutError as exc:
            raise RuntimeError("'Weiter' button not found after e-mail field.") from exc

    async def _enter_password(self, page, password: str) -> None:
        try:
            pwd = page.locator("input[type='password']").first
            await pwd.wait_for(state="visible", timeout=10_000)
            await pwd.fill(password)
            logger.debug("Password filled.")
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                f"Password field did not appear on edu-ID page. URL: {page.url}"
            ) from exc

        # Try multiple selectors — the password page may differ from the email page
        for selector in [
            "button#button-submit",
            "button[type='submit']",
            "input[type='submit']",
            "button.btn-primary",
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    await btn.click(timeout=5_000)
                    logger.debug("Clicked submit after password via '%s'.", selector)
                    return
            except PlaywrightTimeoutError:
                logger.debug("Password submit selector '%s' timed out.", selector)

        raise RuntimeError(
            f"No submit button found after password field. URL: {page.url}"
        )

    async def _accept_consent(self, page) -> None:
        for selector in [
            "button:has-text('Accept')",
            "button:has-text('Akzeptieren')",
            "button:has-text('Zustimmen')",
            "input[type=submit][value='Accept']",
            "button:has-text('Continue')",
            "button:has-text('Weiter')",
        ]:
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    await btn.click(timeout=5_000)
                    logger.debug("Accepted consent via '%s'.", selector)
                    return
            except PlaywrightTimeoutError:
                logger.debug("Consent selector '%s' timed out.", selector)
            except PlaywrightError as exc:
                logger.debug("Consent selector '%s' error: %s", selector, exc)


# ---------------------------------------------------------------------------
# Course adapter
# ---------------------------------------------------------------------------

class IliasCourseAdapter(ICoursePort):
    """Reads the course list from ILIAS via Arbeitsraum → Aktuelles Semester."""

    def __init__(self, browser: BrowserManager, base_url: str, video_quality: str = "lowest") -> None:
        self._browser = browser
        self._base_url = base_url.rstrip("/")
        # "lowest" → smallest file / lowest resolution; "highest" → best quality
        if video_quality not in ("lowest", "highest"):
            raise ValueError(f"video_quality must be 'lowest' or 'highest', got '{video_quality}'")
        self._video_quality = video_quality

    async def get_courses(self) -> list[Course]:
        page = self._browser.page

        # Step 1 – navigate to ILIAS main page
        await page.goto(f"{self._base_url}/ilias.php")
        await page.wait_for_load_state("networkidle")

        # Step 2 – find "Aktuelles Semester" link and navigate to its href directly
        semester_link = page.locator("a:has-text('Aktuelles Semester')").first
        href = await semester_link.get_attribute("href", timeout=10_000)
        if not href:
            raise RuntimeError(
                f"Navigation item 'Aktuelles Semester' not found. URL: {page.url}"
            )
        target = href if href.startswith("http") else f"{self._base_url}/{href.lstrip('/')}"
        await page.goto(target)
        await page.wait_for_load_state("networkidle")
        logger.info("Navigated to 'Aktuelles Semester'. URL: %s", page.url)

        # Step 4 – extract courses from the current page.
        # Course items are rendered as <button data-action="...&ref_id=..."> inside
        # .il-item-title — not as <a> tags.
        courses: list[Course] = []
        seen: set[str] = set()

        selector = ".il-item-title button[data-action*='ref_id']"
        for btn in await page.locator(selector).all():
            action = await btn.get_attribute("data-action") or ""
            title = (await btn.inner_text()).strip()
            if not action or not title:
                continue
            match = re.search(r"ref_id=(\d+)", action)
            if match:
                ref_id = match.group(1)
                if ref_id not in seen:
                    seen.add(ref_id)
                    full_url = (
                        action if action.startswith("http")
                        else f"{self._base_url}/{action.lstrip('/')}"
                    )
                    courses.append(Course(title=title, ref_id=ref_id, url=full_url))

        logger.info("Found %d course(s) in 'Aktuelles Semester'.", len(courses))
        return courses

    async def list_content(self, ref_id: RefId) -> list[ContentItem]:
        page = self._browser.page
        await page.goto(
            f"{self._base_url}/ilias.php"
            f"?ref_id={ref_id}&cmd=view&baseClass=ilrepositorygui"
        )
        await page.wait_for_load_state("networkidle")

        raw: list[dict] = await page.evaluate("""
            () => {
                const blocks = [...document.querySelectorAll('.ilContainerBlock')];
                const inhaltBlock = blocks.find(b => {
                    const h = b.querySelector('h2, h3');
                    return h && h.textContent.trim().toLowerCase() === 'inhalt';
                });
                if (!inhaltBlock) return [];
                // Collect rows from multiple possible CSS classes used across ILIAS versions
                const rowEls = new Set([
                    ...inhaltBlock.querySelectorAll('.ilObjListRow'),
                    ...inhaltBlock.querySelectorAll('.il-std-item'),
                    ...inhaltBlock.querySelectorAll('.il_ContainerListItem'),
                ]);
                const seen = new Set();
                return [...rowEls].map(row => {
                    const link = row.querySelector(
                        '.il_ContainerItemTitle a, .il-item-title a, a[href*="/go/"], a[href*="ref_id="]'
                    );
                    const icon = row.querySelector('img[alt]');
                    if (!link) return null;
                    const url = link.href;
                    if (seen.has(url)) return null;
                    seen.add(url);
                    return {
                        title: link.textContent.trim(),
                        url,
                        item_type: icon ? icon.alt : ''
                    };
                }).filter(Boolean);
            }
        """)

        items: list[ContentItem] = []
        for entry in raw:
            url: str = entry["url"]
            # URL is either /go/{type}/{ref_id} or contains ref_id= as a query param
            match = re.search(r"/go/\w+/(\d+)", url) or re.search(r"ref_id=(\d+)", url)
            if not match:
                continue
            items.append(ContentItem(
                title=entry["title"],
                ref_id=match.group(1),
                url=url,
                item_type=entry["item_type"],
            ))

        logger.info("Found %d content item(s) for ref_id=%s.", len(items), ref_id)
        return items

    async def list_videos(self, ref_id: RefId) -> list[VideoItem]:
        page = self._browser.page
        await page.goto(
            f"{self._base_url}/ilias.php"
            f"?baseClass=ilObjPluginDispatchGUI&cmd=forward"
            f"&ref_id={ref_id}&forwardCmd=showContent"
        )
        await page.wait_for_load_state("networkidle")

        raw: list[dict] = await page.evaluate("""
            () => {
                const rows = [...document.querySelectorAll('table[id*="tbl_xoct"] tbody tr')];
                return rows.map(tr => {
                    const cells = [...tr.querySelectorAll('td')];
                    const eidEl = tr.querySelector('[data-id]');
                    const playEl = tr.querySelector('a[href*="streamVideo"]');
                    // Both video and subtitle links use cmd=download — only distinguishable
                    // by which dropdown button they belong to ("Download" vs "Untertitel").
                    let dlLinks = [];
                    let subtitleUrl = '';
                    for (const dd of tr.querySelectorAll('div.dropdown')) {
                        const btn = dd.querySelector('button.dropdown-toggle');
                        if (!btn) continue;
                        const btnText = btn.textContent.trim();
                        const links = [...dd.querySelectorAll('a[href*="cmd=download"]')];
                        if (btnText.startsWith('Download')) {
                            dlLinks = links;
                        } else if (btnText.startsWith('Untertitel')) {
                            subtitleUrl = links.length > 0 ? links[0].href : '';
                        }
                    }
                    return {
                        title: cells[2] ? cells[2].textContent.trim() : '',
                        event_id: eidEl ? eidEl.dataset.id : '',
                        date: cells[6] ? cells[6].textContent.trim() : '',
                        url: playEl ? playEl.href : '',
                        download_urls: dlLinks.map(a => a.href),
                        subtitle_url: subtitleUrl
                    };
                }).filter(r => r.title && r.url);
            }
        """)

        videos = []
        for r in raw:
            urls: list[str] = r["download_urls"]
            if self._video_quality == "lowest":
                download_url = urls[-1] if urls else ""
            else:  # "highest"
                download_url = urls[0] if urls else ""
            videos.append(VideoItem(
                title=r["title"],
                event_id=r["event_id"],
                date=r["date"],
                url=r["url"],
                download_url=download_url,
                subtitle_url=r.get("subtitle_url", ""),
            ))
        logger.info("Found %d video(s) for ref_id=%s.", len(videos), ref_id)
        return videos


# ---------------------------------------------------------------------------
# File adapter
# ---------------------------------------------------------------------------

class IliasFileAdapter(IFilePort):
    """Lists and downloads files from ILIAS course containers."""

    def __init__(self, browser: BrowserManager, base_url: str, max_filename_len: int = 64) -> None:
        self._browser = browser
        self._base_url = base_url.rstrip("/")
        self._max_filename_len = max_filename_len

    async def list_files(self, ref_id: RefId) -> list[CourseFile]:
        files: list[CourseFile] = []
        seen_urls: set[str] = set()
        await self._collect(str(ref_id), files, set(), seen_urls)
        return files

    async def download(self, file: CourseFile, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        page = self._browser.page
        async with page.expect_download(timeout=7_200_000) as dl_info:
            try:
                await page.goto(file.url)
            except PlaywrightError as exc:
                # Playwright raises when navigation ends with a download instead of a page load.
                # The download event is still captured — this error is expected and safe to ignore.
                if "Download is starting" not in str(exc):
                    raise
        download = await dl_info.value
        raw_name = file.file_name or download.suggested_filename or file.title
        safe_name = sanitize_filename(raw_name, self._max_filename_len)
        save_path = output_dir / safe_name
        await download.save_as(save_path)
        return save_path

    async def download_video(self, video: VideoItem, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        if not video.download_url:
            raise ValueError(f"Video '{video.title}' has no download URL.")
        page = self._browser.page
        async with page.expect_download(timeout=7_200_000) as dl_info:
            try:
                await page.goto(video.download_url)
            except PlaywrightError as exc:
                if "Download is starting" not in str(exc):
                    raise
        download = await dl_info.value
        # Build filename from title + date; fall back to suggested filename
        if video.title:
            safe_title = re.sub(r'[<>:"/\\|?*]', "_", video.title)
            date_part = f"_{video.date}" if video.date else ""
            suffix = Path(download.suggested_filename).suffix or ".mp4"
            raw_name = f"{safe_title}{date_part}{suffix}"
        else:
            raw_name = download.suggested_filename
        safe_name = sanitize_filename(raw_name, self._max_filename_len)
        save_path = output_dir / safe_name
        await download.save_as(save_path)

        # Download subtitle file if available
        if video.subtitle_url:
            await self._download_subtitle(video, output_dir, save_path)

        return save_path

    async def _download_subtitle(self, video: VideoItem, output_dir: Path, video_path: Path) -> None:
        """Download the subtitle file and save it alongside the video with the same stem."""
        page = self._browser.page
        try:
            async with page.expect_download(timeout=60_000) as dl_info:
                try:
                    await page.goto(video.subtitle_url)
                except PlaywrightError as exc:
                    if "Download is starting" not in str(exc):
                        raise
            download = await dl_info.value
            suffix = Path(download.suggested_filename).suffix or ".srt"
            # Use the same stem as the video file so they pair naturally
            subtitle_path = video_path.with_suffix(suffix)
            await download.save_as(subtitle_path)
            logger.info("Saved subtitle: %s", subtitle_path)
        except Exception as exc:
            logger.warning("Failed to download subtitle for '%s': %s", video.title, exc)

    async def get_remote_size(self, url: str) -> int | None:
        try:
            response = await self._browser.page.context.request.head(url)
            cl = response.headers.get("content-length")
            return int(cl) if cl else None
        except Exception:
            return None

    async def _collect(self, ref_id: str, files: list, visited: set, seen_urls: set | None = None) -> None:
        if ref_id in visited:
            return
        visited.add(ref_id)
        if seen_urls is None:
            seen_urls = set()

        page = self._browser.page
        await page.goto(
            f"{self._base_url}/ilias.php"
            f"?ref_id={ref_id}&cmd=view&baseClass=ilrepositorygui"
        )
        await page.wait_for_load_state("networkidle")

        # Collect file links with type and filename — deduplicate by normalised URL
        raw_files: list[dict] = await page.evaluate("""
            () => {
                const links = [...document.querySelectorAll(
                    'a[href*="cmd=sendfile"], a[href*="goto.php?target=file_"]'
                )];
                return links.map(a => {
                    const row = a.closest('.ilObjListRow, .il_ContainerListItem, li, tr')
                        || a.parentElement;
                    // First il_ItemProperty holds type (e.g. "pdf"), second holds size
                    const props = row
                        ? [...row.querySelectorAll('.il_ItemProperty, .ilItemProperty')]
                        : [];
                    const file_type = props[0] ? props[0].textContent.trim().toLowerCase() : '';
                    // File name from the download link in the action dropdown, or derive from title+type
                    const dlLink = row
                        ? row.querySelector('a[href*="cmd=sendfile"][download], a[download]')
                        : null;
                    const file_name = dlLink ? dlLink.getAttribute('download') : '';
                    return {
                        title: a.textContent.trim(),
                        url: a.href,
                        file_type,
                        file_name
                    };
                });
            }
        """)
        for entry in raw_files:
            href = entry["url"]
            title = entry["title"]
            if not href or not title:
                continue
            norm = re.sub(r"cmdNode=[^&]*&?", "", href)
            if norm in seen_urls:
                continue
            seen_urls.add(norm)
            # Derive filename from title + type if not found in DOM
            file_name = entry["file_name"] or ""
            if not file_name and entry["file_type"] and title:
                safe = re.sub(r'[<>:"/\\|?*]', "_", title)
                file_name = f"{safe}.{entry['file_type']}"
            files.append(CourseFile(
                title=title,
                url=href,
                file_type=entry["file_type"],
                file_name=file_name,
            ))

        # Collect sub-container ref_ids from classic ilrepositorygui <a> links
        sub_ref_ids: set[str] = set()
        for link in await page.locator(
            "a[href*='ref_id'][href*='baseClass=ilrepositorygui']"
        ).all():
            href = await link.get_attribute("href") or ""
            match = re.search(r"ref_id=(\d+)", href)
            if match and match.group(1) != ref_id:
                sub_ref_ids.add(match.group(1))

        # ILIAS v9 renders INHALT folder links as /go/fold/{ref_id} (no baseClass in URL)
        for link in await page.locator("a[href*='/go/fold/']").all():
            href = await link.get_attribute("href") or ""
            match = re.search(r"/go/fold/(\d+)", href)
            if match:
                sub_ref_ids.add(match.group(1))

        for sub_id in sub_ref_ids:
            await self._collect(sub_id, files, visited, seen_urls)
```

---

## interface/context.py

```python
"""
Interface layer — shared application context and rate limiter.
"""

import os
import time
from dataclasses import dataclass, field

from mcp.server.fastmcp import Context
from mcp.server.session import ServerSession

from application.auth_service import AuthService
from application.course_service import CourseService
from application.download_service import DownloadService, DownloadTracker
from domain.models import LoginCredentials

_RATE_LIMIT_CALLS = int(os.environ.get("RATE_LIMIT_CALLS_PER_MIN", "20"))
_MIN_INTERVAL = 60.0 / _RATE_LIMIT_CALLS


@dataclass
class RateLimiter:
    _last_calls: dict[str, float] = field(default_factory=dict)

    def check(self, tool_name: str) -> None:
        now = time.monotonic()
        last = self._last_calls.get(tool_name, 0.0)
        elapsed = now - last
        if elapsed < _MIN_INTERVAL:
            wait = _MIN_INTERVAL - elapsed
            raise RuntimeError(
                f"Rate limit exceeded for '{tool_name}'. "
                f"Please wait {wait:.1f}s before calling it again."
            )
        self._last_calls[tool_name] = now


@dataclass
class AppContext:
    credentials: LoginCredentials
    auth_service: AuthService
    course_service: CourseService
    download_service: DownloadService
    rate_limiter: RateLimiter
    download_tracker: DownloadTracker = field(default_factory=DownloadTracker)


def app_from_ctx(ctx: Context[ServerSession, "AppContext"]) -> "AppContext":
    return ctx.request_context.lifespan_context
```

---

## interface/tools/auth_tools.py

```python
"""
Interface layer — MCP tools for authentication.
"""

import logging

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from interface.context import AppContext, app_from_ctx

logger = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def login(ctx: Context[ServerSession, AppContext]) -> str:
        """Login to ILIAS via Switch edu-ID using the credentials from .env."""
        app = app_from_ctx(ctx)
        app.rate_limiter.check("login")
        logger.info("Tool 'login' called.")
        return await app.auth_service.login(app.credentials)
```

---

## interface/tools/course_tools.py

```python
"""
Interface layer — MCP tools for course listing and content exploration.
"""

import logging

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from domain.models import RefId
from interface.context import AppContext, app_from_ctx

logger = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def list_courses(ctx: Context[ServerSession, AppContext]) -> list[dict]:
        """List all courses available on the ILIAS dashboard. Login first."""
        app = app_from_ctx(ctx)
        app.rate_limiter.check("list_courses")
        logger.info("Tool 'list_courses' called.")
        courses = await app.course_service.list_courses()
        return [{"title": c.title, "ref_id": c.ref_id, "url": c.url} for c in courses]

    @mcp.tool()
    async def list_course_content_docs(ctx: Context[ServerSession, AppContext], ref_id: str) -> list[dict]:
        """
        List the top-level INHALT items of a course. Folder items are automatically
        expanded to include their files (title, file_name, file_type, url).

        Args:
            ref_id: The ILIAS ref_id of the course (obtained from list_courses).
        """
        app = app_from_ctx(ctx)
        app.rate_limiter.check("list_course_content_docs")
        logger.info("Tool 'list_course_content_docs' called with ref_id=%s.", ref_id)
        items = await app.course_service.list_course_content_docs(RefId(ref_id))

        result = []
        for item in items:
            entry: dict = {"title": item.title, "ref_id": item.ref_id, "url": item.url, "type": item.item_type}
            if "ordner" in item.item_type.lower():
                files = await app.download_service.list_course_files(RefId(item.ref_id))
                entry["files"] = [
                    {"title": f.title, "file_name": f.file_name, "file_type": f.file_type, "download_url": f.url}
                    for f in files
                ]
            result.append(entry)
        return result

    @mcp.tool()
    async def list_course_content_video(ctx: Context[ServerSession, AppContext], ref_id: str) -> list[dict]:
        """
        List all Opencast video recordings in a course's video series.

        Args:
            ref_id: The ILIAS ref_id of the Opencast series object (obtained from list_course_content_docs).
        """
        app = app_from_ctx(ctx)
        app.rate_limiter.check("list_course_content_video")
        logger.info("Tool 'list_course_content_video' called with ref_id=%s.", ref_id)
        videos = await app.course_service.list_course_content_video(RefId(ref_id))
        return [
            {"title": v.title, "event_id": v.event_id, "date": v.date, "url": v.url, "download_url": v.download_url, "subtitle_url": v.subtitle_url}
            for v in videos
        ]

    @mcp.tool()
    async def list_course_content(ctx: Context[ServerSession, AppContext], ref_id: str) -> str:
        """
        List all content of a course. Automatically expands:
        - Folders → list of files with download URLs
        - Opencast video series → list of video recordings with download URLs

        Use this as the primary tool to explore a course's contents.
        Returns a formatted text with all download URLs explicitly shown.

        Args:
            ref_id: The ILIAS ref_id of the course (obtained from list_courses).
        """
        app = app_from_ctx(ctx)
        app.rate_limiter.check("list_course_content")
        logger.info("Tool 'list_course_content' called with ref_id=%s.", ref_id)
        sections = await app.download_service.list_course_content(RefId(ref_id))

        lines: list[str] = []
        for s in sections:
            lines.append(f"\n## {s.item.item_type}: {s.item.title}  (ref_id={s.item.ref_id})")
            if s.files:
                for f in s.files:
                    label = f.file_name or f.title
                    lines.append(f"  - [{label}]({f.url})")
            elif s.videos:
                for v in s.videos:
                    lines.append(f"  - {v.title} ({v.date})")
                    lines.append(f"    Stream:   {v.url}")
                    lines.append(f"    Download: {v.download_url}")
                    if v.subtitle_url:
                        lines.append(f"    Subtitle: {v.subtitle_url}")
            else:
                lines.append(f"  URL: {s.item.url}")
        return "\n".join(lines)

    @mcp.tool()
    async def list_course_files(ctx: Context[ServerSession, AppContext], ref_id: str) -> list[dict]:
        """
        Recursively list all downloadable files in a course.

        Args:
            ref_id: The ILIAS ref_id of the course (obtained from list_courses).
        """
        app = app_from_ctx(ctx)
        app.rate_limiter.check("list_course_files")
        logger.info("Tool 'list_course_files' called with ref_id=%s.", ref_id)
        files = await app.download_service.list_course_files(RefId(ref_id))
        return [{"title": f.title, "file_name": f.file_name, "file_type": f.file_type, "url": f.url} for f in files]
```

---

## interface/tools/download_tools.py

```python
"""
Interface layer — MCP tools for downloading course files.
"""

import logging
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from application.download_service import DownloadTracker, _build_summary
from domain.models import RefId
from interface.context import AppContext, app_from_ctx

logger = logging.getLogger(__name__)


def _progress_log(ctx: Context[ServerSession, AppContext], collector: list[str]):
    """
    Return an async log callback that:
    - sends each message as an MCP log notification (ctx.info)
    - appends it to `collector` so it can be included in the final tool result
    """
    async def _cb(msg: str) -> None:
        collector.append(msg)
        await ctx.info(msg)
    return _cb


def register(mcp: FastMCP, download_dir: str) -> None:
    @mcp.tool()
    async def download_course_files(ctx: Context[ServerSession, AppContext], ref_id: str) -> str:
        """
        Download all files from a single course to DOWNLOAD_DIR (.env).
        Always call preview_course_files first and show the result to the user before calling this tool.

        Args:
            ref_id: The ILIAS ref_id of the course (obtained from list_courses).
        """
        app = app_from_ctx(ctx)
        app.rate_limiter.check("download_course_files")
        logger.info("Tool 'download_course_files' called with ref_id=%s.", ref_id)
        app.download_tracker.reset()
        log_lines: list[str] = []
        downloaded, skipped = await app.download_service.download_course(
            RefId(ref_id), Path(download_dir), app.download_tracker, log=_progress_log(ctx, log_lines)
        )
        summary = _build_summary(downloaded, skipped, 1, Path(download_dir), app.download_tracker)
        progress_log = "\n".join(log_lines)
        return f"=== Progress Log ===\n{progress_log}\n\n{summary}"

    @mcp.tool()
    async def download_all_files(ctx: Context[ServerSession, AppContext]) -> str:
        """Download every file from every course to the directory configured in DOWNLOAD_DIR (.env)."""
        app = app_from_ctx(ctx)
        app.rate_limiter.check("download_all_files")
        logger.info("Tool 'download_all_files' called.")
        log_lines: list[str] = []
        summary = await app.download_service.download_all(
            Path(download_dir), app.download_tracker, log=_progress_log(ctx, log_lines)
        )
        progress_log = "\n".join(log_lines)
        return f"=== Progress Log ===\n{progress_log}\n\n{summary}"

    @mcp.tool()
    async def download_status(ctx: Context[ServerSession, AppContext]) -> str:
        """
        Show the current download progress: pending, active, done, and skipped files with file sizes.
        Call this every 30 seconds while download_all_files is running.
        """
        app = app_from_ctx(ctx)
        t = app.download_tracker
        if not t.entries:
            return "No download in progress."

        lines: list[str] = []
        if t.active_name:
            lines.append(f"Currently downloading: {t.active_name}")
        lines.append(
            f"Summary: {t.total_done} done, {t.total_skipped} skipped, "
            f"{t.total_error} error(s), "
            f"{sum(1 for e in t.entries if e.status == 'pending')} pending"
        )
        lines.append("")

        for status_label, status_key in [
            ("Active", "active"), ("Pending", "pending"),
            ("Done", "done"), ("Skipped", "skipped"), ("Error", "error"),
        ]:
            group = [e for e in t.entries if e.status == status_key]
            if not group:
                continue
            lines.append(f"### {status_label} ({len(group)})")
            for e in group:
                size_str = f"  {e.size_bytes / 1_048_576:.1f} MB" if e.size_bytes else ""
                error_str = f"  [{e.error}]" if e.error else ""
                lines.append(f"  [{e.course}] {e.name}{size_str}{error_str}")

        return "\n".join(lines)
```

---

## server.py

```python
"""
Interface layer — MCP server entry point.

Dependency flow (DDD layers):
  server.py  →  interface/  →  application/  →  domain/ports  ←  infrastructure/
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from application.auth_service import AuthService
from application.course_service import CourseService
from application.download_service import DownloadService
from domain.models import LoginCredentials
from infrastructure.browser import BrowserManager
from infrastructure.ilias_adapters import (
    IliasAuthAdapter,
    IliasCourseAdapter,
    IliasFileAdapter,
)
from interface.context import AppContext, RateLimiter
import interface.tools.auth_tools as auth_tools
import interface.tools.course_tools as course_tools
import interface.tools.download_tools as download_tools

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

ILIAS_URL = os.environ["ILIAS_URL"]
ILIAS_INSTITUTION = os.environ["ILIAS_INSTITUTION"]
SWITCH_EDU_ID_EMAIL = os.environ["SWITCH_EDU_ID_EMAIL"]
SWITCH_EDU_ID_PASSWORD = os.environ["SWITCH_EDU_ID_PASSWORD"]
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "./ilias_downloads")
MAX_FILENAME_LEN = int(os.environ.get("MAX_FILENAME_LEN", "64"))
MAX_DIRNAME_LEN = int(os.environ.get("MAX_DIRNAME_LEN", "64"))
VIDEO_QUALITY = os.environ.get("VIDEO_QUALITY", "lowest")


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    browser = BrowserManager()
    await browser.start()

    credentials = LoginCredentials(
        email=SWITCH_EDU_ID_EMAIL,
        password=SWITCH_EDU_ID_PASSWORD,
        institution_idp=ILIAS_INSTITUTION,
        base_url=ILIAS_URL,
    )

    auth_adapter = IliasAuthAdapter(browser)
    course_adapter = IliasCourseAdapter(browser, ILIAS_URL, video_quality=VIDEO_QUALITY)
    file_adapter = IliasFileAdapter(browser, ILIAS_URL, max_filename_len=MAX_FILENAME_LEN)

    auth_service = AuthService(auth_adapter)
    course_service = CourseService(course_adapter, auth_service)
    download_service = DownloadService(
        course_service, file_adapter, auth_service,
        max_filename_len=MAX_FILENAME_LEN,
        max_dirname_len=MAX_DIRNAME_LEN,
    )

    try:
        yield AppContext(
            credentials=credentials,
            auth_service=auth_service,
            course_service=course_service,
            download_service=download_service,
            rate_limiter=RateLimiter(),
        )
    finally:
        await browser.stop()


mcp = FastMCP(
    "ilias-mcp",
    lifespan=lifespan,
    instructions=(
        "Use this MCP server whenever the user mentions ILIAS, university courses, "
        "lecture materials, Uni Bern, or ilias.unibe.ch.\n"
        "Workflow: always call `login` first, then use the other tools.\n"
        "- `login` — authenticate via Switch edu-ID\n"
        "- `list_courses` — list enrolled courses\n"
        "- `list_course_content` — list all course content: auto-expands folders (files+download URLs) and Opencast series (videos+download URLs)\n"
        "- `list_course_content_docs` — list top-level INHALT items of a course (folders auto-expanded, no video expansion)\n"
        "- `list_course_content_video` — list Opencast video recordings for a specific series ref_id\n"
        "- `list_course_files` — recursively list all downloadable files in a course\n"
        "- `download_course_files` — download all files from a single course\n"
        "- `download_all_files` — download all files from all courses"
    ),
)

auth_tools.register(mcp)
course_tools.register(mcp)
download_tools.register(mcp, DOWNLOAD_DIR)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
```

---

## Key architectural notes

### Content type detection (DownloadService.list_course_content)
- `item_type.lower()` contains `"ordner"` → folder → expand with `list_files`
- `item_type.lower()` contains `"opencast"` → video series → expand with `list_videos`
- `item_type.lower()` contains `"datei"` or `"file"` → top-level document → wrapped as a single `CourseFile`; extension derived from type prefix (e.g. "pdf Datei" → `.pdf`) or title suffix
- Anything else → pass through as-is (no sub-expansion)

### Download directory layout

- Course root: `output_dir/<course_title>/`
- Folder sections (Ordner) and Opencast sections: saved into `output_dir/<course_title>/<section_title>/`
- Top-level documents and other items: saved directly into `output_dir/<course_title>/`

### Subtitle handling

- `list_videos` discriminates subtitle links from video download links by reading the `button.dropdown-toggle` text: `"Download"` → video links; `"Untertitel"` → subtitle URL
- `VideoItem.subtitle_url` carries the subtitle download URL (empty string if none)
- `download_video` calls `_download_subtitle` after saving the video; subtitle is saved with the same stem as the video file but with the extension from the suggested filename (default `.srt`); subtitle failures are logged as warnings, not raised

### ILIAS DOM selectors (confirmed for ilias.unibe.ch ILIAS v9)
| What | Selector |
|------|----------|
| Institution dropdown | `select#user_idp` |
| Login submit | `input#wayf_submit_button` |
| Email field | `input#username, input[name='j_username']` |
| Password submit | `button#button-submit` (with fallbacks) |
| Course items | `.il-item-title button[data-action*='ref_id']` |
| INHALT block | `.ilContainerBlock` with `h2/h3` text "inhalt" |
| Content rows | `.ilObjListRow`, `.il-std-item`, `.il_ContainerListItem` |
| File links | `a[href*="cmd=sendfile"], a[href*="goto.php?target=file_"]` |
| Folder links (v9) | `a[href*='/go/fold/']` |
| Video table | `table[id*="tbl_xoct"] tbody tr` |

### URL patterns
- Course page: `{base_url}/ilias.php?ref_id={id}&cmd=view&baseClass=ilrepositorygui`
- Opencast series: `{base_url}/ilias.php?baseClass=ilObjPluginDispatchGUI&cmd=forward&ref_id={id}&forwardCmd=showContent`
- Video download links ordered highest → lowest resolution inside each row

### File deduplication
URLs are normalised by stripping `cmdNode=[^&]*&?` before dedup. This removes the ephemeral
`cmdNode` parameter that ILIAS includes in links but which doesn't affect the target file.

### Download skip logic
- Skip if local file exists AND (remote size unavailable OR remote size == local size).
- Re-download if sizes differ (size mismatch = partial/updated file).
- Video skip: checks for `{safe_title}_{date}.mp4` first, then globs for any extension.

### Rate limiter
Per-tool sliding window: max 20 calls/min by default (`RATE_LIMIT_CALLS_PER_MIN` env var).
Each tool call records its own timestamp independently.

---

## Setup and running

```bash
# Install dependencies (uv recommended)
uv sync

# Install Playwright browser
uv run playwright install chromium

# Copy and fill in credentials
cp .env.example .env
# Edit .env with your Switch edu-ID email, password, and institution IdP

# Run the MCP server (stdio transport, for Claude Desktop / MCP clients)
uv run ilias-mcp
# or: uv run python server.py

# Run tests (dev dependencies not installed by default)
uv run --extra dev pytest tests/
```

### Claude Desktop config (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "ilias-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/ilias-mcp", "ilias-mcp"]
    }
  }
}
```

---

## All `__init__.py` files

All package `__init__.py` files (`domain/`, `application/`, `infrastructure/`, `interface/`,
`interface/tools/`, `tests/`) are empty files — create them as empty to make the directories
Python packages.
