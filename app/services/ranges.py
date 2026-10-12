import re

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)", re.ASCII | re.IGNORECASE)


class RangeNotSatisfiable(Exception): ... # 416 err

def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    if not header:
        return None
    match = _RANGE_RE.fullmatch(header.strip())
    if match is None:
        return None

    first, last = match.groups()
    if not first and not last:
        return None

    if not first: # последниe 500 bytes
        suffix = int(last)
        if suffix == 0:
            raise RangeNotSatisfiable(f"bytes={first} bytes={last}")
        start, end = max(size - suffix, 0), size - 1
    else:
        start = int(first)
        end = int(last) if last else size - 1
        if last and end < start:
            return None # нк диапазон

    if start >= size:
        raise RangeNotSatisfiable(f"bytes={first} bytes={last}")
    return start, min(end, size - 1)

