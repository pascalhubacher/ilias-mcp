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
