from datetime import timedelta

from sqlalchemy import func, select

from app.models import Blob, File
from app.services.storage import delete_file, purge_expired, storage_upload
from app.services.blobstore import blob_key
from app.services import storage
from app.db import SessionLocal


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


def test_delete_does_not_remove_file_recreated_between_phases(
    db, settings, store, monkeypatch
):
    data = b"payload" * 100
    f1 = put(db, settings, store, data, "a.txt")
    key = key_of(f1)
    real_lock = storage.lock_blob
    state = {"calls": 0, "injected": False}

    def lock_with_injection(session, sha):
        state["calls"] += 1
        # 1-й вызов: фаза 1 удаления. 2-й: начало фазы 2, как раз в этот момент
        # «параллельная» загрузка успевает создать Blob заново
        if state["calls"] == 2 and not state["injected"]:
            state["injected"] = True
            with SessionLocal() as other:
                storage_upload(other, settings, store, [data], filename="b.txt")
        real_lock(session, sha)

    monkeypatch.setattr(storage, "lock_blob", lock_with_injection)

    delete_file(db, store, f1)

    assert store.exists(key)  # файл новой загрузки на месте
    assert db.scalar(select(func.count()).select_from(Blob)) == 1

