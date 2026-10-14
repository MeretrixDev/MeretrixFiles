import io
import zipfile
from datetime import datetime

import pytest

from app.services.archive import archive_filename, iter_zip, safe_name, unique_names


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("..\\..\\evil.txt", "evil.txt"),
        ("C:\\Users\\x\\doc.txt", "doc.txt"),
        ("a\x00b.txt", "ab.txt"),
        ("bad\nname.txt", "badname.txt"),
        ("..", "file"),
        ("", "file"),
        (None, "file"),
        (".gitignore", ".gitignore"),
        ("отчёт.txt", "отчёт.txt"),
    ],
)
def test_safe_name(raw, expected):
    assert safe_name(raw) == expected


def test_safe_name_limits_length():
    assert len(safe_name("x" * 300)) == 255


def test_unique_names_keeps_order_and_suffixes_duplicates():
    names = ["a.txt", "a.txt", "A.TXT", "b", "b", ".env", ".env"]

    assert unique_names(names) == [
        "a.txt", "a (1).txt", "A (2).TXT", "b", "b (1)", ".env", ".env (1)",
    ]


NOW = datetime(2026, 10, 15, 12, 30, 45)


@pytest.mark.parametrize(
    "requested,expected",
    [
        (None, "files-20261015-123045.zip"),
        ("", "files-20261015-123045.zip"),
        ("photos", "photos.zip"),
        ("photos.ZIP", "photos.ZIP"),
        ("../../x", "x.zip"),
    ],
)
def test_archive_filename(requested, expected):
    assert archive_filename(requested, NOW) == expected


def build(entries, **kw) -> bytes:
    return b"".join(iter_zip(entries, **kw))


def test_zip_roundtrip_and_valid_structure():
    entries = [
        ("a.txt", [b"hello ", b"world"]),
        ("б.txt", [b"x" * 100_000]),
        ("empty.bin", []),
    ]

    with zipfile.ZipFile(io.BytesIO(build(entries))) as zf:
        assert zf.testzip() is None
        assert zf.namelist() == ["a.txt", "б.txt", "empty.bin"]
        assert zf.read("a.txt") == b"hello world"
        assert zf.read("б.txt") == b"x" * 100_000
        assert zf.read("empty.bin") == b""


def test_same_input_gives_identical_bytes():
    def entries():
        return [("a.txt", [b"data"]), ("b.txt", [b"more"])]

    assert build(entries()) == build(entries())  # на этом держится дедупликация архивов


def test_zip_is_produced_incrementally():
    chunks = list(iter_zip([("big.bin", [b"y" * 1000] * 1000)]))

    assert len(chunks) > 100
    assert max(map(len, chunks)) < 5000


def test_force_zip64_still_produces_a_readable_archive():
    data = build([("a.txt", [b"abc"])], force_zip64=True)

    assert zipfile.ZipFile(io.BytesIO(data)).read("a.txt") == b"abc"

