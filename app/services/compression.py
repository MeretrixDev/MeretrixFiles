import hashlib
import itertools
import zstandard as zstd

from collections.abc import Iterable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

class FileTooLargeError(Exception):
    """Raised when file too large"""

@dataclass(frozen=True)
class StoreResult:
    sha256: str
    orig_size: int # размер до сжатия
    stored_size: int # размер на диске
    algo: str # zstd или none


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

def store_stream(chunks: Iterable[bytes], dst: Path, *, filename: str, max_size: int, level: int = 3) -> StoreResult:
    it = iter(chunks)
    head = next(it, b"")
    algo = "none" if is_already_compressed(filename, head) else "zstd"
    stream = itertools.chain((head,), it)

    sha = hashlib.sha256()
    size = 0

    try:
        with dst.open("wb") as raw, ExitStack() as stack:
            if algo == "zstd":
                cctx = zstd.ZstdCompressor(level=level)
                sink = stack.enter_context(cctx.stream_writer(raw, closefd=False))
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

def iter_decompressed(path: Path, algo: str, chunk_size: int) -> Iterator[bytes]:
    with path.open("rb") as f:
        if algo == "zstd":
            with zstd.ZstdDecompressor().stream_reader(f) as reader:
                while chunk := reader.read(chunk_size):
                    yield chunk
        else:
            while chunk := f.read(chunk_size):
                yield chunk
