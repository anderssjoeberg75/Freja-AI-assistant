"""Authentication routes for multi-user user management and JWT issuance."""

import datetime
import collections
import time
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
import bcrypt
import jwt

from backend.config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRATION_MINUTES
from backend.database import get_db_session
from backend.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

_auth_failures = collections.defaultdict(list)  # key -> list of failure timestamps


def _check_auth_rate_limit(key: str, max_attempts: int = 10, window_secs: int = 300):
    now = time.time()
    attempts = [t for t in _auth_failures[key] if now - t < window_secs]
    _auth_failures[key] = attempts
    if len(attempts) >= max_attempts:
        raise HTTPException(
            status_code=429,
            detail="Too many failed authentication attempts. Please try again in a few minutes."
        )


def _record_auth_failure(key: str):
    _auth_failures[key].append(time.time())


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, description="Password must be at least 8 characters long")
    name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    email: str


def hash_password(password: str) -> str:
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    pwd_bytes = plain_password.encode("utf-8")
    hash_bytes = hashed_password.encode("utf-8")
    return bcrypt.checkpw(pwd_bytes, hash_bytes)


def create_access_token(user_id: int, email: str) -> str:
    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=JWT_EXPIRATION_MINUTES)
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": expire
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user_from_token(token: str = Depends(oauth2_scheme)) -> User:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload.get("sub"))
    except (jwt.PyJWTError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )

    with get_db_session() as db:
        user = db.query(User).filter(User.id == user_id, User.is_active == 1).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found"
            )
        return user


def get_current_user(token: str | None = Depends(oauth2_scheme)) -> User:
    """Dependency that returns the authenticated User from JWT token, or falls back to user_id=1 for single-tenant mode."""
    if token:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            user_id = int(payload.get("sub"))
            with get_db_session() as db:
                user = db.query(User).filter(User.id == user_id, User.is_active == 1).first()
                if user:
                    return user
        except (jwt.PyJWTError, ValueError, TypeError):
            pass

    # Fallback to default primary user (user_id = 1) for backward compatibility
    with get_db_session() as db:
        user = db.query(User).filter(User.id == 1).first()
        if not user:
            user = User(id=1, email="admin@freja.local", password_hash="", name="Admin", is_active=1)
        return user


@router.post("/register", response_model=TokenResponse)
def register(req: RegisterRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    _check_auth_rate_limit(f"register:{ip}", max_attempts=5, window_secs=300)

    email_clean = req.email.strip().lower()
    if not email_clean or not req.password:
        raise HTTPException(status_code=400, detail="Email and password are required.")

    with get_db_session() as db:
        existing = db.query(User).filter(User.email == email_clean).first()
        if existing:
            raise HTTPException(status_code=400, detail="This email address is already registered.")

        user = User(
            email=email_clean,
            password_hash=hash_password(req.password),
            name=req.name or email_clean.split("@")[0],
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            is_active=1
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        token = create_access_token(user.id, user.email)
        return TokenResponse(
            access_token=token,
            user_id=user.id,
            email=user.email
        )


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    _check_auth_rate_limit(f"login:{ip}", max_attempts=10, window_secs=300)

    email_clean = req.email.strip().lower()
    with get_db_session() as db:
        user = db.query(User).filter(User.email == email_clean).first()
        if not user or not verify_password(req.password, user.password_hash):
            _record_auth_failure(f"login:{ip}")
            raise HTTPException(status_code=401, detail="Incorrect email or password.")
        if not user.is_active:
            raise HTTPException(status_code=403, detail="Account disabled.")

        token = create_access_token(user.id, user.email)
        return TokenResponse(
            access_token=token,
            user_id=user.id,
            email=user.email
        )


@router.get("/me")
def get_me(user: User = Depends(get_current_user_from_token)):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "created_at": user.created_at
    }
