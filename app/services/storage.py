import os
import secrets
import time
from datetime import timedelta
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.config import Settings
from app.models import Blob, File, utcnow
from app.services.compression import store_stream


def new_tmp_path(settings: Settings) -> Path:
    return settings.temp_dir / secrets.token_hex(16)

def blob_path(settings: Settings, sha256: str, algo: str) -> Path:
    suffix = "zstd" if algo == "zstd" else "raw"
    return settings.storage_dir / sha256[:2] / f"{sha256}.{suffix}"

def _commit_file(settings: Settings, tmp: Path, sha256: str, algo: str) -> None:
    final = blob_path(settings, sha256, algo)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, final)

def storage_upload(
        db: Session,
        settings: Settings,
        chunks: Iterable[bytes],
        *,
        filename: str,
        content_type: str | None = None,
        ttl: timedelta | None = None
) -> File:
    tmp = new_tmp_path(settings)
    try:
        result = store_stream(
            chunks,
            tmp,
            filename=filename,
            max_size=settings.max_upload_size,
            level=settings.compress_level
        )

        blob = db.scalar(select(Blob).where(Blob.sha256 == result.sha256))
        if blob is None:
            _commit_file(settings, tmp, result.sha256, result.algo)
            blob = Blob(
                sha256=result.sha256,
                original_size=result.orig_size,
                stored_size=result.stored_size,
                algorithm=result.algo,
            )
            db.add(blob)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                blob = db.scalar(select(Blob).where(Blob.sha256 == result.sha256))

        file = File(
            public_id=secrets.token_urlsafe(8),
            filename=Path(filename).name[:255] or "file",
            content_type=content_type or "application/octet-stream",
            blob_id=blob.id,
            expires_at=utcnow() + ttl if ttl else None,
        )
        db.add(file)
        db.commit()
        return file
    finally:
        tmp.unlink(missing_ok=True)


def delete_file(db: Session, settings: Settings, file: File) -> None:
    blob = file.blob
    path = blob_path(settings, blob.sha256, blob.algorithm)

    db.delete(file)
    db.flush()

    remaining = db.scalar(
        select(func.count()).select_from(File).where(File.blob_id == blob.id)
    )

    if remaining == 0:
        db.delete(blob)
    db.commit()

    if remaining == 0:
        path.unlink(missing_ok=True)


def cleanup_tmp(settings: Settings, max_age_seconds: int = 3600) -> int:
    removed = 0
    limit = time.time() - max_age_seconds
    for p in settings.temp_dir.iterdir():
        if p.is_file() and p.stat().st_mtime < limit:
            p.unlink(missing_ok=True)
            removed += 1
    return removed


def purge_expired(db: Session, settings: Settings) -> int:
    expired = db.scalars(
        select(File)
        .options(joinedload(File.blob))
        .where(File.expires_at.is_not(None), File.expires_at < utcnow())
    ).all()
    for file in expired:
        delete_file(db, settings, file)
    return len(expired)