import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="filehost-tests-"))
_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
os.environ["MRTRXFILES_STORAGE_DIR"] = str(_TMP / "storage")
os.environ["MRTRXFILES_DATABASE_URL"] = _TEST_DB_URL or f"sqlite:///{_TMP / 'test.db'}"
os.environ["MRTRXFILES_MAX_UPLOAD_SIZE"] = str(1024 * 1024)  # 1 МБ, чтобы тест лимита был быстрым
os.environ["MRTRXFILES_BASE_URL"] = "http://testserver"
os.environ["MRTRXFILES_SECRET_KEY"] = "test-secret-key-for-pytest-only-0123456789"
os.environ["MRTRXFILES_FRAME_SIZE"] = str(64 * 1024)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.ratelimit import limiter  # noqa: E402
from app.services.blobstore import make_blob_storage

from sqlalchemy import text


@pytest.fixture(autouse=True)
def clean_state():
    settings = get_settings()
    url = settings.database_url

    assert _TMP in settings.storage_dir.resolve().parents, (
        f"Тесты используют реальное хранилище: {settings.storage_dir}"
    )
    assert str(_TMP) in url or url.rsplit("/", 1)[-1].endswith("_test"), (
        f"Тесты используют реальную БД: {settings.database_url}"
    )

    shutil.rmtree(settings.storage_dir, ignore_errors=True)
    settings.ensure_dirs()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    limiter.reset()
    yield


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def override_settings(settings):
    def _apply(**overrides):
        app.dependency_overrides[get_settings] = lambda: settings.model_copy(update=overrides)

    yield _apply
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def make_user(client):
    def _make(email="user@example.com", password="correct-horse-battery"):
        client.post("/api/v1/auth/register", json={"email": email, "password": password})
        resp = client.post("/api/v1/auth/login", data={"username": email, "password": password})
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    return _make


@pytest.fixture
def store(settings):
    return make_blob_storage(settings)


@pytest.fixture(scope="session", autouse=True)
def _drop_stale_pg_sessions():
    if engine.dialect.name != "postgresql":
        return
    with engine.connect() as conn:
        name = conn.execute(text("SELECT current_database()")).scalar()
        assert name.endswith("_test"), f"refusing to touch database {name}"
        conn.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        ))
        conn.commit()


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)