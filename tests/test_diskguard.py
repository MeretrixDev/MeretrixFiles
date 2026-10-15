import errno
from collections import namedtuple
from pathlib import Path

import pytest

from app.services.diskguard import InsufficientStorageError, check_free_space

Usage = namedtuple("Usage", "total used free")
GB = 1024**3


@pytest.fixture
def disk(monkeypatch):
    def set_free(free):
        monkeypatch.setattr(
            "app.services.diskguard.shutil.disk_usage", lambda p: Usage(100 * GB, 100 * GB - free, free)
        )

    return set_free


def test_enough_space_passes(disk):
    disk(10 * GB)

    check_free_space(Path("."), min_free=5 * GB, expected=4 * GB)


def test_upload_that_would_cross_the_threshold_is_rejected(disk):
    disk(10 * GB)

    with pytest.raises(InsufficientStorageError):
        check_free_space(Path("."), min_free=5 * GB, expected=6 * GB)


def test_exactly_at_the_threshold_is_allowed(disk):
    disk(10 * GB)

    check_free_space(Path("."), min_free=5 * GB, expected=5 * GB)


def test_zero_threshold_disables_the_guard(disk):
    disk(0)

    check_free_space(Path("."), min_free=0)


def upload(client, data=b"x" * 1000):
    return client.post("/api/v1/upload", files={"file": ("a.txt", data, "text/plain")})


def test_full_disk_returns_507_and_stores_nothing(client, override_settings, settings):
    override_settings(min_free_bytes=10**18)

    resp = upload(client)

    assert resp.status_code == 507
    assert [p for p in settings.storage_dir.rglob("*") if p.is_file()] == []


def test_normal_upload_is_unaffected_by_the_guard(client):
    assert upload(client).status_code == 201


def test_disk_full_in_the_middle_of_a_write_returns_507(client, monkeypatch, settings):
    def boom(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("app.services.storage.store_stream", boom)

    resp = upload(client)

    assert resp.status_code == 507
    assert list(settings.temp_dir.iterdir()) == []


def test_other_os_errors_are_not_hidden_as_507(client, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr("app.services.storage.store_stream", boom)

    with pytest.raises(OSError):
        upload(client)


def test_stats_report_whether_uploads_are_accepted(client, override_settings):
    ok = client.get("/api/v1/stats").json()
    assert ok["accepting_uploads"] is True
    assert 0 <= ok["disk_used_perc"] <= 100

