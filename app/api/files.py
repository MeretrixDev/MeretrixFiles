from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, Header, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.db import get_db
from app.models import File
from app.services.compression import FileTooLargeError, iter_decompressed
from app.services.storage import blob_path, storage_upload, delete_file, verify_token

router = APIRouter()

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



class UploadResult(FileInfo):
    delete_token: str


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
    )


def _get_active_file(db: Session, public_id: str) -> File:
    file = db.scalar(
        select(File).options(
            joinedload(File.blob)
        ).where(File.public_id==public_id)
    )
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file.expires_at is not None:
        expires = file.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="File expired")
    return file


@router.post("/upload", response_model=UploadResult, status_code=201)
def upload_file(
        file: UploadFile,
        ttl_hours: int | None = Form(None, ge=1, le=MAX_TTL_HOURS),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
):
    chunks = iter(lambda: file.file.read(settings.chunk_size), b"")
    try:
        saved, token = storage_upload(
            db,
            settings,
            chunks,
            filename=file.filename or "file",
            content_type=file.content_type,
            ttl=timedelta(hours=ttl_hours) if ttl_hours else None,
        )
    except FileTooLargeError:
        raise HTTPException(status_code=413, detail="File too large")
    info = _to_info(saved, settings)
    return UploadResult(**info.model_dump(), delete_token=token)


@router.get("/f/{public_id}")
def download_file(
    public_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    file = _get_active_file(db, public_id)
    blob = file.blob
    path = blob_path(settings, blob.sha256, blob.algorithm)
    if not path.exists():
        raise HTTPException(status_code=500, detail="File not found")

    headers = {
        # filename*= (RFC 5987) корректно передаёт кириллицу и пробелы
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.filename)}",
        "Content-Length": str(blob.original_size),
        "X-Content-Type-Options": "nosniff",
    }

    return StreamingResponse(
        iter_decompressed(path, blob.algorithm, settings.chunk_size),
        media_type=file.content_type,
        headers=headers,
    )


@router.delete("/f/{public_id}", status_code=204)
def delete_uploaded_file(
    public_id: str,
    db: Session = Depends(get_db),
    x_delete_token: str = Header(...),
    settings: Settings = Depends(get_settings),
):
    file = db.scalar(
        select(File).options(joinedload(File.blob)).where(File.public_id==public_id)
    )
    if file is None:
        raise HTTPException(status_code=404, detail="File not found")
    if not verify_token(x_delete_token, file.delete_token_hash):
        raise HTTPException(status_code=403, detail="Invalid token")

    delete_file(db, settings, file)
    return Response(status_code=204)