from app.config import Settings
from app. models import User

from dataclasses import dataclass


@dataclass(frozen=True)
class UploadLimits:
    max_size: int
    quota_bytes: int | None = None # общая квота


class TtlNotAllowed(Exception):
    def __init__(self, max_hours: int) -> None:
        super().__init__(f"max {max_hours} hours")
        self.max_hours = max_hours


def resolve_ttl_hours(
        user: User,
        requested: int | None,
        settings: Settings,
) -> int | None:
    if user is not None:
        return requested

    limit = settings.anon_ttl_hours
    if requested is None:
        return limit
    if requested > limit:
        raise TtlNotAllowed(limit)
    return requested


def limit_for(user: User | None, settings: Settings) -> UploadLimits:
    if user is None:
        return UploadLimits(min(settings.anon_max_upload_size, settings.max_upload_size))
    return UploadLimits(settings.max_upload_size, settings.user_quota_bytes)

