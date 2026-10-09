import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="filehost-tests-"))
os.environ["MRTRXFILES_STORAGE_DIR"] = str(_TMP / "storage")
os.environ["MRTRXFILES_DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["MRTRXFILES_MAX_UPLOAD_SIZE"] = str(1024 * 1024)  # 1 МБ, чтобы тест лимита был быстрым
os.environ["MRTRXFILES_BASE_URL"] = "http://testserver"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    settings = get_settings()
    assert _TMP in settings.storage_dir.resolve().parents, (
        f"Тесты используют реальное хранилище: {settings.storage_dir}"
    )
    assert str(_TMP) in settings.database_url, (
        f"Тесты используют реальную БД: {settings.database_url}"
    )
    shutil.rmtree(settings.storage_dir, ignore_errors=True)
    settings.ensure_dirs()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    return TestClient(app)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)