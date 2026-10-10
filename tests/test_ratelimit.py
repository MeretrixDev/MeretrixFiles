import pytest

from app.config import get_settings
from app.main import app
from app.services.ratelimit import RateLimiter


def test_allows_up_to_limit_then_blocks_with_retry_after():
    rl = RateLimiter()
    for _ in range(3):
        assert rl.hit("k", 3, 60, now=0)[0]

    allowed, retry_after = rl.hit("k", 3, 60, now=1)

    assert not allowed
    assert retry_after == 59


def test_window_slides():
    rl = RateLimiter()
    for _ in range(3):
        rl.hit("k", 3, 60, now=0)

    assert not rl.hit("k", 3, 60, now=59)[0]
    assert rl.hit("k", 3, 60, now=61)[0]


def test_blocked_attempts_do_not_extend_the_block():
    rl = RateLimiter()
    for _ in range(3):
        rl.hit("k", 3, 60, now=0)
    for t in range(1, 50):
        rl.hit("k", 3, 60, now=t)

    assert rl.hit("k", 3, 60, now=61)[0]


def test_keys_are_independent():
    rl = RateLimiter()
    rl.hit("a", 1, 60, now=0)

    assert not rl.hit("a", 1, 60, now=1)[0]
    assert rl.hit("b", 1, 60, now=1)[0]


def test_purge_removes_idle_keys():
    rl = RateLimiter()
    rl.hit("old", 5, 60, now=0)
    rl.hit("fresh", 5, 60, now=100)

    rl.purge(now=120)

    assert "old" not in rl._hits
    assert "fresh" in rl._hits


@pytest.fixture
def set_limits(settings):
    def _set(**overrides):
        app.dependency_overrides[get_settings] = lambda: settings.model_copy(
            update=overrides
        )

    yield _set
    app.dependency_overrides.pop(get_settings, None)


def login(client, password="wrong-password"):
    return client.post(
        "/auth/login", data={"username": "user@example.com", "password": password}
    )


def test_login_is_blocked_after_limit_even_with_correct_password(client, set_limits):
    client.post(
        "/auth/register",
        json={"email": "user@example.com", "password": "correct-horse-battery"},
    )
    set_limits(rate_login_per_minute=3)

    for _ in range(3):
        assert login(client).status_code == 401
    blocked = login(client, password="correct-horse-battery")

    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


def test_register_is_limited(client, set_limits):
    set_limits(rate_register_per_hour=2)

    codes = [
        client.post(
            "/auth/register",
            json={"email": f"u{i}@example.com", "password": "correct-horse-battery"},
        ).status_code
        for i in range(3)
    ]

    assert codes == [201, 201, 429]


def test_upload_is_limited(client, set_limits):
    set_limits(rate_upload_per_minute=2)

    codes = [
        client.post("/upload", files={"file": ("a.txt", b"data", "text/plain")}).status_code
        for _ in range(3)
    ]

    assert codes == [201, 201, 429]


def test_limits_are_per_endpoint(client, set_limits):
    set_limits(rate_login_per_minute=1)
    login(client)
    assert login(client).status_code == 429

    resp = client.post("/upload", files={"file": ("a.txt", b"data", "text/plain")})

    assert resp.status_code == 201


def test_limiting_can_be_disabled(client, set_limits):
    set_limits(rate_limit_enabled=False, rate_login_per_minute=1)

    assert all(login(client).status_code == 401 for _ in range(5))


