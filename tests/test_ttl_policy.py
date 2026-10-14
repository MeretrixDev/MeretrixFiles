from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models import File, User
from app.services.policy import TtlNotAllowed, resolve_ttl_hours


def upload(client, headers=None, **form):
    return client.post(
        "/api/v1/upload",
        files={"file": ("a.txt", b"data" * 100, "text/plain")},
        data=form,
        headers=headers,
    )


def hours_until(expires_at: str) -> float:
    dt = datetime.fromisoformat(expires_at)
    if dt.tzinfo is None:  # SQLite возвращает время без пояса
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - datetime.now(timezone.utc)).total_seconds() / 3600


def test_anonymous_without_ttl_gets_the_24h_limit(client):
    resp = upload(client)

    assert resp.status_code == 201
    assert 23.9 < hours_until(resp.json()["expires_at"]) <= 24


def test_anonymous_can_ask_for_a_shorter_ttl(client):
    resp = upload(client, ttl_hours="6")

    assert 5.9 < hours_until(resp.json()["expires_at"]) <= 6


def test_anonymous_exactly_at_the_limit_is_allowed(client):
    assert upload(client, ttl_hours="24").status_code == 201


def test_anonymous_over_the_limit_is_rejected_and_nothing_is_stored(client, db, settings):
    resp = upload(client, ttl_hours="25")

    assert resp.status_code == 422
    assert "24" in resp.json()["detail"]
    assert db.scalar(select(func.count()).select_from(File)) == 0
    assert [p for p in settings.storage_dir.rglob("*") if p.is_file()] == []


def test_registered_user_without_ttl_stores_forever(client, make_user):
    resp = upload(client, make_user())

    assert resp.status_code == 201
    assert resp.json()["expires_at"] is None


def test_registered_user_can_exceed_the_anonymous_limit(client, make_user):
    resp = upload(client, make_user(), ttl_hours="48")

    assert 47.9 < hours_until(resp.json()["expires_at"]) <= 48


def test_policy_function_respects_configured_limit(settings):
    s = settings.model_copy(update={"anon_ttl_hours": 12})
    user = User(email="u@example.com", password_hash="x")

    assert resolve_ttl_hours(None, None, s) == 12
    assert resolve_ttl_hours(None, 5, s) == 5
    assert resolve_ttl_hours(None, 12, s) == 12
    with pytest.raises(TtlNotAllowed):
        resolve_ttl_hours(None, 13, s)
    assert resolve_ttl_hours(user, None, s) is None
    assert resolve_ttl_hours(user, 100, s) == 100

