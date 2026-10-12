import pytest

from app.services.blobstore import LocalBlobStore, blob_key


def test_put_moves_file_and_range_reads_work(tmp_path):
    store = LocalBlobStore(tmp_path / "root")
    src = tmp_path / "upload.tmp"
    src.write_bytes(b"0123456789")

    store.put("ab/abc.raw", src)

    assert not src.exists()
    assert store.exists("ab/abc.raw")
    assert store.size("ab/abc.raw") == 10
    assert store.read_range("ab/abc.raw", 3, 4) == b"3456"
    assert store.read_range("ab/abc.raw", 8, 100) == b"89"  # за концом: сколько есть


def test_delete_is_idempotent(tmp_path):
    store = LocalBlobStore(tmp_path)

    store.delete("ab/missing.raw")  # не падает


def test_path_traversal_is_rejected(tmp_path):
    store = LocalBlobStore(tmp_path / "root")

    with pytest.raises(ValueError):
        store.size("../outside")


def test_blob_key_fans_out_by_hash_prefix():
    assert blob_key("abcdef", "zstd-seekable") == "ab/abcdef.zst"
    assert blob_key("abcdef", "none") == "ab/abcdef.raw"
    with pytest.raises(KeyError):
        blob_key("abcdef", "zstd")  # старый формат без таблицы кадров