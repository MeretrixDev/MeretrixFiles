from datetime import datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.services.stats import stats_cache

router = APIRouter(tags=["stats"])


class StatsOut(BaseModel):
    files: int
    users: int
    uploaded_bytes: int
    unique_bytes: int
    stored_bytes: int
    dedup_saved_bytes: int
    compression_saved_bytes: int
    saved_bytes: int
    saved_perc: float
    generated_at: datetime


@router.get("/stats", response_model=StatsOut)
def public_stats(
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    s = stats_cache.get(db, settings.stats_cache_seconds)
    if settings.stats_cache_seconds > 0:
        response.headers["Cache-Control"] = f"public, max-age={settings.stats_cache_seconds}"

    saved = s.uploaded_bytes - s.stored_bytes
    return StatsOut(
        files=s.files,
        users=s.users,
        uploaded_bytes=s.uploaded_bytes,
        unique_bytes=s.unique_bytes,
        stored_bytes=s.stored_bytes,
        dedup_saved_bytes=s.uploaded_bytes - s.unique_bytes,
        compression_saved_bytes=s.unique_bytes - s.stored_bytes,
        saved_bytes=saved,
        saved_perc=round(saved / s.uploaded_bytes * 100, 1) if s.uploaded_bytes else 0.0,
        generated_at=s.generated_at,
    )

