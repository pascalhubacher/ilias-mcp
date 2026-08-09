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
