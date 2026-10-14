from datetime import timedelta

from sqlalchemy import func, select

from app.models import File, utcnow


def upload(client, size, headers=None, fill=b"a", name="f.txt", **form):
    return client.post(
        "/api/v1/upload",
        files={"file": (name, fill * size, "text/plain")},
        data=form,
        headers=headers,
    )


def usage(client, headers):
    resp = client.get("/api/v1/users/me/usage", headers=headers)
    assert resp.status_code == 200
    return resp.json()


def test_usage_requires_login(client):
    assert client.get("/api/v1/users/me/usage").status_code == 401


def test_usage_starts_empty(client, make_user, settings):
    body = usage(client, make_user())

    assert body["files"] == 0
    assert body["used_bytes"] == 0
    assert body["quota_bytes"] == settings.user_quota_bytes
    assert body["remaining_bytes"] == settings.user_quota_bytes


def test_usage_counts_original_size_of_own_files_only(client, make_user):
    alice, bob = make_user("alice@example.com"), make_user("bob@example.com")
    upload(client, 100_000, alice, fill=b"a")
    upload(client, 50_000, alice, fill=b"b")
    upload(client, 70_000, bob, fill=b"c")

    body = usage(client, alice)

    assert body["files"] == 2
    assert body["used_bytes"] == 150_000
    assert body["stored_bytes"] < body["used_bytes"]
    assert body["saved_perc"] > 90


def test_quota_counts_duplicates_too(client, make_user, override_settings):
    override_settings(user_quota_bytes=200_000)
    alice = make_user()

    assert upload(client, 100_000, alice).status_code == 201
    assert upload(client, 100_000, alice).status_code == 201  # на диске один файл, в квоте два
    assert upload(client, 1, alice, fill=b"z").status_code == 413


def test_exceeding_quota_returns_413_and_stores_nothing(
    client, db, settings, make_user, override_settings
):
    override_settings(user_quota_bytes=150_000)
    alice = make_user()
    assert upload(client, 100_000, alice, fill=b"a").status_code == 201

    resp = upload(client, 100_000, alice, fill=b"b")

    assert resp.status_code == 413
    assert "quota" in resp.json()["detail"].lower()
    assert db.scalar(select(func.count()).select_from(File)) == 1
    on_disk = [p for p in settings.storage_dir.rglob("*") if p.is_file()]
    assert len(on_disk) == 1  # ни второго блоба, ни мусора в tmp


def test_size_limit_is_not_reported_as_quota(client, make_user, settings):
    resp = upload(client, settings.max_upload_size + 1, make_user())

    assert resp.status_code == 413
    assert "quota" not in resp.json()["detail"].lower()


def test_deleting_a_file_frees_quota(client, make_user, override_settings):
    override_settings(user_quota_bytes=150_000)
    alice = make_user()
    first = upload(client, 100_000, alice, fill=b"a").json()
    assert upload(client, 100_000, alice, fill=b"b").status_code == 413

    assert client.delete(f"/api/v1/f/{first['public_id']}", headers=alice).status_code == 204

    assert upload(client, 100_000, alice, fill=b"b").status_code == 201


def test_expired_files_do_not_count_towards_quota(client, db, make_user):
    alice = make_user()
    pid = upload(client, 100_000, alice, ttl_hours="1").json()["public_id"]
    file = db.scalar(select(File).where(File.public_id == pid))
    file.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    body = usage(client, alice)

    assert body["files"] == 0
    assert body["used_bytes"] == 0


def test_quota_is_per_user(client, make_user, override_settings):
    override_settings(user_quota_bytes=100_000)
    alice, bob = make_user("alice@example.com"), make_user("bob@example.com")
    assert upload(client, 100_000, alice, fill=b"a").status_code == 201
    assert upload(client, 1, alice, fill=b"b").status_code == 413

    assert upload(client, 100_000, bob, fill=b"c").status_code == 201


def test_anonymous_size_cap_has_a_sign_in_hint(client, override_settings):
    override_settings(anon_max_upload_size=100_000)

    ok = upload(client, 100_000)
    too_big = upload(client, 100_001, fill=b"b")

    assert ok.status_code == 201
    assert too_big.status_code == 413
    assert "sign in" in too_big.json()["detail"].lower()


def test_registered_user_is_not_limited_by_the_anonymous_cap(
    client, make_user, override_settings
):
    override_settings(anon_max_upload_size=100_000)

    assert upload(client, 200_000, make_user()).status_code == 201


def test_archive_counts_towards_quota_by_its_total_size(
    client, make_user, override_settings
):
    override_settings(user_quota_bytes=150_000)
    alice = make_user()

    def two(n):
        return [("file", (f"{i}.txt", bytes([97 + i]) * n, "text/plain")) for i in range(2)]

    too_big = client.post("/api/v1/upload", files=two(100_000), headers=alice)
    fits = client.post("/api/v1/upload", files=two(50_000), headers=alice)

    assert too_big.status_code == 413
    assert fits.status_code == 201
    assert 100_000 <= usage(client, alice)["used_bytes"] < 101_000  # данные плюс служебные байты ZIP

