from datetime import datetime, timezone
from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Blob(Base):
    __tablename__ = "blobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    original_size: Mapped[int] = mapped_column(BigInteger)
    stored_size: Mapped[int] = mapped_column(BigInteger)
    algorithm: Mapped[str] = mapped_column(String(64), default="zstd") # zstd или none
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow())

    files: Mapped[list["File"]] = relationship(back_populates="blob")

class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(127), default="application/octet-stream")
    blob_id: Mapped[int] = mapped_column(ForeignKey("blobs.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    blob: Mapped[Blob] = relationship(back_populates="files")