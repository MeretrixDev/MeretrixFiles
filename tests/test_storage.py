from datetime import timedelta

from sqlalchemy import func, select

from app.models import Blob
from app.services.storage import blob_path, delete_file, purge_expired, storage_upload


def put(db, settings, data: bytes, name="a.txt", ttl=None):
    file, _token = storage_upload(db, settings, [data], filename=name, ttl=ttl)
    return file


def test_blob_is_deleted_only_with_its_last_file(db, settings):
    f1 = put(db, settings, b"payload" * 100, "a.txt")
    f2 = put(db, settings, b"payload" * 100, "b.txt")
    path = blob_path(settings, f1.blob.sha256, f1.blob.algorithm)

    delete_file(db, settings, f1)
    assert path.exists()  # на blob ещё ссылается f2

    delete_file(db, settings, f2)
    assert not path.exists()
    assert db.scalar(select(func.count()).select_from(Blob)) == 0


def test_purge_expired_removes_only_expired(db, settings):
    expired = put(db, settings, b"old" * 100, "old.txt", ttl=timedelta(hours=-1))
    alive = put(db, settings, b"new" * 100, "new.txt", ttl=timedelta(hours=1))
    expired_path = blob_path(settings, expired.blob.sha256, expired.blob.algorithm)
    alive_path = blob_path(settings, alive.blob.sha256, alive.blob.algorithm)

    assert purge_expired(db, settings) == 1

    assert not expired_path.exists()
    assert alive_path.exists()