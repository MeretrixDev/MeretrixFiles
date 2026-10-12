import random

import pytest
import zstandard as zstd

from app.services.blobstore import LocalBlobStore
from app.services.compression import ALGO_ZSTD, BlobReader, store_stream
from app.services.seekable import CorruptBlobError, read_seek_table

FRAME = 10_000
# Случайные байты: на повторяющихся данных ошибка в смещении осталась бы незаметной
DATA = random.Random(1).randbytes(255_000)


@pytest.fixture
def blob(tmp_path):
    chunks = [DATA[i : i + 7_777] for i in range(0, len(DATA), 7_777)]  # «неудобные» куски
    store_stream(chunks, tmp_path / "blob", filename="data.txt", max_size=10**9, frame_size=FRAME)
    return LocalBlobStore(tmp_path), "blob"


def test_frames_are_cut_at_frame_size_regardless_of_chunking(blob):
    store, key = blob

    table = read_seek_table(store, key)
    sizes = [b - a for a, b in zip(table.plain_offsets, table.plain_offsets[1:])]

    assert table.size == len(DATA)
    assert sizes[:-1] == [FRAME] * 25
    assert sizes[-1] == 5_000


@pytest.mark.parametrize(
    "start,end",
    [
        (0, 0),
        (0, 9),
        (9_999, 10_000),      # граница двух кадров
        (10_000, 19_999),     # ровно один кадр
        (123, 45_678),
        (5, 254_999),         # почти весь файл
        (250_000, 254_999),   # неполный последний кадр
        (254_999, 254_999),   # последний байт
    ],
)
def test_range_matches_plain_slice(blob, start, end):
    store, key = blob
    reader = BlobReader(store, key, ALGO_ZSTD, 4096)

    assert b"".join(reader.iter_range(start, end)) == DATA[start : end + 1]


def test_empty_file_has_only_the_table(tmp_path):
    store = LocalBlobStore(tmp_path)

    store_stream([], tmp_path / "blob", filename="e.txt", max_size=10, frame_size=FRAME)
    reader = BlobReader(store, "blob", ALGO_ZSTD, 4096)

    assert reader.size == 0
    assert list(reader.iter_range(0, -1)) == []


def test_file_ends_with_seekable_magic(blob, tmp_path):
    assert (tmp_path / "blob").read_bytes()[-4:] == bytes.fromhex("b1ea928f")


def test_truncated_file_is_detected(blob, tmp_path):
    path = tmp_path / "blob"
    path.write_bytes(path.read_bytes()[:-5])

    with pytest.raises(CorruptBlobError):
        BlobReader(LocalBlobStore(tmp_path), "blob", ALGO_ZSTD, 4096)


def test_flipped_byte_fails_frame_checksum(blob, tmp_path):
    path = tmp_path / "blob"
    raw = bytearray(path.read_bytes())
    raw[100] ^= 0xFF  # внутри первого кадра
    path.write_bytes(raw)
    reader = BlobReader(LocalBlobStore(tmp_path), "blob", ALGO_ZSTD, 4096)

    with pytest.raises(zstd.ZstdError):
        b"".join(reader.iter_range(0, 99))

