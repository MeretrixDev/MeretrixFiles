from datetime import timedelta
from urllib.parse import quote

from sqlalchemy import func, select

from app.models import Blob, File, utcnow
from app.services.storage import hash_token


def delete(client, public_id, token):
    return client.delete(f"/f/{public_id}", headers={"X-Delete-Token": token})

def upload(client, data: bytes, name: str = "note.txt", **form):
    return client.post(
        "/upload",
        files={"file": (name, data, "text/plain")},
        data=form,
    )


def test_upload_then_download_returns_identical_bytes(client):
    data = b"hello world " * 5000

    resp = upload(client, data)
    assert resp.status_code == 201
    info = resp.json()
    assert info["original_size"] == len(data)
    assert info["stored_size"] < info["original_size"]
    assert info["algorithm"] == "zstd"

    dl = client.get(f"/f/{info['public_id']}")
    assert dl.status_code == 200
    assert dl.content == data


def test_already_compressed_file_is_stored_raw(client):
    resp = upload(client, b"\xff\xd8\xff" + b"0" * 1000, name="photo.jpg")

    assert resp.status_code == 201
    assert resp.json()["algorithm"] == "none"


def test_oversized_upload_returns_413(client, settings):
    data = b"x" * (settings.max_upload_size + 1)

    resp = upload(client, data)

    assert resp.status_code == 413
    assert list(settings.temp_dir.iterdir()) == []  # мусора не осталось


def test_unknown_id_returns_404(client):
    assert client.get("/f/doesnotexist").status_code == 404


def test_expired_file_returns_410(client, db):
    public_id = upload(client, b"data", ttl_hours="1").json()["public_id"]
    file = db.scalar(select(File).where(File.public_id == public_id))
    file.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    assert client.get(f"/f/{public_id}").status_code == 410


def test_ttl_must_be_positive(client):
    assert upload(client, b"data", ttl_hours="0").status_code == 422


def test_cyrillic_filename_is_encoded_in_header(client):
    public_id = upload(client, b"data", name="отчёт.txt").json()["public_id"]

    header = client.get(f"/f/{public_id}").headers["content-disposition"]

    assert header == f"attachment; filename*=UTF-8''{quote('отчёт.txt')}"


def test_same_content_is_deduplicated(client, db, settings):
    a = upload(client, b"same content" * 100, name="a.txt").json()
    b = upload(client, b"same content" * 100, name="b.txt").json()

    assert a["public_id"] != b["public_id"]
    assert db.scalar(select(func.count()).select_from(File)) == 2
    assert db.scalar(select(func.count()).select_from(Blob)) == 1
    files_on_disk = [p for p in settings.storage_dir.rglob("*") if p.is_file()]
    assert len(files_on_disk) == 1


def test_path_in_filename_is_stripped(client):
    info = upload(client, b"data", name="../../evil.txt").json()

    assert info["filename"] == "evil.txt"


def test_delete_with_valid_token_removes_file(client):
    info = upload(client, b"data" * 100).json()

    resp = delete(client, info["public_id"], info["delete_token"])

    assert resp.status_code == 204
    assert client.get(f"/f/{info['public_id']}").status_code == 404


def test_delete_with_wrong_token_is_forbidden_and_keeps_file(client):
    info = upload(client, b"data" * 100).json()

    resp = delete(client, info["public_id"], "wrong-token")

    assert resp.status_code == 403
    assert client.get(f"/f/{info['public_id']}").status_code == 200


def test_delete_unknown_id_returns_404(client):
    assert delete(client, "doesnotexist", "any").status_code == 404


def test_token_is_not_exposed_in_info_endpoint(client):
    info = upload(client, b"data").json()

    assert "delete_token" not in client.get(f"/f/{info['public_id']}/info").json()


def test_only_hash_of_token_is_stored(client, db):
    info = upload(client, b"data").json()

    file = db.scalar(select(File).where(File.public_id == info["public_id"]))

    assert file.delete_token_hash != info["delete_token"]
    assert file.delete_token_hash == hash_token(info["delete_token"])


def test_deleting_one_duplicate_keeps_the_other(client):
    a = upload(client, b"same" * 100, name="a.txt").json()
    b = upload(client, b"same" * 100, name="b.txt").json()

    assert delete(client, a["public_id"], a["delete_token"]).status_code == 204

    assert client.get(f"/f/{b['public_id']}").content == b"same" * 100


def test_delete_without_credentials_is_forbidden(client):
    info = upload(client, b"data").json()

    assert client.delete(f"/f/{info['public_id']}").status_code == 403


def test_very_long_content_type_does_not_break_upload(client):
    resp = client.post(
        "/upload", files={"file": ("a.txt", b"data", "text/" + "x" * 300)}
    )

    assert resp.status_code == 201


def test_nul_byte_in_filename_is_removed(client):
    resp = client.post("/upload", files={"file": ("a\x00b.txt", b"data", "text/plain")})

    assert resp.status_code == 201
    assert "\x00" not in resp.json()["filename"]