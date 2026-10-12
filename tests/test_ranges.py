import pytest

from app.services.ranges import RangeNotSatisfiable, parse_range


@pytest.mark.parametrize(
    "header,size,expected",
    [
        (None, 100, None),
        ("bytes=0-9", 100, (0, 9)),
        ("bytes=0-0", 100, (0, 0)),
        ("bytes=10-", 100, (10, 99)),
        ("bytes=-10", 100, (90, 99)),
        ("bytes=-500", 100, (0, 99)),      # суффикс длиннее файла: весь файл
        ("bytes=50-1000", 100, (50, 99)),  # конец за пределами: обрезаем
        ("bytes=5-3", 100, None),          # некорректный диапазон: игнорируем
        ("bytes=0-1,5-6", 100, None),      # несколько диапазонов не поддерживаем
        ("items=0-1", 100, None),
        ("garbage", 100, None),
        ("bytes=-", 100, None),
    ],
)
def test_parse_range(header, size, expected):
    assert parse_range(header, size) == expected


@pytest.mark.parametrize(
    "header,size",
    [("bytes=100-", 100), ("bytes=200-300", 100), ("bytes=-0", 100), ("bytes=0-0", 0), ("bytes=-5", 0)],
)
def test_unsatisfiable_ranges(header, size):
    with pytest.raises(RangeNotSatisfiable):
        parse_range(header, size)