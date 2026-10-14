import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app import models  # noqa: F401 (импорт нужен, чтобы create_all увидел таблицы)
from app.api import files, auth
from app.config import Settings, get_settings
from app.services.maintenance import run_cleanup
from app.services.ratelimit import limiter

log = logging.getLogger("filehost")

CLEANUP_INTERVAL_SECONDS = 600


def _tick(settings: Settings) -> None:
    if settings.cleanup_in_app:
        result = run_cleanup(settings)
        if result.files_removed or result.tmp_removed:
            log.info("cleanup: expired=%d tmp=%d", result.files_removed, result.tmp_removed)
    limiter.purge()


async def _maintenance_loop(settings: Settings) -> None:
    while True:
        try:
            await asyncio.to_thread(_tick, settings)
        except Exception:
            log.exception("maintenance failed")
        await asyncio.sleep(settings.cleanup_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    task = asyncio.create_task(_maintenance_loop(settings))

    yield

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(
    title="MeretrixFiles",
    description="Файловый хостинг со сжатием на лету (zstd) и дедупликацией",
    version="0.7.0",
    lifespan=lifespan,
)

API_PREFIX = "/api/v1"

app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(files.router, prefix=API_PREFIX)
app.include_router(files.public_router)

@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}