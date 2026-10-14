import os
import re
import zipfile
from collections.abc import Iterable, Iterator
from datetime import datetime

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def safe_name(raw: str | None, fallback: str = "file") -> str:
    name = _CONTROL.sub("", raw or "")
    name = re.split(r"[\\/]", name)[-1].strip()
    if name in ("", ".", ".."):
        return fallback
    return name[:255]


def unique_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        stem, ext = os.path.splitext(name)
        candidate, i = name, 0
        while candidate.lower() in seen:
            i += 1
            candidate = f"{stem} ({i}){ext}"
        seen.add(candidate.lower())
        result.append(candidate)
    return result


def archive_filename(requested: str | None, now: datetime) -> str:
    name = safe_name(requested, fallback="") if requested else ""
    if not name:
        name = f"files-{now:%Y%m%d-%H%M%S}"
    return name if name.lower().endswith(".zip") else f"{name}.zip"


class _Sink:
    def __init__(self) -> None:
        self._buf = bytearray()
        self._pos = 0

    def write(self, data: bytes) -> int:
        self._buf += data
        self._pos += len(data)
        return len(data)

    def tell(self) -> int:
        return self._pos

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        data = bytes(self._buf)
        self._buf.clear()
        return data


def iter_zip(
        entries: Iterable[tuple[str, Iterable[bytes]]],
        *,
        force_zip64: bool = False
) -> Iterator[bytes]:
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for name, chunks in entries:
            info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            with zf.open(info, "w", force_zip64=force_zip64) as entry:
                for chunk in chunks:
                    entry.write(chunk)
                    if data := sink.drain():
                        yield data
            if data := sink.drain(): # дескриптор данных
                yield data
    if data := sink.drain(): # центральный каталог
        yield data

