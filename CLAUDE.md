# ilias-mcp — Complete Reconstruction Specification

An MCP server that connects to the ILIAS university learning platform (ilias.unibe.ch, Uni Bern)
via Switch edu-ID SSO. Allows an AI assistant to list semesters, courses, explore content, and
download files/videos from ILIAS through a headless Chromium browser (Playwright).

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
│   └── utils.py                       # sanitize_filename, clean_text helpers
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
│   ├── schemas.py                     # Pydantic response models for structured MCP tool output
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
on application. Nothing in domain/ depends on any other layer. `interface/schemas.py` depends only
on `pydantic` (already a transitive dependency of `mcp`) — it holds no business logic, only the
response shapes tools return so FastMCP can derive a typed `outputSchema` per the MCP spec.

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
class Semester:
    """Represents a semester grouping on the ILIAS dashboard."""
    label: str       # e.g. "HS2025", "FS2026" — the unique identifier
    url: str         # Stable goto.php URL to this semester's course listing page
    is_current: bool = False  # True if this is the currently active/selected semester


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
```

---

## domain/utils.py

```python
"""
Domain utilities — pure helper functions with no external dependencies.
"""

import re


def clean_text(s: str) -> str:
    """Strip ASCII control characters from a scraped string (keeps tab and newline)."""
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", s)


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
from domain.models import ContentItem, Course, RefId, Semester, VideoItem
from domain.ports import ICoursePort


class CourseService:
    """Lists courses from ILIAS, enforcing that the user is authenticated."""

    def __init__(self, course_port: ICoursePort, auth_service: AuthService) -> None:
        self._course_port = course_port
        self._auth = auth_service

    async def list_semesters(self) -> list[Semester]:
        """Return all available semesters from the ILIAS dashboard."""
        self._auth.require_authenticated()
        return await self._course_port.get_semesters()

    async def list_courses(self, semester_label: str | None = None) -> list[Course]:
        """Return all courses for the given semester label (or current semester if None)."""
        self._auth.require_authenticated()
        return await self._course_port.get_courses(semester_label)

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
        semester_label: str | None = None,
    ) -> tuple[int, int]:
        """
        Download all files of a single course into output_dir/<course_title>/.
        Returns (downloaded, skipped) counts.
        If *sections* is provided (pre-fetched via list_course_content), the
        content scan is skipped; otherwise list_course_content is called here.
        *semester_label* is forwarded to list_courses to fetch from the correct semester.
        """
        self._auth.require_authenticated()
        courses = await self._course_service.list_courses(semester_label)
        course = next((c for c in courses if c.ref_id == ref_id.value), None)
        if course is None:
            raise ValueError(f"Course with ref_id={ref_id.value} not found.")

        course_dir = _course_dir(output_dir, course.title, self._max_dirname_len)
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
        semester_label: str | None = None,
    ) -> str:
        """
        Download every file from every course into output_dir/<course_title>/.
        Skips files that already exist. Returns a human-readable summary report.
        *semester_label* restricts the download to a specific semester; if None,
        the current semester is used.

        Two-phase approach:
        1. Scan all courses via list_course_content to discover their content.
        2. Download each course via download_course, reusing the pre-fetched content.
        """
        self._auth.require_authenticated()
        if tracker is not None:
            tracker.reset()

        courses = await self._course_service.list_courses(semester_label)

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
                semester_label=semester_label,
            )
            total_downloaded += downloaded
            total_skipped += skipped

        return _build_summary(total_downloaded, total_skipped, len(courses), output_dir, tracker)


def _safe_name(name: str, max_len: int = 64) -> str:
    return sanitize_filename(name, max_len)


def _strip_course_prefix(title: str) -> str:
    """
    Remove the institutional course-code prefix from a course title.
    Strips patterns like "450407-FS2026-0_ " or "450407-FS2026-0: " (digits, dashes,
    digits, then underscore or colon, then optional space) leaving only the
    human-readable name, e.g. "Grundzüge Erdwissenschaften II".
    If the title doesn't match the pattern it is returned unchanged.
    """
    return re.sub(r"^\d+[\w-]*[_:]\s*", "", title).strip() or title


def _course_dir(output_dir: Path, course_title: str, max_dirname_len: int) -> Path:
    """
    Resolve the local directory for a course, preferring a clean stripped name.
    Falls back to the legacy full-title directory if it already exists, so that
    previously downloaded courses are recognised without re-downloading anything.
    """
    stripped = _strip_course_prefix(course_title)
    new_dir = output_dir / _safe_name(stripped, max_dirname_len)
    # Backward-compat: if old directory (full title) exists and new one doesn't, keep old path.
    if stripped != course_title:
        old_dir = output_dir / _safe_name(course_title, max_dirname_len)
        if old_dir.exists() and not new_dir.exists():
            return old_dir
    return new_dir


def _strip_video_title(title: str) -> str:
    """
    Remove the institutional prefix from a video title.
    Strips patterns like "FS2026: " or "FS2026_ " (semester prefix) or "450407-FS2026-0: " (full code prefix).
    Must mirror the logic in IliasFileAdapter.download_video.
    """
    return re.sub(r"^(?:\d+[\w-]*[_:]|[A-Z]+\d+[_:])\s*", "", title).strip() or title


def _video_expected_path(video: VideoItem, course_dir: Path, max_len: int) -> Path | None:
    """
    Return the local path where a video would be saved.
    Mirrors the filename logic in IliasFileAdapter.download_video, assuming .mp4
    as the default suffix.  Returns None if no matching file is found.

    Checks the stripped title (current behavior — semester prefix removed) first,
    then falls back to the unstripped title for backward compatibility with files
    downloaded before prefix stripping was introduced.
    """
    stripped_title = _strip_video_title(video.title)
    date_part = f"_{video.date}" if video.date else ""

    def _find(title_variant: str) -> Path | None:
        safe = re.sub(r'[<>:"/\\|?*]', "_", title_variant)
        raw_stem = f"{safe}{date_part}"
        candidate = course_dir / sanitize_filename(f"{raw_stem}.mp4", max_len)
        if candidate.exists():
            return candidate
        if course_dir.exists():
            stem = Path(sanitize_filename(f"{raw_stem}.mp4", max_len)).stem
            matches = list(course_dir.glob(f"{glob_escape(stem)}.*"))
            if matches:
                return matches[0]
        return None

    # Primary: stripped title (no semester prefix)
    found = _find(stripped_title)
    if found:
        return found
    # Backward compat: old files saved with semester prefix
    if stripped_title != video.title:
        return _find(video.title)
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

from domain.models import ContentItem, Course, CourseFile, LoginCredentials, RefId, Semester, VideoItem
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

    async def _get_semester_url(self, page, semester_label: str | None = None) -> str:
        """
        Return the stable goto.php URL for the given semester.
        If semester_label is None, returns the default 'Aktuelles Semester' URL.

        URL pattern: goto.php?target=iLUBModsSemester{labels_joined_by_underscore}&default={label}
        Example:     goto.php?target=iLUBModsSemesterHS2026_FS2026_HS2025_FS2025_HS2024&default=HS2025
        """
        await page.goto(f"{self._base_url}/ilias.php")
        await page.wait_for_load_state("networkidle")
        semester_link = page.locator("a:has-text('Aktuelles Semester')").first
        href = await semester_link.get_attribute("href", timeout=10_000)
        if not href:
            raise RuntimeError("Navigation item 'Aktuelles Semester' not found.")
        full_href = href if href.startswith("http") else f"{self._base_url}/{href.lstrip('/')}"
        if semester_label is None:
            return full_href
        # Substitute default= with the requested semester label
        match = re.search(r"target=(iLUBModsSemester[^&]+)", href)
        if not match:
            raise RuntimeError(f"Cannot parse semester target from URL: {href}")
        target_val = match.group(1)
        return f"{self._base_url}/goto.php?target={target_val}&default={semester_label}"

    async def get_semesters(self) -> list[Semester]:
        """
        Return all available semesters from the ILIAS dashboard.

        Semester labels (e.g. "HS2025") are parsed from the 'Aktuelles Semester' goto URL
        which encodes all available semesters in the target parameter:
          target=iLUBModsSemesterHS2026_FS2026_HS2025_FS2025_HS2024
        The active semester is detected via aria-pressed="true" on its tab button.
        """
        page = self._browser.page
        default_url = await self._get_semester_url(page)
        await page.goto(default_url)
        await page.wait_for_load_state("networkidle")

        # Parse semester labels from the goto URL target parameter
        match = re.search(r"target=iLUBModsSemester([^&]+)", default_url)
        if not match:
            raise RuntimeError(f"Cannot parse semester list from URL: {default_url}")
        semesters_str = match.group(1)
        labels = semesters_str.split("_")

        # Find active semester — its tab button has aria-pressed="true" and aria-label matching the label
        active_labels: set[str] = set()
        for label in labels:
            btn = page.locator(f"button[aria-label='{label}'][aria-pressed='true']").first
            if await btn.count() > 0:
                active_labels.add(label)

        semesters: list[Semester] = []
        for label in labels:
            sem_url = f"{self._base_url}/goto.php?target=iLUBModsSemester{semesters_str}&default={label}"
            semesters.append(Semester(label=label, url=sem_url, is_current=(label in active_labels)))
        logger.info("Found %d semester(s): %s", len(semesters), labels)
        return semesters

    async def get_courses(self, semester_label: str | None = None) -> list[Course]:
        """
        Return all courses for the given semester (or the current semester if None).

        Navigation strategy:
          1. Navigate to the stable goto.php URL (default={label}), which usually
             pre-selects the requested semester via a server-side redirect.
          2. Verify the active tab.  If ILIAS did not honour the default= parameter
             (e.g. due to a server-side session preference), click the correct tab so
             the page updates to the right semester before extracting courses.
        """
        page = self._browser.page
        target_url = await self._get_semester_url(page, semester_label)
        await page.goto(target_url)
        await page.wait_for_load_state("networkidle")

        if semester_label:
            active = page.locator(f"button[aria-label='{semester_label}'][aria-pressed='true']").first
            if await active.count() == 0:
                # The page landed on the wrong semester — click the correct tab.
                tab = page.locator(f"button[aria-label='{semester_label}']").first
                if await tab.count() > 0:
                    await tab.click()
                    await page.wait_for_load_state("networkidle")
                    logger.debug("Clicked semester tab '%s' (default= URL didn't select it).", semester_label)
                else:
                    logger.warning("Semester tab '%s' not found on page.", semester_label)

        logger.info("Navigated to semester '%s'. URL: %s", semester_label or "current", page.url)

        # Extract courses — rendered as <button data-action="...&ref_id=..."> inside .il-item-title
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

        logger.info("Found %d course(s) for semester '%s'.", len(courses), semester_label or "current")
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
            # Strip institutional prefix (e.g. "FS2026: " or "FS2026_ " or "450407-FS2026-0: ")
            stripped_title = re.sub(r"^(?:\d+[\w-]*[_:]|[A-Z]+\d+[_:])\s*", "", video.title).strip() or video.title
            safe_title = re.sub(r'[<>:"/\\|?*]', "_", stripped_title)
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
        except (PlaywrightError, OSError):
            logger.warning("Failed to download subtitle for '%s'", video.title, exc_info=True)

    async def get_remote_size(self, url: str) -> int | None:
        try:
            response = await self._browser.page.context.request.head(url)
            cl = response.headers.get("content-length")
            return int(cl) if cl else None
        except (PlaywrightError, OSError, ValueError):
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

## interface/schemas.py

Pydantic response models for MCP tool structured output. FastMCP derives each tool's
`outputSchema` from its return type annotation — returning these models (instead of a bare
`dict`/`list[dict]`) gives clients and LLMs a validated, typed `structuredContent` block per the
MCP tool spec, instead of a generic untyped object schema.

```python
"""
Interface layer — Pydantic response models for MCP tool structured output.

FastMCP derives each tool's outputSchema from its return type annotation.
Returning these models (instead of bare dict/list[dict]) gives clients and
LLMs a validated, typed structuredContent block per the MCP tool spec.
"""

from pydantic import BaseModel


class SemesterOut(BaseModel):
    label: str
    url: str
    is_current: bool


class CourseOut(BaseModel):
    title: str
    ref_id: str
    url: str


class CourseListOut(BaseModel):
    semester: str
    courses: list[CourseOut]


class CourseFileOut(BaseModel):
    title: str
    file_name: str
    file_type: str
    url: str


class ContentFileOut(BaseModel):
    title: str
    file_name: str
    file_type: str
    download_url: str


class ContentItemOut(BaseModel):
    title: str
    ref_id: str
    url: str
    type: str
    files: list[ContentFileOut] | None = None


class VideoOut(BaseModel):
    title: str
    event_id: str
    date: str
    url: str
    download_url: str
    subtitle_url: str
```

Tools that return a formatted, human-readable report (`list_course_content`, `login`,
`download_course_files`, `download_all_files`, `download_status`) intentionally keep a plain
`str` return type instead of one of these models — they are text summaries, not structured data,
matching the SDK's own `echo`-style plain-text tool pattern.

---

## interface/tools/auth_tools.py

```python
"""
Interface layer — MCP tools for authentication.
"""

import logging

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from mcp.types import ToolAnnotations

from interface.context import AppContext, app_from_ctx

logger = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        title="Login to ILIAS",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
        ),
    )
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
from mcp.types import ToolAnnotations

from domain.models import RefId
from domain.utils import clean_text
from interface.context import AppContext, app_from_ctx
from interface.schemas import (
    ContentFileOut,
    ContentItemOut,
    CourseFileOut,
    CourseListOut,
    CourseOut,
    SemesterOut,
    VideoOut,
)

logger = logging.getLogger(__name__)

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)


def register(mcp: FastMCP) -> None:
    @mcp.tool(title="List Semesters", annotations=_READ_ONLY)
    async def list_semesters(ctx: Context[ServerSession, AppContext]) -> list[SemesterOut]:
        """
        List all available semesters on the ILIAS dashboard. Login first.
        Returns each semester's label (e.g. "HS2025") and whether it is the current one.
        Use the label with list_courses, download_course_files, and download_all_files
        to target a specific semester.
        """
        app = app_from_ctx(ctx)
        app.rate_limiter.check("list_semesters")
        logger.info("Tool 'list_semesters' called.")
        semesters = await app.course_service.list_semesters()
        return [
            SemesterOut(label=s.label, url=s.url, is_current=s.is_current)
            for s in semesters
        ]

    @mcp.tool(title="List Courses", annotations=_READ_ONLY)
    async def list_courses(
        ctx: Context[ServerSession, AppContext],
        semester: str = "",
    ) -> CourseListOut:
        """
        List all courses for a given semester. Login first.

        IMPORTANT: the semester parameter controls which semester is shown.
        - Always call list_semesters first to see available labels (e.g. "HS2025", "FS2026").
        - Pass semester="HS2025" to get courses from HS2025.
        - Leave semester empty to get courses from the current semester.
        - The response includes a "semester" field showing which semester was actually loaded —
          verify it matches what was requested.

        Args:
            semester: Semester label to load, e.g. "HS2025" or "FS2026".
                      Leave empty to use the current semester.
        """
        app = app_from_ctx(ctx)
        app.rate_limiter.check("list_courses")
        sem = semester or None
        logger.info("Tool 'list_courses' called (semester=%s).", sem or "current")
        courses = await app.course_service.list_courses(sem)
        return CourseListOut(
            semester=semester if semester else "current",
            courses=[CourseOut(title=clean_text(c.title), ref_id=c.ref_id, url=c.url) for c in courses],
        )

    @mcp.tool(title="List Course Documents", annotations=_READ_ONLY)
    async def list_course_content_docs(ctx: Context[ServerSession, AppContext], ref_id: str) -> list[ContentItemOut]:
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

        result: list[ContentItemOut] = []
        for item in items:
            entry = ContentItemOut(title=clean_text(item.title), ref_id=item.ref_id, url=item.url, type=item.item_type)
            if "ordner" in item.item_type.lower():
                files = await app.download_service.list_course_files(RefId(item.ref_id))
                entry.files = [
                    ContentFileOut(title=clean_text(f.title), file_name=f.file_name, file_type=f.file_type, download_url=f.url)
                    for f in files
                ]
            result.append(entry)
        return result

    @mcp.tool(title="List Course Videos", annotations=_READ_ONLY)
    async def list_course_content_video(ctx: Context[ServerSession, AppContext], ref_id: str) -> list[VideoOut]:
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
            VideoOut(
                title=clean_text(v.title), event_id=v.event_id, date=clean_text(v.date),
                url=v.url, download_url=v.download_url, subtitle_url=v.subtitle_url,
            )
            for v in videos
        ]

    @mcp.tool(title="List Course Content", annotations=_READ_ONLY)
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
            lines.append(f"\n## {s.item.item_type}: {clean_text(s.item.title)}  (ref_id={s.item.ref_id})")
            if s.files:
                for f in s.files:
                    label = clean_text(f.file_name or f.title)
                    lines.append(f"  - [{label}]({f.url})")
            elif s.videos:
                for v in s.videos:
                    lines.append(f"  - {clean_text(v.title)} ({clean_text(v.date)})")
                    lines.append(f"    Stream:   {v.url}")
                    lines.append(f"    Download: {v.download_url}")
                    if v.subtitle_url:
                        lines.append(f"    Subtitle: {v.subtitle_url}")
            else:
                lines.append(f"  URL: {s.item.url}")
        return "\n".join(lines)

    @mcp.tool(title="List Course Files", annotations=_READ_ONLY)
    async def list_course_files(ctx: Context[ServerSession, AppContext], ref_id: str) -> list[CourseFileOut]:
        """
        Recursively list all downloadable files in a course.

        Args:
            ref_id: The ILIAS ref_id of the course (obtained from list_courses).
        """
        app = app_from_ctx(ctx)
        app.rate_limiter.check("list_course_files")
        logger.info("Tool 'list_course_files' called with ref_id=%s.", ref_id)
        files = await app.download_service.list_course_files(RefId(ref_id))
        return [CourseFileOut(title=clean_text(f.title), file_name=f.file_name, file_type=f.file_type, url=f.url) for f in files]
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
from mcp.types import ToolAnnotations

from application.download_service import DownloadTracker, _build_summary
from domain.models import RefId
from interface.context import AppContext, app_from_ctx

logger = logging.getLogger(__name__)

# Downloads write new files and occasionally overwrite a stale local copy
# (on remote/local size mismatch) — not read-only, and destructiveHint=True
# reflects that possible overwrite so clients can prompt before running it.
_DOWNLOAD = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
)
_STATUS = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)


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
    @mcp.tool(title="Download Course Files", annotations=_DOWNLOAD)
    async def download_course_files(
        ctx: Context[ServerSession, AppContext],
        ref_id: str,
        semester: str = "",
    ) -> str:
        """
        Download all files from a single course to DOWNLOAD_DIR (.env).
        When a semester is specified, files are saved under DOWNLOAD_DIR/<semester>/<course>/.
        Always call list_course_content first and show the result to the user before calling this tool.

        Args:
            ref_id: The ILIAS ref_id of the course (obtained from list_courses).
            semester: Semester label, e.g. "HS2025" (from list_semesters).
                      Used both for navigation and as the subfolder name. Leave empty for current semester.
        """
        app = app_from_ctx(ctx)
        app.rate_limiter.check("download_course_files")
        logger.info("Tool 'download_course_files' called with ref_id=%s, semester=%s.", ref_id, semester or "current")
        app.download_tracker.reset()
        base_dir = Path(download_dir) / semester if semester else Path(download_dir)
        log_lines: list[str] = []
        downloaded, skipped = await app.download_service.download_course(
            RefId(ref_id),
            base_dir,
            app.download_tracker,
            log=_progress_log(ctx, log_lines),
            semester_label=semester or None,
        )
        summary = _build_summary(downloaded, skipped, 1, base_dir, app.download_tracker)
        progress_log = "\n".join(log_lines)
        return f"=== Progress Log ===\n{progress_log}\n\n{summary}"

    @mcp.tool(title="Download All Files", annotations=_DOWNLOAD)
    async def download_all_files(
        ctx: Context[ServerSession, AppContext],
        semester: str = "",
    ) -> str:
        """
        Download every file from every course to the directory configured in DOWNLOAD_DIR (.env).
        When a semester is specified, files are saved under DOWNLOAD_DIR/<semester>/<course>/.

        Args:
            semester: Semester label, e.g. "HS2025" (from list_semesters).
                      Used both for navigation and as the subfolder name. Leave empty for current semester.
        """
        app = app_from_ctx(ctx)
        app.rate_limiter.check("download_all_files")
        logger.info("Tool 'download_all_files' called (semester=%s).", semester or "current")
        base_dir = Path(download_dir) / semester if semester else Path(download_dir)
        log_lines: list[str] = []
        summary = await app.download_service.download_all(
            base_dir,
            app.download_tracker,
            log=_progress_log(ctx, log_lines),
            semester_label=semester or None,
        )
        progress_log = "\n".join(log_lines)
        return f"=== Progress Log ===\n{progress_log}\n\n{summary}"

    @mcp.tool(title="Download Status", annotations=_STATUS)
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
        "- `list_semesters` — list all available semesters (e.g. HS2025, FS2026) with their labels\n"
        "- `list_courses` — list enrolled courses; pass semester=\"HS2025\" to get courses from a specific semester\n"
        "- `list_course_content` — list all course content: auto-expands folders (files+download URLs) and Opencast series (videos+download URLs)\n"
        "- `list_course_content_docs` — list top-level INHALT items of a course (folders auto-expanded, no video expansion)\n"
        "- `list_course_content_video` — list Opencast video recordings for a specific series ref_id\n"
        "- `list_course_files` — recursively list all downloadable files in a course\n"
        "- `download_course_files` — download all files from a single course; pass semester=\"HS2025\" to target a specific semester (files saved to DOWNLOAD_DIR/<semester>/<course>/)\n"
        "- `download_all_files` — download all files from all courses; pass semester=\"HS2025\" to target a specific semester\n"
        "Semester workflow: call list_semesters → note the label (e.g. \"HS2025\") → call list_courses(semester=\"HS2025\") → verify response.semester matches → download with semester=\"HS2025\""
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

### Semester handling
- `IliasCourseAdapter._get_semester_url` resolves the stable `goto.php?target=iLUBModsSemester{labels}&default={label}` URL by reading the "Aktuelles Semester" nav link once and substituting the `default=` query param — this URL survives across ILIAS sessions and does not depend on click state.
- `get_semesters()` parses all available labels from the `target=` parameter (underscore-joined, e.g. `HS2026_FS2026_HS2025_FS2025_HS2024`) and detects the active one via `button[aria-label='{label}'][aria-pressed='true']`.
- `get_courses(semester_label)` navigates to the `default={label}` URL; if ILIAS does not honour that parameter (server-side session preference can override it), it falls back to clicking the matching semester tab directly before extracting courses.
- Course/video titles from ILIAS often carry an institutional code prefix (e.g. `"450407-FS2026-0: Grundzüge Erdwissenschaften II"` or `"FS2026: Lektion 1"`). `_strip_course_prefix` / `_strip_video_title` (in `download_service.py`) and the inline stripping in `IliasFileAdapter.download_video` remove this prefix for local directory/file names, while `_course_dir` and `_video_expected_path` still fall back to the old unstripped name if a matching directory/file from before this change already exists — so previously downloaded courses/videos are not re-downloaded.
- `download_course` / `download_all` / the `download_course_files` / `download_all_files` MCP tools all accept an optional `semester_label` / `semester` parameter; when set, the download tools additionally save into `DOWNLOAD_DIR/<semester>/<course>/` instead of `DOWNLOAD_DIR/<course>/`.

### Tool annotations & structured output (interface/tools/, interface/schemas.py)
- Every `@mcp.tool(...)` registration sets `title=` (human-readable display name) and `annotations=ToolAnnotations(...)` per the MCP tool spec, so clients can decide when to prompt the user before invoking a tool:
  - All `list_*` tools: `readOnlyHint=True, idempotentHint=True, openWorldHint=True` (shared `_READ_ONLY` constant in `course_tools.py`).
  - `login`: `readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True`.
  - `download_course_files` / `download_all_files`: `readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True` — `destructiveHint=True` because a local/remote file-size mismatch triggers an overwrite of the existing file (see "Download skip logic" below).
  - `download_status`: `readOnlyHint=True, idempotentHint=True, openWorldHint=False` — it only reads in-memory tracker state, no ILIAS network call.
- Tools that return list/dict data (`list_semesters`, `list_courses`, `list_course_content_docs`, `list_course_content_video`, `list_course_files`) return Pydantic models from `interface/schemas.py` instead of bare `dict`/`list[dict]`. FastMCP derives a typed `outputSchema` (with real `$defs`/`properties`, not a generic object) from these return-type annotations, and populates `structuredContent` on every tool call automatically — no extra wiring needed beyond the return type.
- Tools that return a formatted text report (`login`, `list_course_content`, `download_course_files`, `download_all_files`, `download_status`) intentionally keep `-> str`; FastMCP still auto-wraps these in a generic `{"result": "..."}` structured block, but the meaningful payload is the human-readable text content, not structured data.

### Download directory layout

- Course root: `output_dir/<course_title>/` (or `DOWNLOAD_DIR/<semester>/<course_title>/` when a semester is specified — see "Semester handling" above)
- Folder sections (Ordner) and Opencast sections: saved into `output_dir/<course_title>/<section_title>/`
- Top-level documents and other items: saved directly into `output_dir/<course_title>/`

### Subtitle handling

- `list_videos` discriminates subtitle links from video download links by reading the `button.dropdown-toggle` text: `"Download"` → video links; `"Untertitel"` → subtitle URL
- `VideoItem.subtitle_url` carries the subtitle download URL (empty string if none)
- `download_video` calls `_download_subtitle` after saving the video; subtitle is saved with the same stem as the video file but with the extension from the suggested filename (default `.srt`); subtitle failures (`PlaywrightError`, `OSError`) are logged as warnings, not raised

### ILIAS DOM selectors (confirmed for ilias.unibe.ch ILIAS v9)
| What | Selector |
|------|----------|
| Institution dropdown | `select#user_idp` |
| Login submit | `input#wayf_submit_button` |
| Email field | `input#username, input[name='j_username']` |
| Password submit | `button#button-submit` (with fallbacks) |
| Semester nav link | `a:has-text('Aktuelles Semester')` |
| Semester tab button | `button[aria-label='{label}']`, active state via `[aria-pressed='true']` |
| Course items | `.il-item-title button[data-action*='ref_id']` |
| INHALT block | `.ilContainerBlock` with `h2/h3` text "inhalt" |
| Content rows | `.ilObjListRow`, `.il-std-item`, `.il_ContainerListItem` |
| File links | `a[href*="cmd=sendfile"], a[href*="goto.php?target=file_"]` |
| Folder links (v9) | `a[href*='/go/fold/']` |
| Video table | `table[id*="tbl_xoct"] tbody tr` |

### URL patterns
- Course page: `{base_url}/ilias.php?ref_id={id}&cmd=view&baseClass=ilrepositorygui`
- Opencast series: `{base_url}/ilias.php?baseClass=ilObjPluginDispatchGUI&cmd=forward&ref_id={id}&forwardCmd=showContent`
- Semester listing: `{base_url}/goto.php?target=iLUBModsSemester{labels_joined_by_underscore}&default={label}`
- Video download links ordered highest → lowest resolution inside each row

### File deduplication
URLs are normalised by stripping `cmdNode=[^&]*&?` before dedup. This removes the ephemeral
`cmdNode` parameter that ILIAS includes in links but which doesn't affect the target file.

### Download skip logic
- Skip if local file exists AND (remote size unavailable OR remote size == local size).
- Re-download if sizes differ (size mismatch = partial/updated file) — this is the case that makes `download_course_files`/`download_all_files` carry `destructiveHint=True` (see "Tool annotations & structured output" above).
- Video skip: checks for `{stripped_title}_{date}.mp4` first (current, prefix-stripped naming), then falls back to `{original_title}_{date}.mp4` for files downloaded before prefix stripping was introduced, then globs for any extension.

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
Python packages. `interface/schemas.py` is **not** an `__init__.py` — it is a regular module with
the Pydantic response models documented above.
