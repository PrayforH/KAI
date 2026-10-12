"""Filename-based archive allowance; office ZIP containers stay ordinary inputs."""

ARCHIVE_EXTENSIONS = (".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".dmg")
DEFAULT_ARCHIVE_UPLOAD_BYTES = 500 * 1024 * 1024


def is_archive_attachment(name: str) -> bool:
    return name.lower().endswith(ARCHIVE_EXTENSIONS)
