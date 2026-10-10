from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Blob, File
from app.services.storage import storage_upload

pytestmark = pytest.mark.skipif(
    not get_settings().database_url.startswith("postgresql"),
    reason="SQLite сериализует записи, гонку можно проверить только на PostgreSQL",
)


def test_concurrent_uploads_of_same_content_create_one_blob(settings):
    data = b"race condition " * 2000

    def worker(i: int) -> None:
        with SessionLocal() as db:  # у каждого потока своя сессия
            storage_upload(db, settings, [data], filename=f"{i}.txt")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(8)))  # исключение в потоке всплывёт здесь

    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Blob)) == 1
        assert db.scalar(select(func.count()).select_from(File)) == 8