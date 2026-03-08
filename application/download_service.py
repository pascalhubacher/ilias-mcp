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
