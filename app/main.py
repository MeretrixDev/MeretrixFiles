import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app import models  # noqa: F401 (импорт нужен, чтобы create_all увидел таблицы)
from app.api import files, auth
from app.config import Settings, get_settings
from app.db import Base, SessionLocal, engine
from app.services.storage import cleanup_tmp, purge_expired

log = logging.getLogger("filehost")

CLEANUP_INTERVAL_SECONDS = 600

def _cleanup_once(settings: Settings) -> None:
    removed_tmp = cleanup_tmp(settings)
    with SessionLocal() as db:
        removed_files = purge_expired(db, settings)
    if removed_tmp or removed_files:
        log.info(f"Cleanup: tmp={removed_tmp}, files={removed_files}")


async def _cleanup_loop(settings: Settings) -> None:
    while True:
        try:
            await asyncio.to_thread(_cleanup_once, settings)
        except Exception:
            log.exception(f"Cleanup: loop failed")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    task = asyncio.create_task(_cleanup_loop(settings))

    yield

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(
    title="MeretrixFiles",
    description="Файловый хостинг со сжатием на лету (zstd) и дедупликацией",
    version="0.3.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(files.router)
app.include_router(files.router)

@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}