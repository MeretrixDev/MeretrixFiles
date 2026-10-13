from datetime import timedelta

from sqlalchemy import func, select

from app.models import Blob, File
from app.services.storage import delete_file, purge_expired, storage_upload
from app.services.blobstore import blob_key
from app.services import storage


def put(db, settings, store, data: bytes, name="a.txt", ttl=None):
    file, _token = storage_upload(db, settings, store, [data], filename=name, ttl=ttl)
    return file


def key_of(file):
    return blob_key(file.blob.sha256, file.blob.algorithm)


def test_blob_is_deleted_only_with_its_last_file(db, settings, store):
    f1 = put(db, settings, store, b"payload" * 100, "a.txt")
    f2 = put(db, settings, store, b"payload" * 100, "b.txt")
    key = key_of(f1)

    delete_file(db, store, f1)
    assert store.exists(key)  # на blob ещё ссылается f2

    delete_file(db, store, f2)
    assert not store.exists(key)
    assert db.scalar(select(func.count()).select_from(Blob)) == 0


def test_purge_expired_removes_only_expired(db, settings, store):
    expired = put(db, settings, store, b"old" * 100, "old.txt", ttl=timedelta(hours=-1))
    alive = put(db, settings, store, b"new" * 100, "new.txt", ttl=timedelta(hours=1))
    expired_key, alive_key = key_of(expired), key_of(alive)

    assert purge_expired(db, store) == 1

    assert not store.exists(expired_key)
    assert store.exists(alive_key)


def test_purge_continues_after_a_failure(db, settings, store, monkeypatch):
    for i in range(2):
        put(db, settings, store, f"data{i}".encode() * 100, f"{i}.txt", ttl=timedelta(hours=-1))
    real = storage.delete_file
    calls = []

    def flaky(db_, store_, file):
        calls.append(file.id)
        if len(calls) == 1:
            raise RuntimeError("boom")
        real(db_, store_, file)

    monkeypatch.setattr(storage, "delete_file", flaky)

    assert purge_expired(db, store) == 1
    assert db.scalar(select(func.count()).select_from(File)) == 1  # один удалён, один остался

