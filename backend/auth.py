"""
auth.py — Custom JWT authentication (step ① of the request flow).

Signup/login hash passwords with bcrypt, store users in the `users` table
(created in db.py), and issue a JWT. Protected routes use the `current_user_id`
dependency to extract the authenticated user's id from the Bearer token.

Secrets come from the environment:
  JWT_SECRET      — signing key (REQUIRED in production; a dev default is used
                    if unset so the app still boots, with a warning).
  JWT_EXPIRE_HOURS — token lifetime in hours (default 24).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt  # PyJWT
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import db

JWT_ALGORITHM = "HS256"


def _jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        # Lets local dev run without config, but tokens won't survive a restart
        # with a different secret. Always set JWT_SECRET in production.
        return "dev-insecure-secret-change-me"
    return secret


def _expire_hours() -> int:
    try:
        return int(os.getenv("JWT_EXPIRE_HOURS", "24"))
    except ValueError:
        return 24


# --------------------------------------------------------------------------- #
# Password hashing (bcrypt)                                                    #
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# JWT                                                                          #
# --------------------------------------------------------------------------- #
def create_token(user_id: int, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + timedelta(hours=_expire_hours()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])


# --------------------------------------------------------------------------- #
# DB-backed user operations                                                    #
# --------------------------------------------------------------------------- #
class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


async def signup(email: str, password: str) -> dict:
    """Create a user (bcrypt hash) and return {token, user}. 409 if email taken."""
    if not db.is_connected():
        raise AuthError("Database not configured; cannot sign up.", status=503)
    if not email or not password:
        raise AuthError("Email and password are required.", status=400)

    password_hash = hash_password(password)
    pool = db._require_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)
        if existing:
            raise AuthError("An account with this email already exists.", status=409)
        row = await conn.fetchrow(
            """
            INSERT INTO users (email, password_hash)
            VALUES ($1, $2)
            RETURNING id, email
            """,
            email,
            password_hash,
        )
    token = create_token(row["id"], row["email"])
    return {"token": token, "user": {"id": row["id"], "email": row["email"]}}


async def login(email: str, password: str) -> dict:
    """Validate credentials and return {token, user}. 401 on bad creds."""
    if not db.is_connected():
        raise AuthError("Database not configured; cannot log in.", status=503)

    pool = db._require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, password_hash FROM users WHERE email = $1", email
        )
    if not row or not verify_password(password, row["password_hash"]):
        raise AuthError("Invalid email or password.", status=401)

    token = create_token(row["id"], row["email"])
    return {"token": token, "user": {"id": row["id"], "email": row["email"]}}


# --------------------------------------------------------------------------- #
# FastAPI dependency: extract the authenticated user's id from the Bearer token #
# --------------------------------------------------------------------------- #
_bearer = HTTPBearer(auto_error=True)


def current_user_id(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> int:
    """Use as `Depends(current_user_id)` on protected routes."""
    try:
        payload = decode_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail={"message": "Token has expired."})
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail={"message": "Invalid token."})
    sub = payload.get("sub")
    if sub is None:
        raise HTTPException(status_code=401, detail={"message": "Malformed token."})
    return int(sub)
