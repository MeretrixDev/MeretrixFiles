import hashlib

import pytest

from app.services.compression import FileTooLargeError, iter_decompressed, store_stream

def test_text_is_compressed_and_restored_exactly(tmp_path):
    # Arrange
    data = b"hello world " * 10_000
    dst = tmp_path / "blob"

    # Act
    result = store_stream([data], dst, filename="note.txt", max_size=10**9)

    # Assert
    assert result.algo == "zstd"
    assert result.stored_size < result.orig_size
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    restored = b"".join(iter_decompressed(dst, result.algo, 1024))
    assert restored == data


def test_oversized_upload_is_rejected_and_leaves_no_file(tmp_path):
    dst = tmp_path / "blob"

    with pytest.raises(FileTooLargeError):
        store_stream([b"x" * 100, b"x" * 100], dst, filename="a.txt", max_size=150)

    assert not dst.exists()