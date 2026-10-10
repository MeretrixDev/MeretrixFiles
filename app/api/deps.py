import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from collections.abc import Callable

from app.db import get_db
from app.config import Settings, get_settings
from app.models import User
from app.services.security import decode_access_token
from app.services.ratelimit import limiter

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_optional_user(
        token: str = Depends(oauth2_scheme),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
) -> User:

    if token is None:
        return None

    try:
        user_id = int(decode_access_token(token, settings=settings))
    except (jwt.InvalidTokenError, ValueError):
        raise _unauthorized("Invalid or expired token")

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("Invalid or expired token")
    return user


def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    if user is None:
        raise _unauthorized("Not logged in")
    return user

def rate_limit(
        name: str,
        get_limit: Callable[[Settings], int],
        window: int,
):
    def dependency(
            request: Request, settings: Settings = Depends(get_settings)
    ) -> None:
        if not settings.rate_limit_enabled:
            return

        ip = request.client.host if request.client else "unknown"
        allowed, retry_after = limiter.hit(
            f"{name}:{ip}",
            get_limit(settings),
            window
        )

        if not allowed:
            raise HTTPException(
                429,
                "Too many requests, please try again later.",
                headers={"Retry-After": str(retry_after)},
            )
    return dependency

limit_login = rate_limit(
    "login",
    lambda s: s.rate_login_per_minute, 60
)
limit_register = rate_limit(
    "register",
    lambda s: s.rate_register_per_hour, 3600
)
limit_upload = rate_limit(
    "upload",
    lambda s: s.rate_upload_per_minute, 60
)