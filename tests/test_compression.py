import hashlib

import pytest

from app.services.blobstore import LocalBlobStore
from app.services.compression import BlobReader, FileTooLargeError, store_stream


def read_back(tmp_path, key, algorithm) -> bytes:
    reader = BlobReader(LocalBlobStore(tmp_path), key, algorithm, 1024)
    return b"".join(reader.iter_range(0, reader.size - 1))


def test_text_is_compressed_and_restored_exactly(tmp_path):
    data = b"hello world " * 10_000

    result = store_stream([data], tmp_path / "blob", filename="note.txt", max_size=10**9)

    assert result.algo == "zstd-seekable"
    assert result.stored_size < result.orig_size
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert read_back(tmp_path, "blob", result.algo) == data


def test_oversized_upload_is_rejected_and_leaves_no_file(tmp_path):
    dst = tmp_path / "blob"

    with pytest.raises(FileTooLargeError):
        store_stream([b"x" * 100, b"x" * 100], dst, filename="a.txt", max_size=150)

    assert not dst.exists()