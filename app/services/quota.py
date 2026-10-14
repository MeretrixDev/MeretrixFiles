from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Blob, File, utcnow


@dataclass(frozen=True)
class Usage:
    files: int
    used_bytes: int # исходный размер по квоте
    stored_bytes: int # размер на диске без учёта дедупликации


def get_usage(db: Session, user_id: int) -> Usage:
    files, used, stored = db.execute(
        select(
            func.count(File.id),
            func.coalesce(func.sum(Blob.original_size), 0),
            func.coalesce(func.sum(Blob.stored_size), 0),
        )
        .select_from(File)
        .join(Blob, File.blob_id == Blob.id)
        .where(
            File.owner_id == user_id,
            or_(File.expires_at.is_(None), File.expires_at > utcnow()),
        )
    ).one()
    return Usage(files=int(files), used_bytes=int(used), stored_bytes=int(stored))

