import random
from datetime import timedelta

from sqlalchemy import select

from app.models import File, utcnow
from app.services.stats import StatsCache, stats_cache


def upload(client, data, name="a.txt", headers=None, **form):
    resp = client.post(
        "/api/v1/upload",
        files={"file": (name, data, "text/plain")},
        data=form,
        headers=headers,
    )
    assert resp.status_code == 201
    return resp.json()


def stats(client) -> dict:
    resp = client.get("/api/v1/stats")
    assert resp.status_code == 200
    return resp.json()


def test_empty_instance_has_zeros_and_no_division_error(client):
    s = stats(client)

    assert s["files"] == 0
    assert s["uploaded_bytes"] == s["unique_bytes"] == s["stored_bytes"] == 0
    assert s["saved_perc"] == 0


def test_dedup_and_compression_are_counted_separately(client):
    data = b"a" * 100_000
    upload(client, data, "a.txt")
    upload(client, data, "b.txt")  # то же содержимое: один блоб

    s = stats(client)

    assert s["files"] == 2
    assert s["uploaded_bytes"] == 200_000
    assert s["unique_bytes"] == 100_000
    assert s["dedup_saved_bytes"] == 100_000
    assert s["stored_bytes"] < 5_000
    assert s["compression_saved_bytes"] == 100_000 - s["stored_bytes"]
    assert s["saved_bytes"] == 200_000 - s["stored_bytes"]
    assert s["saved_perc"] > 97


def test_incompressible_file_saves_nothing(client):
    upload(client, random.Random(3).randbytes(50_000), "photo.jpg")

    s = stats(client)

    assert s["uploaded_bytes"] == s["stored_bytes"] == 50_000
    assert s["saved_bytes"] == 0
    assert s["saved_perc"] == 0


def test_expired_files_are_not_counted(client, db):
    pid = upload(client, b"x" * 1000, ttl_hours="1")["public_id"]
    file = db.scalar(select(File).where(File.public_id == pid))
    file.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    s = stats(client)

    assert s["files"] == 0
    assert s["unique_bytes"] == 0


def test_blob_shared_with_an_expired_file_still_counts_once(client, db):
    data = b"y" * 1000
    pid = upload(client, data, "old.txt")["public_id"]
    upload(client, data, "new.txt")
    old = db.scalar(select(File).where(File.public_id == pid))
    old.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    s = stats(client)

    assert s["files"] == 1
    assert s["uploaded_bytes"] == s["unique_bytes"] == 1000
    assert s["dedup_saved_bytes"] == 0


def test_users_are_counted_and_inactive_ones_are_not(client, make_user):
    make_user("a@example.com")
    make_user("b@example.com")

    assert stats(client)["users"] == 2


def test_private_files_are_in_totals_but_endpoint_is_public(client, make_user):
    alice = make_user()
    upload(client, b"s" * 1000, "secret.txt", headers=alice, visibility="private")

    s = stats(client)  # без токена

    assert s["files"] == 1
    assert "secret" not in str(s)


def test_response_is_cacheable(client):
    resp = client.get("/api/v1/stats")

    assert resp.headers["cache-control"] == "public, max-age=60"


def test_endpoint_serves_cached_numbers_until_reset(client):
    upload(client, b"a" * 1000, "a.txt")
    first = stats(client)
    upload(client, b"b" * 1000, "b.txt")

    assert stats(client)["files"] == first["files"]  # ещё из кэша

    stats_cache.reset()
    assert stats(client)["files"] == first["files"] + 1


def test_cache_class_respects_ttl(client, db):
    cache = StatsCache()

    assert cache.get(db, 60, now=0).files == 0
    upload(client, b"x" * 100)
    assert cache.get(db, 60, now=30).files == 0   # срок не вышел
    assert cache.get(db, 60, now=61).files == 1   # пересчитано
    upload(client, b"z" * 100, "z.txt")
    assert cache.get(db, 0, now=62).files == 2    # ttl=0: кэш выключен

