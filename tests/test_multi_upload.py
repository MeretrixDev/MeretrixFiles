import io
import re
import zipfile

from sqlalchemy import func, select

from app.models import Blob, File


def upload_many(client, items, headers=None, **form):
    return client.post(
        "/api/v1/upload",
        files=[("file", (name, data, "application/octet-stream")) for name, data in items],
        data=form,
        headers=headers,
    )


def open_zip(client, public_id, headers=None) -> zipfile.ZipFile:
    resp = client.get(f"/f/{public_id}", headers=headers)
    assert resp.status_code == 200
    return zipfile.ZipFile(io.BytesIO(resp.content))


def test_several_files_become_one_zip_archive(client, db):
    items = [("a.txt", b"alpha " * 1000), ("b.txt", b"beta " * 1000), ("c.bin", b"\x00\x01" * 500)]

    resp = upload_many(client, items)

    assert resp.status_code == 201
    info = resp.json()
    assert info["content_type"] == "application/zip"
    assert info["filename"].endswith(".zip")
    assert db.scalar(select(func.count()).select_from(File)) == 1  # одна запись, одна ссылка
    with open_zip(client, info["public_id"]) as zf:
        assert zf.testzip() is None
        assert zf.namelist() == ["a.txt", "b.txt", "c.bin"]
        for name, data in items:
            assert zf.read(name) == data


def test_single_file_is_not_archived(client):
    info = upload_many(client, [("a.txt", b"data" * 100)]).json()

    assert info["filename"] == "a.txt"
    assert client.get(f"/f/{info['public_id']}").content == b"data" * 100


def test_duplicate_names_inside_archive_are_renamed(client):
    info = upload_many(client, [("a.txt", b"1"), ("a.txt", b"2"), ("A.TXT", b"3")]).json()

    with open_zip(client, info["public_id"]) as zf:
        assert zf.namelist() == ["a.txt", "a (1).txt", "A (2).TXT"]
        assert [zf.read(n) for n in zf.namelist()] == [b"1", b"2", b"3"]


def test_paths_in_names_cannot_escape_the_archive(client):
    info = upload_many(client, [("../../evil.txt", b"1"), ("sub/dir/x.txt", b"2")]).json()

    with open_zip(client, info["public_id"]) as zf:
        assert zf.namelist() == ["evil.txt", "x.txt"]


def test_cyrillic_names_survive_in_archive(client):
    info = upload_many(client, [("отчёт.txt", b"1"), ("фото.txt", b"2")]).json()

    with open_zip(client, info["public_id"]) as zf:
        assert zf.namelist() == ["отчёт.txt", "фото.txt"]


def test_archive_name_can_be_chosen(client):
    info = upload_many(client, [("a", b"1"), ("b", b"2")], archive_name="holiday").json()

    assert info["filename"] == "holiday.zip"


def test_default_archive_name_has_timestamp(client):
    info = upload_many(client, [("a", b"1"), ("b", b"2")]).json()

    assert re.fullmatch(r"files-\d{8}-\d{6}\.zip", info["filename"])


def test_too_many_files_are_rejected_and_nothing_is_stored(client, db, override_settings):
    override_settings(max_files_per_upload=2)

    resp = upload_many(client, [(f"{i}.txt", b"x") for i in range(3)])

    assert resp.status_code == 422
    assert db.scalar(select(func.count()).select_from(File)) == 0


def test_archive_is_compressed_by_the_storage_even_though_it_is_a_zip(client):
    items = [("a.txt", b"hello " * 50_000), ("b.txt", b"world " * 50_000)]

    info = upload_many(client, items).json()

    assert info["algorithm"] == "zstd-seekable"
    assert info["stored_size"] < info["original_size"] / 20
    with open_zip(client, info["public_id"]) as zf:
        assert zf.read("a.txt") == items[0][1]


def test_same_files_in_same_order_give_one_stored_archive(client, db):
    items = [("a.txt", b"1" * 1000), ("b.txt", b"2" * 1000)]

    upload_many(client, items)
    upload_many(client, items)

    assert db.scalar(select(func.count()).select_from(File)) == 2
    assert db.scalar(select(func.count()).select_from(Blob)) == 1


def test_size_limit_applies_to_the_whole_archive(client, override_settings):
    override_settings(anon_max_upload_size=150_000)
    two_files = [("a.txt", b"a" * 100_000), ("b.txt", b"b" * 100_000)]

    assert upload_many(client, two_files).status_code == 413
    assert upload_many(client, two_files[:1]).status_code == 201


def test_archive_supports_range_requests(client):
    info = upload_many(client, [("a.txt", b"1" * 1000), ("b.txt", b"2" * 1000)]).json()

    resp = client.get(f"/f/{info['public_id']}", headers={"Range": "bytes=0-3"})

    assert resp.status_code == 206
    assert resp.content == b"PK\x03\x04"


def test_archive_follows_visibility_rules(client, make_user):
    alice = make_user()

    info = upload_many(
        client, [("a", b"1"), ("b", b"2")], headers=alice, visibility="private"
    ).json()

    assert info["visibility"] == "private"
    assert client.get(f"/f/{info['public_id']}").status_code == 404
    assert client.get(f"/f/{info['public_id']}", headers=alice).status_code == 200

