"""
Interface layer — MCP tools for course listing and content exploration.
"""

import logging

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from domain.models import RefId
from domain.utils import clean_text
from interface.context import AppContext, app_from_ctx

logger = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def list_semesters(ctx: Context[ServerSession, AppContext]) -> list[dict]:
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
            {"label": s.label, "url": s.url, "is_current": s.is_current}
            for s in semesters
        ]

    @mcp.tool()
    async def list_courses(
        ctx: Context[ServerSession, AppContext],
        semester_label: str = "",
    ) -> dict:
        """
        List all courses for a given semester. Login first.

        IMPORTANT: semester_label controls which semester is shown.
        - Always call list_semesters first to see available labels (e.g. "HS2025", "FS2026").
        - Pass semester_label="HS2025" to get courses from HS2025.
        - Leave semester_label empty to get courses from the current semester.
        - The response includes a "semester" field showing which semester was actually loaded —
          verify it matches what was requested.

        Args:
            semester_label: Semester label to load, e.g. "HS2025" or "FS2026".
                            Leave empty to use the current semester.
        """
        app = app_from_ctx(ctx)
        app.rate_limiter.check("list_courses")
        sem = semester_label or None
        logger.info("Tool 'list_courses' called (semester=%s).", sem or "current")
        courses = await app.course_service.list_courses(sem)
        return {
            "semester": semester_label if semester_label else "current",
            "courses": [{"title": clean_text(c.title), "ref_id": c.ref_id, "url": c.url} for c in courses],
        }

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
            entry: dict = {"title": clean_text(item.title), "ref_id": item.ref_id, "url": item.url, "type": item.item_type}
            if "ordner" in item.item_type.lower():
                files = await app.download_service.list_course_files(RefId(item.ref_id))
                entry["files"] = [
                    {"title": clean_text(f.title), "file_name": f.file_name, "file_type": f.file_type, "download_url": f.url}
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
            {"title": clean_text(v.title), "event_id": v.event_id, "date": clean_text(v.date), "url": v.url, "download_url": v.download_url, "subtitle_url": v.subtitle_url}
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
        return [{"title": clean_text(f.title), "file_name": f.file_name, "file_type": f.file_type, "url": f.url} for f in files]
