from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select

from app.models import User


def register(client, email="User@Example.com", password="correct-horse-battery"):
    return client.post("/auth/register", json={"email": email, "password": password})


def login(client, email="user@example.com", password="correct-horse-battery"):
    return client.post("/auth/login", data={"username": email, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def make_token(settings, sub, delta, key=None):
    now = datetime.now(timezone.utc)
    payload = {"sub": str(sub), "iat": now, "exp": now + delta}
    return jwt.encode(payload, key or settings.secret_key, algorithm=settings.jwt_algorithm)


def test_register_creates_user_without_exposing_password(client):
    resp = register(client)

    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "user@example.com"  # приведён к нижнему регистру
    assert "password" not in body and "password_hash" not in body


def test_password_is_stored_hashed(client, db):
    register(client, password="correct-horse-battery")

    user = db.scalar(select(User))

    assert user.password_hash != "correct-horse-battery"
    assert user.password_hash.startswith("$argon2")


def test_duplicate_email_is_rejected_case_insensitively(client):
    assert register(client, email="a@example.com").status_code == 201

    assert register(client, email="A@EXAMPLE.COM").status_code == 409


def test_short_password_is_rejected(client):
    assert register(client, password="short").status_code == 422


def test_invalid_email_is_rejected(client):
    assert register(client, email="not-an-email").status_code == 422


def test_login_returns_token_that_opens_me(client):
    register(client)

    token = login(client).json()["access_token"]
    me = client.get("/users/me", headers=bearer(token))

    assert me.status_code == 200
    assert me.json()["email"] == "user@example.com"


def test_login_is_case_insensitive_for_email(client):
    register(client)

    assert login(client, email="USER@example.COM").status_code == 200


def test_wrong_password_and_unknown_user_look_the_same(client):
    register(client)

    wrong = login(client, password="wrong-password")
    unknown = login(client, email="nobody@example.com")

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_me_requires_token(client):
    assert client.get("/users/me").status_code == 401


def test_garbage_token_is_rejected(client):
    assert client.get("/users/me", headers=bearer("garbage")).status_code == 401


def test_expired_token_is_rejected(client, settings):
    register(client)
    token = make_token(settings, 1, timedelta(minutes=-1))

    assert client.get("/users/me", headers=bearer(token)).status_code == 401


def test_token_signed_with_other_key_is_rejected(client, settings):
    register(client)
    token = make_token(settings, 1, timedelta(minutes=5), key="x" * 40)

    assert client.get("/users/me", headers=bearer(token)).status_code == 401


def test_inactive_user_cannot_login_or_use_old_token(client, db):
    register(client)
    token = login(client).json()["access_token"]
    user = db.scalar(select(User))
    user.is_active = False
    db.commit()

    assert login(client).status_code == 401
    assert client.get("/users/me", headers=bearer(token)).status_code == 401