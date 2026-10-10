from datetime import timedelta

from sqlalchemy import select

from app.models import File, User, utcnow


def upload(client, data=b"data" * 100, name="note.txt", headers=None, **form):
    return client.post(
        "/upload", files={"file": (name, data, "text/plain")}, data=form, headers=headers
    )


def test_anonymous_upload_has_no_owner(client, db):
    upload(client)

    assert db.scalar(select(File)).owner_id is None


def test_authenticated_upload_belongs_to_user(client, db, make_user):
    headers = make_user()

    resp = upload(client, headers=headers)

    assert resp.status_code == 201
    assert db.scalar(select(File)).owner_id == db.scalar(select(User)).id


def test_invalid_token_on_upload_is_rejected_not_treated_as_anonymous(client):
    resp = upload(client, headers={"Authorization": "Bearer garbage"})

    assert resp.status_code == 401


def test_my_files_lists_only_own_files(client, make_user):
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    upload(client, b"a" * 100, name="alice.txt", headers=alice)
    upload(client, b"b" * 100, name="bob.txt", headers=bob)
    upload(client, b"c" * 100, name="anon.txt")

    names = [f["filename"] for f in client.get("/users/me/files", headers=alice).json()]

    assert names == ["alice.txt"]


def test_my_files_requires_auth(client):
    assert client.get("/users/me/files").status_code == 401


def test_my_files_hides_expired_and_supports_paging(client, db, make_user):
    headers = make_user()
    for i in range(3):
        upload(client, bytes([65 + i]) * 100, name=f"f{i}.txt", headers=headers)
    old = db.scalar(select(File).where(File.filename == "f0.txt"))
    old.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    everything = client.get("/users/me/files", headers=headers).json()
    first_page = client.get("/users/me/files?limit=1", headers=headers).json()

    assert {f["filename"] for f in everything} == {"f1.txt", "f2.txt"}
    assert len(first_page) == 1


def test_owner_can_delete_without_token(client, make_user):
    headers = make_user()
    info = upload(client, headers=headers).json()

    resp = client.delete(f"/f/{info['public_id']}", headers=headers)

    assert resp.status_code == 204
    assert client.get(f"/f/{info['public_id']}").status_code == 404


def test_other_user_cannot_delete_without_token(client, make_user):
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    info = upload(client, headers=alice).json()

    resp = client.delete(f"/f/{info['public_id']}", headers=bob)

    assert resp.status_code == 403
    assert client.get(f"/f/{info['public_id']}").status_code == 200


def test_delete_token_still_works_for_owned_file(client, make_user):
    info = upload(client, headers=make_user()).json()

    resp = client.delete(
        f"/f/{info['public_id']}", headers={"X-Delete-Token": info["delete_token"]}
    )

    assert resp.status_code == 204

