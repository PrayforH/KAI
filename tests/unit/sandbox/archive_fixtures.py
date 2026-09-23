"""Archive bytes a sandbox would hand back for one workspace."""

from collections.abc import Iterable
from io import BytesIO
from tarfile import DIRTYPE, TarInfo
from tarfile import open as open_tar


def workspace_tar(
    entries: dict[str, bytes], *, directories: Iterable[str] = ()
) -> bytes:
    """Build the tar a provider's ``download_archive`` returns.

    Directories get their own members because ``tar -cf`` writes them, and the
    shared extractor creates the empty ones from exactly those members. Shared by
    every provider that collects through an archive, so the fake has one shape.
    """

    buffer = BytesIO()
    with open_tar(fileobj=buffer, mode="w") as archive:
        for name in directories:
            info = TarInfo(name.rstrip("/") + "/")
            info.type = DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        for name, content in entries.items():
            info = TarInfo(name)
            info.size = len(content)
            archive.addfile(info, BytesIO(content))
    return buffer.getvalue()
