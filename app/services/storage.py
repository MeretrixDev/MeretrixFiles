import secrets
import time
import hmac
import hashlib

from datetime import timedelta
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.config import Settings
from app.models import Blob, File, utcnow
from app.services.compression import store_stream
from app.services.blobstore import BlobStore, blob_key


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def verify_token(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), expected_hash)

def new_tmp_path(settings: Settings) -> Path:
    return settings.temp_dir / secrets.token_hex(16)


def lock_blob(db: Session, sha256: str) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"blob:{sha256}"},
    )


def storage_upload(
        db: Session,
        settings: Settings,
        store: BlobStore,
        chunks: Iterable[bytes],
        *,
        filename: str,
        content_type: str | None = None,
        ttl: timedelta | None = None,
        owner_id: int | None = None,
) -> tuple[File, str]:
    tmp = new_tmp_path(settings)
    try:
        result = store_stream(
            chunks,
            tmp,
            filename=filename,
            max_size=settings.max_upload_size,
            level=settings.compress_level,
            frame_size=settings.frame_size
        )

        lock_blob(db, result.sha256)
        blob = db.scalar(select(Blob).where(Blob.sha256 == result.sha256))
        if blob is None:
            store.put(blob_key(result.sha256, result.algo), tmp)
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

        token = secrets.token_urlsafe(24)
        file = File(
            public_id=secrets.token_urlsafe(8),
            filename=Path(filename).name.replace("\x00", "")[:255] or "file",
            content_type=(content_type or "application/octet-stream")[:127],
            blob_id=blob.id,
            delete_token_hash=hash_token(token),
            owner_id=owner_id,
            expires_at=utcnow() + ttl if ttl else None,
        )
        db.add(file)
        db.commit()
        return file, token
    finally:
        tmp.unlink(missing_ok=True)


def delete_file(db: Session, store: BlobStore, file: File) -> None:
    blob = file.blob
    sha, algorithm = blob.sha256, blob.algorithm
    key = blob_key(sha, algorithm)

    lock_blob(db, sha)
    db.delete(file)
    db.flush()
    remaining = db.scalar(
        select(func.count()).select_from(File).where(File.blob_id == blob.id)
    )
    if remaining == 0:
        db.delete(blob)
    db.commit()

    if remaining:
        return

    lock_blob(db, sha)
    try:
        recreated = db.scalar(
            select(Blob.id).where(Blob.sha256 == sha, Blob.algorithm == algorithm)
        )
        if recreated is None:
            store.delete(key)
    finally:
        db.rollback()


def cleanup_tmp(settings: Settings, max_age_seconds: int = 3600) -> int:
    removed = 0
    limit = time.time() - max_age_seconds
    for p in settings.temp_dir.iterdir():
        if p.is_file() and p.stat().st_mtime < limit:
            p.unlink(missing_ok=True)
            removed += 1
    return removed


def purge_expired(db: Session, store: BlobStore) -> int:
    expired = db.scalars(
        select(File)
        .options(joinedload(File.blob))
        .where(File.expires_at.is_not(None), File.expires_at < utcnow())
    ).all()
    removed = 0
    for file in expired:
        try:
            delete_file(db, store, file)
            removed += 1
        except Exception:
            db.rollback()
            #log.exception(f"Failed to purge expired file: {file}")
    return removed


