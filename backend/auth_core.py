"""Authentication primitives: bcrypt hashing, JWT sessions with password-version revocation, role guards."""
import os
import re
import jwt
import bcrypt
from datetime import timedelta
from bson import ObjectId
from fastapi import HTTPException, Request, Depends
from core import db, now_utc

JWT_ALG = "HS256"
ACCESS_MIN = 30


def hash_pw(p):
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt(rounds=12)).decode()


def check_pw(p, h):
    try:
        return bcrypt.checkpw(p.encode(), h.encode())
    except ValueError:
        return False


def password_problems(pw, username="", email=""):
    out = []
    if len(pw) < 10:
        out.append("at least 10 characters")
    if len(pw) > 128:
        out.append("at most 128 characters")
    if not re.search(r"[a-z]", pw):
        out.append("a lowercase letter")
    if not re.search(r"[A-Z]", pw):
        out.append("an uppercase letter")
    if not re.search(r"\d", pw):
        out.append("a number")
    if not re.search(r"[^A-Za-z0-9]", pw):
        out.append("a symbol")
    low = pw.lower()
    for part in (username, (email or "").split("@")[0]):
        if part and len(part) >= 3 and part.lower() in low:
            out.append("no username or email name inside it")
            break
    return out


def require_strong(pw, username="", email=""):
    p = password_problems(pw, username, email)
    if p:
        raise HTTPException(422, "Password needs " + ", ".join(p) + ".")


def make_token(uid, email, kind, minutes, pv=0, jti=None):
    claims = {"sub": uid, "email": email, "type": kind, "pv": pv, "exp": now_utc() + timedelta(minutes=minutes)}
    if jti:
        claims["jti"] = jti
    return jwt.encode(claims, os.environ["JWT_SECRET"], algorithm=JWT_ALG)


def refresh_minutes(remember=True):
    return 60 * 24 * 7 if remember else 60 * 12


def _samesite():
    # Same-origin deployment (site + /api on one domain behind Caddy) → "lax" blocks cross-site cookie use.
    # Use COOKIE_SAMESITE=none only if the frontend is hosted on a different domain from the API.
    v = (os.environ.get("COOKIE_SAMESITE") or "lax").strip().lower()
    return v if v in ("lax", "strict", "none") else "lax"


def set_cookies(resp, u, remember=True, jti=None):
    uid, pv = str(u["_id"]), u.get("pv", 0)
    refresh_min = refresh_minutes(remember)
    resp.set_cookie("access_token", make_token(uid, u["email"], "access", ACCESS_MIN, pv), httponly=True, secure=True, samesite=_samesite(), max_age=ACCESS_MIN * 60, path="/")
    resp.set_cookie("refresh_token", make_token(uid, u["email"], "refresh", refresh_min, pv, jti), httponly=True, secure=True, samesite=_samesite(),
                    max_age=refresh_min * 60 if remember else None, path="/")
    resp.set_cookie("remember", "1" if remember else "0", httponly=True, secure=True, samesite=_samesite(), max_age=refresh_min * 60 if remember else None, path="/")


def clear_cookies(resp):
    for c in ("access_token", "refresh_token", "remember"):
        resp.delete_cookie(c, path="/", samesite=_samesite(), secure=True)


def public_user(u):
    return {"id": str(u["_id"]), "email": u["email"], "username": u.get("username"), "name": u.get("name"),
            "role": "admin" if u.get("role") == "admin" else "user", "status": u.get("status", "active"),
            "email_verified": bool(u.get("email_verified")), "created_at": u.get("created_at"), "last_login": u.get("last_login"),
            "totp_enabled": bool(u.get("totp_enabled"))}


async def load_valid_user(p):
    try:
        u = await db.users.find_one({"_id": ObjectId(p["sub"])})
    except Exception:
        u = None
    if not u:
        raise HTTPException(401, "User not found")
    if u.get("status", "active") == "pending":
        raise HTTPException(401, "This account is waiting for administrator approval")
    if u.get("status", "active") != "active":
        raise HTTPException(401, "This account has been deactivated by the administrator")
    if p.get("pv", 0) != u.get("pv", 0):
        raise HTTPException(401, "Session ended because the password was changed")
    return u


async def current_user(request: Request):
    token = request.cookies.get("access_token")
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        token = token or h[7:]
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        p = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")
    if p.get("type") != "access":
        raise HTTPException(401, "Invalid token type")
    return public_user(await load_valid_user(p))


async def admin_user(u=Depends(current_user)):
    if u["role"] != "admin":
        raise HTTPException(403, "View-only account: only the admin can change data")
    return u
