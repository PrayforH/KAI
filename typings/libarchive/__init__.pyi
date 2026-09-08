from collections.abc import Iterator
from contextlib import AbstractContextManager

class ArchiveEntry:
    pathname: str
    size: int | None
    isdir: bool
    isfile: bool
    issym: bool
    islnk: bool
    def get_blocks(self, block_size: int = ...) -> Iterator[bytes]: ...

def memory_reader(
    buf: bytes,
    format_name: str = ...,
    filter_name: str = ...,
    passphrase: str | bytes | None = ...,
    header_codec: str = ...,
) -> AbstractContextManager[Iterator[ArchiveEntry]]: ...
