"""Safe response metadata for user-controlled filenames."""

from urllib.parse import quote


def attachment_content_disposition(filename: str, *, inline: bool = False) -> str:
    normalized = filename.replace("\x00", "").strip() or "download"
    return f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(normalized, safe='')}"
