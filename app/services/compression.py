import hashlib
import itertools

from collections.abc import Iterable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from app.services.blobstore import BlobStore
from app.services.seekable import SeekableWriter, SeekTable, iter_plain_range, read_seek_table

class FileTooLargeError(Exception):
    """Raised when file too large"""

@dataclass(frozen=True)
class StoreResult:
    sha256: str
    orig_size: int # размер до сжатия
    stored_size: int # размер на диске
    algo: str # zstd или none


ALGO_ZSTD = "zstd-seekable"
ALGO_NONE = "none"

# Форматы, которые уже сжаты
_MAGIC_PREFIXES = (
    b"\xff\xd8\xff",          # JPEG
    b"\x89PNG\r\n\x1a\n",     # PNG
    b"GIF8",                  # GIF
    b"PK\x03\x04",            # ZIP, docx, xlsx, jar...
    b"\x1f\x8b",              # gzip
    b"BZh",                   # bzip2
    b"\xfd7zXZ\x00",          # xz
    b"7z\xbc\xaf\x27\x1c",    # 7z
    b"Rar!",                  # RAR
    b"\x28\xb5\x2f\xfd",      # zstd
    b"\x1a\x45\xdf\xa3",      # Matroska/WebM
    b"OggS",                  # ogg
    b"ID3",                   # mp3 с тегами
)

_COMPRESSED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".heic",
    ".zip", ".gz", ".bz2", ".xz", ".zst", ".7z", ".rar",
    ".mp3", ".aac", ".ogg", ".opus", ".flac",
    ".mp4", ".mkv", ".webm", ".mov", ".avi",
}

def is_already_compressed(filename: str, head: bytes) -> bool:
    #Смотрим первые байты
    if Path(filename).suffix.lower() in _COMPRESSED_EXTENSIONS:
        return True
    if head.startswith(_MAGIC_PREFIXES):
        return True
    if head[4:8] == b"ftyp": # MP4/MOV/HEIC
        return True
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return True
    return False

def store_stream(
        chunks: Iterable[bytes],
        dst: Path,
        *,
        filename: str,
        max_size: int,
        level: int = 3,
        frame_size: int = 1024 * 1024,
) -> StoreResult:
    it = iter(chunks)
    head = next(it, b"")
    algo = ALGO_NONE if is_already_compressed(filename, head) else ALGO_ZSTD
    stream = itertools.chain((head,), it)

    sha = hashlib.sha256()
    size = 0

    try:
        with dst.open("wb") as raw, ExitStack() as stack:
            if algo == ALGO_ZSTD:
                sink = stack.enter_context(
                    SeekableWriter(raw, level=level, frame_size=frame_size)
                )
            else:
                sink = raw

            for chunk in stream:
                size += len(chunk)
                if size > max_size:
                    raise FileTooLargeError(f"File too large: {filename} over {max_size} bytes")
                sha.update(chunk)
                sink.write(chunk)
    except BaseException:
        dst.unlink(missing_ok=True)
        raise

    return StoreResult(
        sha256=sha.hexdigest(),
        orig_size=size,
        stored_size=dst.stat().st_size,
        algo=algo,
    )


class BlobReader:
    def __init__(self, store: BlobStore, key: str, algorithm: str, chunk_size: int) -> None:
        self._store = store
        self._key = key
        self._chunk_size = chunk_size

        self._table: SeekTable | None = (
            read_seek_table(store, key) if algorithm == ALGO_ZSTD else None
        )

        self.size = self._table.size if self._table else store.size(key)

    def iter_range(self, start: int, end: int) -> Iterator[bytes]:
        if self._table is not None:
            yield from iter_plain_range(self._store, self._key, self._table, start, end)
            return
        pos = start
        while pos <= end:
            data = self._store.read_range(self._key, pos, min(self._chunk_size, end - pos + 1))
            if not data:
                break
            yield data
            pos += len(data)

