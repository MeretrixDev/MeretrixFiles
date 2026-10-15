import shutil
from pathlib import Path

class InsufficientStorageError(Exception): ...


def disk_usage(path: Path) -> tuple[int, int, int]:
    u = shutil.disk_usage(path)
    return u.total, u.used, u.free


def check_free_space(path: Path, min_free: int, expected: int = 0) -> None:
    _total, _used, free = disk_usage(path)
    if free - expected < min_free:
        raise InsufficientStorageError(f"free={free}, expected={expected}, min_free={min_free}")

