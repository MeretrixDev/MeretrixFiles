import os
import time
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from app.cli import main
from app.db import engine
from app.models import File
from app.services.maintenance import LOCK_KEY, run_cleanup
from app.services.storage import storage_upload


def add(db, settings, store, data: bytes, ttl):
    file, _ = storage_upload(db, settings, store, [data], filename="a.txt", ttl=ttl)
    return file


def test_run_cleanup_removes_expired_files_and_stale_tmp(db, settings, store):
    add(db, settings, store, b"old" * 100, timedelta(hours=-1))
    add(db, settings, store, b"new" * 100, timedelta(hours=1))
    stale = settings.temp_dir / "stale"
    stale.write_bytes(b"x")
    two_hours_ago = time.time() - 7200
    os.utime(stale, (two_hours_ago, two_hours_ago))
    fresh = settings.temp_dir / "fresh"
    fresh.write_bytes(b"x")

    result = run_cleanup(settings)

    assert (result.files_removed, result.tmp_removed, result.skipped) == (1, 1, False)
    assert not stale.exists() and fresh.exists()
    assert db.scalar(select(func.count()).select_from(File)) == 1


def test_cli_purge_prints_summary_and_exits_zero(db, settings, store, capsys):
    add(db, settings, store, b"old" * 100, timedelta(hours=-1))

    assert main(["purge"]) == 0

    assert "expired files removed: 1" in capsys.readouterr().out


def test_cli_requires_a_command():
    with pytest.raises(SystemExit) as exc:
        main([])

    assert exc.value.code == 2


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="advisory-блокировки есть только в PostgreSQL")
def test_cleanup_does_not_leave_the_lock_behind(settings):
    run_cleanup(settings)

    with engine.connect() as c:
        held = c.execute(
            text(
                "SELECT count(*) FROM pg_locks "
                "WHERE locktype = 'advisory' AND classid = CAST(:c AS oid)"
            ),
            {"c": LOCK_KEY >> 32},
        ).scalar()

    assert held == 0


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="advisory-блокировки есть только в PostgreSQL")
def test_cleanup_is_skipped_while_another_runner_holds_the_lock(settings):
    with engine.connect() as other:
        other.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_KEY})
        try:
            result = run_cleanup(settings)
        finally:
            other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})

    assert result.skipped

