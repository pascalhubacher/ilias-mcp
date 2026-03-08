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
