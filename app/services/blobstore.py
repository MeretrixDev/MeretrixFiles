import os
from pathlib import Path
from typing import Protocol

from app.config import Settings

_SUFFIXES = {"zstd-seekable": "zst", "none": "raw"}


def blob_key(sha256: str, algorithm: str) -> str:
    return f"{sha256[:2]}/{sha256}.{_SUFFIXES[algorithm]}"


class BlobStore(Protocol):
    def put(self, key: str, src: Path) -> None: ...

    def exists(self, key: str) -> bool: ...

    def size(self, key: str) -> int: ...

    def read_range(self, key: str, offset: int, length: int) -> bytes: ...

    def delete(self, key: str) -> None: ...


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()

        if self.root.resolve() not in path.parents:
            raise ValueError(f"Invalid key: {key!r}")
        return path

    def put(self, key: str, src: Path) -> None:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    def read_range(self, key: str, offset: int, length: int) -> bytes:
        with self._path(key).open(mode="rb") as f:
            f.seek(offset)
            return f.read(length)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def make_blob_storage(settings: Settings) -> BlobStore:
    return LocalBlobStore(settings.storage_dir)

