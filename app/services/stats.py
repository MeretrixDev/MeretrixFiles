import threading
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Blob, File, User, utcnow


@dataclass(frozen=True)
class Stats:
    files: int
    users: int
    uploaded_bytes: int  # исходный размер всех активных файлов
    unique_bytes: int  # исходный размер уникальных блобов
    stored_bytes: int  # размер уникальных блобов на диске
    generated_at: datetime


def compute_stats(db: Session) -> Stats:
    active = or_(File.expires_at.is_(None), File.expires_at > utcnow())

    files, uploaded = db.execute(
        select(func.count(File.id), func.coalesce(func.sum(Blob.original_size), 0))
        .select_from(File)
        .join(Blob, File.blob_id == Blob.id)
        .where(active)
    ).one()

    live_blobs = select(File.blob_id).where(active)
    unique, stored = db.execute(
        select(
            func.coalesce(func.sum(Blob.original_size), 0),
            func.coalesce(func.sum(Blob.stored_size), 0),
        ).where(Blob.id.in_(live_blobs))
    ).one()

    users = db.scalar(select(func.count(User.id)).where(User.is_active.is_(True)))

    return Stats(
        files=int(files),
        users=int(users or 0),
        uploaded_bytes=int(uploaded),
        unique_bytes=int(unique),
        stored_bytes=int(stored),
        generated_at=utcnow(),
    )


class StatsCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: Stats | None = None
        self._at = 0.0

    def get(self, db: Session, ttl: int, now: float | None = None) -> Stats:
        now = time.monotonic() if now is None else now
        with self._lock:
            if ttl > 0 and self._value is not None and now - self._at < ttl:
                return self._value
            self._value = compute_stats(db)
            self._at = now
            return self._value

    def reset(self) -> None:
        with self._lock:
            self._value = None


stats_cache = StatsCache()

