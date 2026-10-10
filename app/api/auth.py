from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_current_user, limit_login, limit_register
from app.config import Settings, get_settings
from app.db import get_db
from app.models import User
from app.services.security import (
    DUMMY_HASH,
    create_access_token,
    hash_password,
    verify_password,
)

router = APIRouter(tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


def _normalize(email: str) -> str:
    return email.strip().lower()


@router.post("/auth/register", response_model=UserOut, status_code=201, dependencies=[Depends(limit_register)])
def register(body: RegisterIn, db: Session = Depends(get_db)):
    email = _normalize(body.email)
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered")
    return user


@router.post("/auth/login", response_model=TokenOut, dependencies=[Depends(limit_login)])
def login(
        form: OAuth2PasswordRequestForm = Depends(),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings),
):
    user = db.scalar(select(User).where(User.email == _normalize(form.username)))

    password_ok = verify_password(
        form.password, user.password_hash if user else DUMMY_HASH
    )

    if user is None or not password_ok or not user.is_active:
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenOut(access_token=create_access_token(user.id, settings))


@router.get("/users/me", response_model=UserOut)
def read_me(user: User = Depends(get_current_user)):
    return user

