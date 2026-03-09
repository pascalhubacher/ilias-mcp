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

    @mcp.tool()
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
