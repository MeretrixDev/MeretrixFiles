from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Blob, File, User
from app.services.storage import storage_upload, delete_file, blob_key, QuotaExceededError, UploadLimits, get_usage
from app.services.blobstore import make_blob_storage


pytestmark = pytest.mark.skipif(
    not get_settings().database_url.startswith("postgresql"),
    reason="SQLite сериализует записи, гонку можно проверить только на PostgreSQL",
)


def test_concurrent_uploads_of_same_content_create_one_blob(settings):
    data = b"race condition " * 2000

    store = make_blob_storage(settings)

    def worker(i: int) -> None:
        with SessionLocal() as db:  # у каждого потока своя сессия
            storage_upload(db, settings, store, [data], filename=f"{i}.txt")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(8)))  # исключение в потоке всплывёт здесь

    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Blob)) == 1
        assert db.scalar(select(func.count()).select_from(File)) == 8


def test_concurrent_upload_and_delete_never_lose_a_live_file(settings):
    data = b"churn " * 5000
    store = make_blob_storage(settings)

    def worker(_: int) -> None:
        for _i in range(15):
            with SessionLocal() as db:
                file, _token = storage_upload(db, settings, store, [data], filename="x.txt")
                key = blob_key(file.blob.sha256, file.blob.algorithm)
                # Мою запись другие потоки удалить не могут, значит, файл обязан быть
                assert store.exists(key), "file of a live record disappeared"
                delete_file(db, store, file)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(worker, range(6)))

    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Blob)) == 0
    leftovers = [
        p for p in settings.storage_dir.rglob("*")
        if p.is_file() and settings.tmp_dir not in p.parents
    ]
    assert leftovers == []


def test_parallel_uploads_cannot_exceed_the_quota(settings):
    store = make_blob_storage(settings)
    with SessionLocal() as db:
        user = User(email="u@example.com", password_hash="x")
        db.add(user)
        db.commit()
        uid = user.id
    limits = UploadLimits(max_size=10**9, quota_bytes=250_000)

    def worker(i: int) -> bool:
        with SessionLocal() as db:
            try:
                storage_upload(
                    db, settings, store, [bytes([65 + i]) * 100_000],
                    filename=f"{i}.txt", owner_id=uid, limits=limits,
                )
                return True
            except QuotaExceededError:
                return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(worker, range(8)))

    assert sum(results) == 2  # в 250 КБ помещаются ровно два файла по 100 КБ
    with SessionLocal() as db:
        assert get_usage(db, uid).used_bytes == 200_000

