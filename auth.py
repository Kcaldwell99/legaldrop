import os
from typing import Optional
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from passlib.context import CryptContext
from fastapi import Request, HTTPException
from database import get_pool

SECRET_KEY      = os.environ.get("SECRET_KEY", "change-me")
SESSION_COOKIE  = "ld_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
signer  = URLSafeTimedSerializer(SECRET_KEY)


def hash_password(p: str) -> str:      return pwd_ctx.hash(p)
def verify_password(p: str, h: str):   return pwd_ctx.verify(p, h)


def make_delivery_token(delivery_id: str) -> str:
    return signer.dumps({"did": delivery_id}, salt="delivery-access")


def decode_delivery_token(token: str, max_age_seconds: int) -> Optional[str]:
    try:
        data = signer.loads(token, salt="delivery-access", max_age=max_age_seconds)
        return data["did"]
    except (BadSignature, SignatureExpired):
        return None


def create_session_token(user_id: str) -> str:
    return signer.dumps(user_id, salt="session")


def decode_session_token(token: str) -> Optional[str]:
    try:
        return signer.loads(token, salt="session", max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def set_session(response, user_id: str):
    token = create_session_token(str(user_id))
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("BASE_URL", "").startswith("https"),
    )


def clear_session(response):
    response.delete_cookie(SESSION_COOKIE)


async def get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    user_id = decode_session_token(token)
    if not user_id:
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, email, full_name, firm_name, bar_number, state, credit_cents "
            "FROM users WHERE id=$1", user_id,
        )
    return dict(row) if row else None


async def require_user(request: Request) -> dict:
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user