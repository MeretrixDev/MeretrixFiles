import struct
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import accumulate
from typing import BinaryIO

import zstandard as zstd

from app.services.blobstore import BlobStore

_SKIPPABLE_MAGIC = 0x184D2A5E
_SEEKABLE_MAGIC = 0x8F92EAB1

_HEADER = struct.Struct("<II") # 8byte (length)
_ENTRY = struct.Struct("<II") # 8byte (snap size)
_FOOTER = struct.Struct("<IBI") # 9byte (snaps, flags, magic)


class CorruptBlobError(Exception): ...


class SeekableWriter:
    def __init__(self, raw: BinaryIO, *, level: int, frame_size: int) -> None:
        self._raw = raw
        self._frame_size = frame_size
        self._cctx = zstd.ZstdCompressor(level=level, write_checksum=True)
        self._buf = bytearray()
        self._entries: list[tuple[int, int]] = []

    def _flush_frame(self, n: int) -> None:
        part = bytes(self._buf[:n])
        del self._buf[:n]
        frame = self._cctx.compress(part)
        self._raw.write(frame)
        self._entries.append((len(frame), n))

    def write(self, data: bytes) -> int:
        self._buf += data
        while len(self._buf) >= self._frame_size:
            self._flush_frame(self._frame_size)
        return len(data)

    def close(self) -> None:
        if self._buf:
            self._flush_frame(len(self._buf))
        table = b"".join(_ENTRY.pack(c, d) for c, d in self._entries)
        footer = _FOOTER.pack(len(self._entries), 0, _SEEKABLE_MAGIC)

        self._raw.write(_HEADER.pack(_SKIPPABLE_MAGIC, len(table) + len(footer)))
        self._raw.write(table)
        self._raw.write(footer)

    def __enter__(self) -> "SeekableWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.close()


@dataclass(frozen=True)
class SeekTable:
    comp_offsets: list[int] # N + 1
    plain_offsets: list[int] # N + 1

    @property
    def frames(self) -> int:
        return len(self.plain_offsets) - 1

    @property
    def size(self) -> int:
        return self.plain_offsets[-1]


def read_seek_table(store: BlobStore, key: str) -> SeekTable:
    total = store.size(key)
    if total < _HEADER.size + _FOOTER.size:
        raise CorruptBlobError(f"file is too small")

    count, flags, magic = _FOOTER.unpack(
        store.read_range(key, total - _FOOTER.size, _FOOTER.size)
    )

    if magic != _SEEKABLE_MAGIC or flags != 0:
        raise CorruptBlobError(f"seek table footer not found")

    table_len = count * _ENTRY.size
    table_start = total - _FOOTER.size - table_len
    if table_start < _HEADER.size:
        raise CorruptBlobError(f"seek table does not fit in file")

    raw = store.read_range(key, table_start, table_len) if table_len else b""
    entries = list(_ENTRY.iter_unpack(raw))
    comp = list(accumulate((c for c, _ in entries), initial=0))
    plain = list(accumulate((d for _, d in entries), initial=0))

    if comp[-1] != table_start - _HEADER.size:
        raise CorruptBlobError(f"frame size do not match file size")
    return SeekTable(comp, plain)


def iter_plain_range(
        store: BlobStore,
        key: str,
        table: SeekTable,
        start: int,
        end: int
) -> Iterator[bytes]:
    dctx = zstd.ZstdDecompressor()
    first = bisect_right(table.comp_offsets, start) - 1 # кадр содержащий старт
    for i in range(first, table.frames):
        frame_start = table.plain_offsets[i]
        if frame_start > end:
            break

        c0, c1 = table.comp_offsets[i], table.comp_offsets[i + 1]
        excepted = table.plain_offsets[i + 1] - frame_start
        plain = dctx.decompress(store.read_range(key, c0, c1 - c0), max_output_size=excepted)

        if len(plain) != excepted:
            raise CorruptBlobError(f"frame {i} has unexpected size")
        yield plain[max(start - frame_start, 0) : end - frame_start + 1]

