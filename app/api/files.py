from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, Header, Response, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.db import get_db
from app.models import File, User, utcnow, Visibility
from app.services.blobstore import BlobStore, blob_key
from app.services.compression import BlobReader, FileTooLargeError
from app.services.ranges import RangeNotSatisfiable, parse_range
from app.services.seekable import CorruptBlobError
from app.services.storage import delete_file, storage_upload, verify_token
from app.api.deps import get_blob_store, get_current_user, get_optional_user, limit_upload

router = APIRouter()
public_router = APIRouter()

MAX_TTL_HOURS = 24 * 365


class FileInfo(BaseModel):
    public_id: str
    url: str
    filename: str
    content_type: str
    original_size: int
    stored_size: int
    algorithm: str
    saved_perc: float
    expires_at: datetime | None
    visibility: str


class UploadResult(FileInfo):
    delete_token: str


class VisibilityUpdate(BaseModel):
    visibility: Visibility


def _to_info(file: File, settings: Settings) -> FileInfo:
    blob = file.blob
    saved = (1-blob.stored_size/blob.original_size)*100 if blob.original_size else 0.0
    return FileInfo(
        public_id=file.public_id,
        url=f"{settings.base_url}/f/{file.public_id}",
        filename=file.filename,
        content_type=file.content_type,
        original_size=blob.original_size,
        stored_size=blob.stored_size,
        algorithm=blob.algorithm,
        saved_perc=round(saved, 1),
        expires_at=file.expires_at,
        visibility=file.visibility,
    )


def _get_active_file(db: Session, public_id: str, user: User | None = None) -> File:
    file = db.scalar(
        select(File).options(
            joinedload(File.blob)
        ).where(File.public_id==public_id)
    )
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")
    if file.visibility == "private" and (user is None or file.owner_id != user.id):
        raise HTTPException(404, "File not found")

    if file.expires_at is not None:
        expires = file.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="File expired")
    return file


@router.post("/upload", response_model=UploadResult, status_code=201, dependencies=[Depends(limit_upload)])
def upload_file(
        file: UploadFile,
        ttl_hours: int | None = Form(None, ge=1, le=MAX_TTL_HOURS),
        visibility: Visibility = Form("unlisted"),
        user: User | None = Depends(get_optional_user),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
        store: BlobStore = Depends(get_blob_store),
):
    if visibility == "private" and user is None:
        raise HTTPException(
            status_code=401, detail="Private files require authentication",
            headers={"WWW-Authenticate": "Bearer"},
        )
    chunks = iter(lambda: file.file.read(settings.chunk_size), b"")
    try:
        saved, token = storage_upload(
            db=db,
            settings=settings,
            store=store,
            chunks=chunks,
            filename=file.filename or "file",
            content_type=file.content_type,
            ttl=timedelta(hours=ttl_hours) if ttl_hours else None,
            owner_id=user.id if user else None,
            visibility=visibility,
        )
    except FileTooLargeError:
        raise HTTPException(status_code=413, detail="File too large")
    info = _to_info(saved, settings)
    return UploadResult(**info.model_dump(), delete_token=token)


@public_router.get("/f/{public_id}")
def download_file(
    public_id: str,
    range_header: str | None = Header(None, alias="Range"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    store: BlobStore = Depends(get_blob_store),
    user: User | None = Depends(get_optional_user),
):
    file = _get_active_file(db, public_id, user)
    blob = file.blob
    key = blob_key(blob.sha256, blob.algorithm)
    if not store.exists(key):
        raise HTTPException(500, "Stored file is missing")
    try:
        reader = BlobReader(store, key, blob.algorithm, settings.chunk_size)
    except CorruptBlobError:
        raise HTTPException(500, "Stored file is corrupted")
    size = blob.original_size
    if reader.size != size:  # размер в БД и в самом файле должны совпадать
        raise HTTPException(500, "Stored file is corrupted")

    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.filename)}",
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
        "ETag": f'"{blob.sha256}"',  # хэш содержимого
    }

    if file.visibility == "private":
        headers["Cache-Control"] = "private, no-store"

    try:
        byte_range = parse_range(range_header, size)
    except RangeNotSatisfiable:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

    if byte_range is None:
        status, start, end = 200, 0, size - 1
    else:
        status = 206
        start, end = byte_range
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1)

    return StreamingResponse(
        reader.iter_range(start, end),
        status_code=status,
        media_type=file.content_type,
        headers=headers,
    )


@router.delete("/f/{public_id}", status_code=204)
def delete_uploaded_file(
    public_id: str,
    db: Session = Depends(get_db),
    x_delete_token: str | None = Header(None),
    user: User | None = Depends(get_optional_user),
    store: BlobStore = Depends(get_blob_store),
):
    file = db.scalar(
        select(File).options(joinedload(File.blob)).where(File.public_id==public_id)
    )
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    is_owner = user is not None and file.owner_id == user.id
    token_ok = x_delete_token is not None and verify_token(
        x_delete_token, file.delete_token_hash
    )

    if not (is_owner or token_ok):
        if file.visibility == "private":
            raise HTTPException(status_code=404, detail="File not found")
        raise HTTPException(status_code=403, detail="Not allowed to delete this file")

    delete_file(db, store, file)
    return Response(status_code=204)


@router.get("/users/me/files", response_model=list[FileInfo])
def my_files(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        user: User | None = Depends(get_current_user),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
):
    rows = db.scalars(
        select(File)
        .options(joinedload(File.blob))
        .where(
            File.owner_id == user.id,
            or_(
                File.expires_at.is_(None),
                File.expires_at > utcnow()
            ),
        )
        .order_by(File.created_at.desc(), File.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [_to_info(file, settings) for file in rows]


@router.patch("/f/{public_id}", response_model=FileInfo)
def update_visibility(
        public_id: str,
        body: VisibilityUpdate,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
):
    file = _get_active_file(db, public_id, user)
    if file.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not allowed to update this file")
    file.visibility = body.visibility
    db.commit()
    return _to_info(file, settings)

