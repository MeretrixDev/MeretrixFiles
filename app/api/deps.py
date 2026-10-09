import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.config import Settings, get_settings
from app.models import User
from app.services.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
        token: str = Depends(oauth2_scheme),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
) -> User:
    error = HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    try:
        user_id = int(decode_access_token(token, settings))
    except (jwt.InvalidTokenError, ValueError):
        raise error

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise error
    return user