from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import Settings
from app.db import SessionLocal, engine
from app.services.blobstore import make_blob_storage
from app.services.storage import cleanup_tmp, purge_expired

LOCK_KEY = 0x4D52545258


@dataclass(frozen=True)
class CleanupResult:
    skipped: bool = False
    tmp_removed: int = 0
    files_removed: int = 0


@contextmanager
def single_runner(eng: Engine) -> Iterator[bool]:
    if eng.dialect.name != "postgresql":
        yield True
        return
    with eng.connect() as conn:
        got = bool(
            conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"),
                {"k": LOCK_KEY}
            ).scalar()
        )
        conn.commit()
        try:
            yield got
        finally:
            if got:
                conn.execute(
                    text("SELECT pg_try_advisory_lock(:k)"),
                    {"k": LOCK_KEY}
                )
                conn.commit()


def run_cleanup(settings: Settings) -> CleanupResult:
    store = make_blob_storage(settings)
    with single_runner(engine) as acquired:
        if not acquired:
            return CleanupResult(skipped=True)
        tmp_removed = cleanup_tmp(settings)
        with SessionLocal() as db:
            files_removed = purge_expired(db, store)
    return CleanupResult(tmp_removed=tmp_removed, files_removed=files_removed)

