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
