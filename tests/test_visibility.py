from datetime import timedelta

from sqlalchemy import select

from app.models import File, utcnow

DATA = b"secret data " * 100


def upload(client, headers=None, name="note.txt", **form):
    return client.post(
        "/api/v1/upload",
        files={"file": (name, DATA, "text/plain")},
        data=form,
        headers=headers,
    )


def patch(client, public_id, visibility, headers=None):
    return client.patch(
        f"/api/v1/f/{public_id}", json={"visibility": visibility}, headers=headers
    )


def test_default_visibility_is_unlisted_and_open_by_link(client):
    info = upload(client).json()

    assert info["visibility"] == "unlisted"
    assert client.get(f"/f/{info['public_id']}").content == DATA


def test_anonymous_cannot_upload_private(client):
    assert upload(client, visibility="private").status_code == 401


def test_invalid_visibility_is_rejected(client, make_user):
    assert upload(client, make_user(), visibility="public").status_code == 422


def test_private_file_is_hidden_from_everyone_but_owner(client, make_user):
    alice, bob = make_user("alice@example.com"), make_user("bob@example.com")
    pid = upload(client, alice, visibility="private").json()["public_id"]

    assert client.get(f"/f/{pid}").status_code == 404
    assert client.get(f"/f/{pid}", headers=bob).status_code == 404
    assert client.get(f"/api/v1/f/{pid}/info").status_code == 404
    assert client.get(f"/api/v1/f/{pid}/info", headers=bob).status_code == 404

    resp = client.get(f"/f/{pid}", headers=alice)
    assert resp.status_code == 200
    assert resp.content == DATA
    assert "no-store" in resp.headers["cache-control"]


def test_private_file_hidden_response_equals_missing_file_response(client, make_user):
    pid = upload(client, make_user(), visibility="private").json()["public_id"]

    hidden = client.get(f"/f/{pid}")
    missing = client.get("/f/doesnotexist")

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()


def test_range_requests_follow_the_same_rules(client, make_user):
    alice = make_user()
    pid = upload(client, alice, visibility="private").json()["public_id"]

    anon = client.get(f"/f/{pid}", headers={"Range": "bytes=0-9"})
    owner = client.get(f"/f/{pid}", headers={**alice, "Range": "bytes=0-9"})

    assert anon.status_code == 404
    assert owner.status_code == 206
    assert owner.content == DATA[:10]


def test_expired_private_file_looks_missing_to_strangers_but_gone_to_owner(
    client, db, make_user
):
    alice = make_user()
    pid = upload(client, alice, visibility="private", ttl_hours="1").json()["public_id"]
    file = db.scalar(select(File).where(File.public_id == pid))
    file.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    assert client.get(f"/f/{pid}").status_code == 404
    assert client.get(f"/f/{pid}", headers=alice).status_code == 410


def test_owner_can_switch_visibility_back_and_forth(client, make_user):
    alice = make_user()
    pid = upload(client, alice).json()["public_id"]

    resp = patch(client, pid, "private", alice)
    assert resp.status_code == 200
    assert resp.json()["visibility"] == "private"
    assert client.get(f"/f/{pid}").status_code == 404

    patch(client, pid, "unlisted", alice)
    assert client.get(f"/f/{pid}").status_code == 200


def test_patch_requires_login(client, make_user):
    pid = upload(client, make_user()).json()["public_id"]

    assert patch(client, pid, "private").status_code == 401


def test_non_owner_cannot_change_visibility(client, make_user):
    alice, bob = make_user("alice@example.com"), make_user("bob@example.com")
    pid = upload(client, alice).json()["public_id"]

    assert patch(client, pid, "private", bob).status_code == 403
    assert client.get(f"/f/{pid}").status_code == 200  # осталось как было


def test_patch_on_strangers_private_file_looks_like_missing(client, make_user):
    alice, bob = make_user("alice@example.com"), make_user("bob@example.com")
    pid = upload(client, alice, visibility="private").json()["public_id"]

    assert patch(client, pid, "unlisted", bob).status_code == 404


def test_anonymous_upload_cannot_be_made_private(client, make_user):
    pid = upload(client).json()["public_id"]

    assert patch(client, pid, "private", make_user()).status_code == 403


def test_my_files_shows_visibility(client, make_user):
    alice = make_user()
    upload(client, alice, visibility="private")

    files = client.get("/api/v1/users/me/files", headers=alice).json()

    assert [f["visibility"] for f in files] == ["private"]


def test_delete_token_on_private_file_works_and_wrong_token_looks_like_missing(
    client, make_user
):
    info = upload(client, make_user(), visibility="private").json()
    url = f"/api/v1/f/{info['public_id']}"

    assert client.delete(url, headers={"X-Delete-Token": "wrong"}).status_code == 404
    assert client.delete(
        url, headers={"X-Delete-Token": info["delete_token"]}
    ).status_code == 204