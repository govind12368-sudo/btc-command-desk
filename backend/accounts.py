"""Accounts: login, registration, email verification, password reset/change, admin user management."""
import os
import re
import hashlib
import secrets
import logging
from datetime import timedelta
from html import escape
from typing import Optional
import jwt
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from pydantic import BaseModel, Field
from core import db, now_utc, iso, parse_iso, get_settings, APP_NAME
from auth_core import (hash_pw, check_pw, require_strong, make_token, set_cookies, clear_cookies, public_user,
                       load_valid_user, current_user, admin_user, JWT_ALG, ACCESS_MIN, refresh_minutes)
import alerts
import security as SEC

log = logging.getLogger("accounts")
router = APIRouter(prefix="/api")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
USER_RE = re.compile(r"^[a-zA-Z0-9_.]{3,24}$")
RESERVED = {"admin", "administrator", "root", "support", "system", "btcdesk", "vantage", "moderator"}
GENERIC_FORGOT = "If an account exists for that address, a reset link has been sent. It expires in 30 minutes and works once."


def app_url():
    return os.environ["FRONTEND_URL"].split(",")[0].rstrip("/")


def client_ip(request):
    # Ingress appends the real client address as the last X-Forwarded-For hop; earlier hops are client-controlled.
    xff = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    return xff[-1] if xff else (request.client.host if request.client else "x")


def h(token):
    return hashlib.sha256(token.encode()).hexdigest()


LOGIN_FAIL_MSG = "Invalid username or password"
LOGIN_IP_LIMIT = 10        # failed logins per IP per 15 minutes (on top of the per-account lock)
ROTATION_GRACE_SEC = 60    # two tabs refreshing at once is not token theft


async def start_session(response, u, remember, request=None):
    """New refresh-token session: random jti stored server-side so it can be rotated and revoked."""
    jti = secrets.token_urlsafe(24)
    await db.sessions.insert_one({"_id": jti, "user_id": str(u["_id"]), "created_at": iso(now_utc()),
                                  "expires_at": now_utc() + timedelta(minutes=refresh_minutes(remember)),
                                  "revoked": False, "remember": bool(remember),
                                  "ip": SEC.request_ip(request) if request else None,
                                  "user_agent": (request.headers.get("user-agent") or "")[:200] if request else None})
    set_cookies(response, u, remember, jti)
    return jti


async def revoke_all_sessions(user_id, reason):
    await db.sessions.update_many({"user_id": str(user_id), "revoked": False},
                                  {"$set": {"revoked": True, "revoked_reason": reason, "revoked_at": iso(now_utc())}})


async def login_ip_blocked(ip):
    since = now_utc() - timedelta(minutes=15)
    return await db.login_ip_failures.count_documents({"ip": ip, "at": {"$gte": iso(since)}}) >= LOGIN_IP_LIMIT


async def login_ip_fail(ip):
    await db.login_ip_failures.insert_one({"ip": ip, "at": iso(now_utc()), "expires_at": now_utc() + timedelta(minutes=15)})


async def rate_limit(key, limit, minutes):
    since = iso(now_utc() - timedelta(minutes=minutes))
    n = await db.rate_events.count_documents({"key": key, "at": {"$gte": since}})
    if n >= limit:
        raise HTTPException(429, "Too many requests. Please wait and try again later.")
    await db.rate_events.insert_one({"key": key, "at": iso(now_utc()), "expires_at": now_utc() + timedelta(minutes=minutes)})


async def issue_token(uid, kind, minutes):
    raw = secrets.token_urlsafe(32)
    await db.auth_tokens.delete_many({"user_id": uid, "kind": kind, "used": False})
    await db.auth_tokens.insert_one({"_id": h(raw), "user_id": uid, "kind": kind, "used": False, "created_at": iso(now_utc()),
                                     "expires_at": now_utc() + timedelta(minutes=minutes)})
    return raw


async def consume_token(raw, kind):
    if not raw or len(raw) > 200:
        raise HTTPException(400, "Invalid or expired link")
    t = await db.auth_tokens.find_one_and_update({"_id": h(raw), "kind": kind, "used": False}, {"$set": {"used": True, "used_at": iso(now_utc())}})
    if not t:
        raise HTTPException(400, "Invalid, used or expired link")
    exp = t["expires_at"]
    if exp.tzinfo is None:
        from datetime import timezone
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now_utc():
        raise HTTPException(400, "This link has expired. Please request a new one.")
    return t


def mail_html(title, body, link, label):
    u = escape(link)
    return (f"<table role='presentation' width='100%'><tr><td style='padding:24px;font-family:Arial,sans-serif;color:#111'>"
            f"<h2 style='margin:0 0 12px'>{escape(title)}</h2><p>{escape(body)}</p>"
            f"<p><a href='{u}' style='display:inline-block;padding:10px 18px;background:#F59E0B;color:#111;text-decoration:none;border-radius:6px;font-weight:bold'>{escape(label)}</a></p>"
            f"<p style='font-size:12px;color:#555'>Or open: {u}</p>"
            f"<p style='font-size:12px;color:#888'>{escape(APP_NAME)} — 100% free, for educational purposes only. If you did not request this, ignore this email.</p></td></tr></table>")


async def send_verify(u):
    raw = await issue_token(str(u["_id"]), "verify", 60 * 24)
    link = f"{app_url()}/verify-email?token={raw}"
    await alerts.send_email(to=u["email"], subject=f"Verify your {APP_NAME} email", html=mail_html("Confirm your email", f"Hi {u.get('name') or u.get('username')}, please confirm your email address to finish setting up your free account.", link, "Verify email"))


class LoginIn(BaseModel):
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    remember: bool = True
    otp: Optional[str] = Field(default=None, max_length=32)


def _check_second_factor(u, otp):
    """True if the TOTP code or an unused recovery code matches. Returns (ok, used_recovery_hash)."""
    if not otp:
        return False, None
    try:
        if SEC.totp_ok(SEC.decrypt_secret(u["totp_secret_enc"]), otp):
            return True, None
    except Exception:
        log.warning("2FA secret could not be read", exc_info=True)
    hc = SEC.hash_code(otp)
    if hc in (u.get("recovery_hashes") or []):
        return True, hc
    return False, None


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, response: Response):
    ident_in = body.email.strip().lower()
    ident = f"login:{ident_in}"
    ip = SEC.request_ip(request)
    if await login_ip_blocked(ip):
        await SEC.log_event("login_blocked_ip", request, ident_in, ok=False)
        raise HTTPException(429, "Too many failed attempts from this network. Try again in 15 minutes.")
    att = await db.login_attempts.find_one({"identifier": ident})
    if att and parse_iso(att["last"]) <= now_utc() - timedelta(minutes=15):
        await db.login_attempts.delete_one({"identifier": ident})
        att = None
    if att and att.get("count", 0) >= 5:
        raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")
    u = await db.users.find_one({"email": ident_in} if "@" in ident_in else {"username": ident_in})
    if not u or not check_pw(body.password, u["password_hash"]):
        await db.login_attempts.update_one({"identifier": ident}, {"$inc": {"count": 1}, "$set": {"last": iso(now_utc())}}, upsert=True)
        await login_ip_fail(ip)
        await SEC.log_event("login_failed", request, ident_in, ok=False)
        raise HTTPException(401, LOGIN_FAIL_MSG)
    status = u.get("status", "active")
    if status == "pending":
        raise HTTPException(403, "Your account is waiting for administrator approval.")
    if status != "active":
        raise HTTPException(403, "This account has been deactivated. Contact the administrator.")
    if u.get("totp_enabled"):
        if not body.otp:
            raise HTTPException(401, "2FA_REQUIRED")
        ok, used = _check_second_factor(u, body.otp)
        if not ok:
            await db.login_attempts.update_one({"identifier": ident}, {"$inc": {"count": 1}, "$set": {"last": iso(now_utc())}}, upsert=True)
            await login_ip_fail(ip)
            await SEC.log_event("login_2fa_failed", request, u["email"], ok=False)
            raise HTTPException(401, "Invalid two-factor code")
        if used:
            await db.users.update_one({"_id": u["_id"]}, {"$pull": {"recovery_hashes": used}})
            await SEC.log_event("recovery_code_used", request, u["email"])
    await db.login_attempts.delete_one({"identifier": ident})
    await db.users.update_one({"_id": u["_id"]}, {"$set": {"last_login": iso(now_utc())}})
    await start_session(response, u, body.remember, request)
    await SEC.log_event("login_ok", request, u["email"])
    return {**public_user(u), "token": make_token(str(u["_id"]), u["email"], "access", ACCESS_MIN, u.get("pv", 0))}


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    username: str = Field(min_length=3, max_length=24)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    confirm_password: str = Field(min_length=1, max_length=128)


async def registration_mode():
    s, _ = await get_settings()
    m = s.get("registration_mode", "approval")
    return m if m in ("open", "approval", "closed") else "approval"


@router.get("/auth/registration")
async def registration_info():
    return {"mode": await registration_mode()}


@router.post("/auth/register")
async def register(body: RegisterIn, request: Request, response: Response):
    mode = await registration_mode()
    if mode == "closed":
        raise HTTPException(403, "New registrations are closed. Contact the administrator.")
    await rate_limit(f"register-any:{client_ip(request)}", 40, 60)
    name = re.sub(r"[<>\x00-\x1f]", "", body.name).strip()
    username, email = body.username.strip().lower(), body.email.strip().lower()
    if len(name) < 2:
        raise HTTPException(422, "Please enter your full name.")
    if not USER_RE.match(username) or username in RESERVED:
        raise HTTPException(422, "Username must be 3–24 letters, numbers, dots or underscores (and not a reserved name).")
    if not EMAIL_RE.match(email) or ".." in email:
        raise HTTPException(422, "Please enter a valid email address.")
    if body.password != body.confirm_password:
        raise HTTPException(422, "Passwords do not match.")
    require_strong(body.password, username, email)
    if await db.users.find_one({"email": email}):
        raise HTTPException(409, "An account with this email already exists.")
    if await db.users.find_one({"username": username}):
        raise HTTPException(409, "This username is already taken.")
    await rate_limit(f"register:{client_ip(request)}", 10, 60)
    doc = {"email": email, "username": username, "name": name, "password_hash": hash_pw(body.password), "role": "user",
           "status": "pending" if mode == "approval" else "active", "email_verified": False, "pv": 0,
           "created_at": iso(now_utc()), "last_login": None if mode == "approval" else iso(now_utc())}
    try:
        r = await db.users.insert_one(doc)
    except Exception:
        raise HTTPException(409, "Email or username already registered.")
    doc["_id"] = r.inserted_id
    sent = True
    try:
        await send_verify(doc)
    except Exception as e:
        log.warning(f"verify email failed: {e}")
        sent = False
    await SEC.log_event("register", request, email, detail=f"mode={mode}")
    if mode == "approval":
        return {"ok": True, "pending": True, "verification_email_sent": sent,
                "message": "Account created. An administrator must approve it before you can sign in."}
    await start_session(response, doc, True, request)
    return {**public_user(doc), "token": make_token(str(doc["_id"]), email, "access", ACCESS_MIN, 0), "verification_email_sent": sent}


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


@router.post("/auth/verify-email")
async def verify_email(body: TokenIn):
    t = await consume_token(body.token, "verify")
    await db.users.update_one({"_id": ObjectId(t["user_id"])}, {"$set": {"email_verified": True, "email_verified_at": iso(now_utc())}})
    return {"ok": True, "message": "Email verified. Thank you!"}


@router.post("/auth/resend-verification")
async def resend_verification(u=Depends(current_user)):
    if u["email_verified"]:
        return {"ok": True, "message": "Email already verified."}
    await rate_limit(f"verify:{u['id']}", 3, 60)
    doc = await db.users.find_one({"_id": ObjectId(u["id"])})
    try:
        await send_verify(doc)
    except Exception as e:
        raise HTTPException(502, f"Could not send email: {str(e)[:120]}")
    return {"ok": True, "message": "Verification email sent."}


class ForgotIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)


@router.post("/auth/forgot-password")
async def forgot(body: ForgotIn, request: Request):
    await rate_limit(f"forgot:{client_ip(request)}", 8, 60)
    ident = body.email.strip().lower()
    u = await db.users.find_one({"email": ident} if "@" in ident else {"username": ident})
    if u and u.get("status", "active") == "active":
        await rate_limit(f"forgot-user:{u['_id']}", 3, 60)
        raw = await issue_token(str(u["_id"]), "reset", 30)
        link = f"{app_url()}/reset-password?token={raw}"
        try:
            await alerts.send_email(to=u["email"], subject=f"Reset your {APP_NAME} password",
                                    html=mail_html("Password reset", "Use the button below to choose a new password. The link expires in 30 minutes and can be used once.", link, "Reset password"))
        except Exception as e:
            log.warning(f"reset email failed: {e}")
    return {"ok": True, "message": GENERIC_FORGOT}


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=1, max_length=128)
    confirm_password: str = Field(min_length=1, max_length=128)


@router.post("/auth/reset-password")
async def reset(body: ResetIn):
    if body.password != body.confirm_password:
        raise HTTPException(422, "Passwords do not match.")
    t = await db.auth_tokens.find_one({"_id": h(body.token), "kind": "reset", "used": False})
    u = await db.users.find_one({"_id": ObjectId(t["user_id"])}) if t else None
    if u:
        require_strong(body.password, u.get("username", ""), u["email"])
    t = await consume_token(body.token, "reset")
    u = await db.users.find_one({"_id": ObjectId(t["user_id"])})
    if not u:
        raise HTTPException(400, "Invalid link")
    await db.users.update_one({"_id": u["_id"]}, {"$set": {"password_hash": hash_pw(body.password), "password_changed_at": iso(now_utc()), "email_verified": True}, "$inc": {"pv": 1}})
    await revoke_all_sessions(u["_id"], "password_reset")
    await SEC.log_event("password_reset", None, u["email"])
    await db.login_attempts.delete_many({"identifier": {"$in": [f"login:{u['email']}", f"login:{u.get('username')}"]}})
    return {"ok": True, "message": "Password updated. Please sign in with your new password."}


class ChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)
    confirm_password: str = Field(min_length=1, max_length=128)


@router.post("/auth/change-password")
async def change_password(body: ChangeIn, response: Response, request: Request, me=Depends(current_user)):
    await rate_limit(f"change:{me['id']}", 10, 15)
    u = await db.users.find_one({"_id": ObjectId(me["id"])})
    if not check_pw(body.current_password, u["password_hash"]):
        raise HTTPException(400, "Current password is incorrect.")
    if body.new_password != body.confirm_password:
        raise HTTPException(422, "New passwords do not match.")
    if body.new_password == body.current_password:
        raise HTTPException(422, "New password must be different from the current one.")
    require_strong(body.new_password, u.get("username", ""), u["email"])
    await db.users.update_one({"_id": u["_id"]}, {"$set": {"password_hash": hash_pw(body.new_password), "password_changed_at": iso(now_utc())}, "$inc": {"pv": 1}})
    u = await db.users.find_one({"_id": u["_id"]})
    await revoke_all_sessions(u["_id"], "password_changed")
    await start_session(response, u, request.cookies.get("remember") != "0", request)
    await SEC.log_event("password_changed", request, u["email"])
    return {"ok": True, "message": "Password changed. Other sessions were signed out.", "token": make_token(str(u["_id"]), u["email"], "access", ACCESS_MIN, u.get("pv", 0))}


@router.post("/auth/refresh")
async def refresh(request: Request, response: Response):
    t = request.cookies.get("refresh_token")
    if not t:
        raise HTTPException(401, "No refresh token")
    try:
        p = jwt.decode(t, os.environ["JWT_SECRET"], algorithms=[JWT_ALG])
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid refresh token")
    if p.get("type") != "refresh":
        raise HTTPException(401, "Invalid token")
    u = await load_valid_user(p)
    remember = request.cookies.get("remember") != "0"
    jti = p.get("jti")
    if jti:
        sess = await db.sessions.find_one({"_id": jti})
        if not sess or sess.get("user_id") != str(u["_id"]):
            raise HTTPException(401, "Session not found. Please sign in again.")
        if sess.get("revoked"):
            rotated_at = parse_iso(sess.get("revoked_at")) if sess.get("revoked_at") else None
            benign = sess.get("revoked_reason") == "rotated" and rotated_at and (now_utc() - rotated_at).total_seconds() <= ROTATION_GRACE_SEC
            if not benign:
                # A refresh token that was already used came back: assume it was stolen and end every session.
                await revoke_all_sessions(u["_id"], "refresh_reuse")
                await db.users.update_one({"_id": u["_id"]}, {"$inc": {"pv": 1}})
                await SEC.log_event("refresh_token_reuse", request, u["email"], ok=False)
                clear_cookies(response)
                raise HTTPException(401, "Session ended for security reasons. Please sign in again.")
        else:
            await db.sessions.update_one({"_id": jti, "revoked": False},
                                         {"$set": {"revoked": True, "revoked_reason": "rotated", "revoked_at": iso(now_utc())}})
    await start_session(response, u, remember, request)
    return {"ok": True, "token": make_token(str(u["_id"]), u["email"], "access", ACCESS_MIN, u.get("pv", 0))}


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    t = request.cookies.get("refresh_token")
    if t:
        try:
            p = jwt.decode(t, os.environ["JWT_SECRET"], algorithms=[JWT_ALG], options={"verify_exp": False})
            if p.get("jti"):
                await db.sessions.update_one({"_id": p["jti"]}, {"$set": {"revoked": True, "revoked_reason": "logout", "revoked_at": iso(now_utc())}})
        except jwt.InvalidTokenError:
            pass
    clear_cookies(response)
    return {"ok": True}


@router.get("/auth/me")
async def me(u=Depends(current_user)):
    return u


class ProfileIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)


@router.put("/auth/profile")
async def profile(body: ProfileIn, u=Depends(admin_user)):
    name = re.sub(r"[<>\x00-\x1f]", "", body.name).strip()
    await db.users.update_one({"_id": ObjectId(u["id"])}, {"$set": {"name": name}})
    return public_user(await db.users.find_one({"_id": ObjectId(u["id"])}))


@router.get("/admin/users")
async def list_users(q: Optional[str] = None, a=Depends(admin_user)):
    f = {}
    if q:
        rx = re.escape(q.strip().lower())[:60]
        f = {"$or": [{"email": {"$regex": rx}}, {"username": {"$regex": rx}}, {"name": {"$regex": rx, "$options": "i"}}]}
    rows = [public_user(x) async for x in db.users.find(f, {"password_hash": 0}).sort("created_at", -1).limit(500)]
    return {"users": rows, "total": await db.users.count_documents({}), "pending": await db.users.count_documents({"status": "pending"}),
            "registration_mode": await registration_mode(),
            "note": "Passwords are stored as one-way bcrypt hashes and can never be viewed. Admin rights are never granted by registration."}


class StatusIn(BaseModel):
    status: str


@router.put("/admin/users/{uid}/status")
async def set_status(uid: str, body: StatusIn, a=Depends(admin_user)):
    if body.status not in ("active", "disabled"):
        raise HTTPException(422, "status must be active or disabled")
    if uid == a["id"]:
        raise HTTPException(400, "You cannot deactivate your own admin account.")
    try:
        _id = ObjectId(uid)
    except Exception:
        raise HTTPException(404, "User not found")
    t = await db.users.find_one({"_id": _id})
    if not t:
        raise HTTPException(404, "User not found")
    if t.get("role") == "admin":
        raise HTTPException(400, "Admin accounts cannot be deactivated here.")
    upd = {"$set": {"status": body.status, "status_changed_by": a["email"], "status_changed_at": iso(now_utc())}}
    if body.status == "disabled":
        upd["$inc"] = {"pv": 1}
    await db.users.update_one({"_id": _id}, upd)
    if body.status == "disabled":
        await revoke_all_sessions(_id, "disabled_by_admin")
    kind = "user_approved" if t.get("status") == "pending" and body.status == "active" else f"user_{body.status}"
    await SEC.log_event(kind, None, a["email"], detail=f"target={t['email']}")
    return public_user(await db.users.find_one({"_id": _id}))


# ---------------- admin two-factor authentication (TOTP) ----------------
class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class DisableIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=6, max_length=32)


@router.get("/auth/2fa")
async def twofa_status(a=Depends(admin_user)):
    u = await db.users.find_one({"_id": ObjectId(a["id"])})
    return {"enabled": bool(u.get("totp_enabled")), "recovery_codes_left": len(u.get("recovery_hashes") or [])}


@router.post("/auth/2fa/setup")
async def twofa_setup(request: Request, a=Depends(admin_user)):
    u = await db.users.find_one({"_id": ObjectId(a["id"])})
    if u.get("totp_enabled"):
        raise HTTPException(409, "Two-factor authentication is already enabled. Disable it first to re-enrol.")
    secret = SEC.new_totp_secret()
    await db.users.update_one({"_id": u["_id"]}, {"$set": {"totp_pending_enc": SEC.encrypt_secret(secret), "totp_pending_at": iso(now_utc())}})
    uri = SEC.totp_uri(secret, u.get("email"))
    return {"secret": secret, "uri": uri, "qr_svg": SEC.qr_svg(uri),
            "note": "Scan the QR code (or type the secret) in Google Authenticator, Microsoft Authenticator or Authy, then enter the 6-digit code."}


@router.post("/auth/2fa/enable")
async def twofa_enable(body: CodeIn, request: Request, a=Depends(admin_user)):
    u = await db.users.find_one({"_id": ObjectId(a["id"])})
    if not u.get("totp_pending_enc"):
        raise HTTPException(400, "Start setup first.")
    secret = SEC.decrypt_secret(u["totp_pending_enc"])
    if not SEC.totp_ok(secret, body.code):
        await SEC.log_event("2fa_enable_failed", request, u["email"], ok=False)
        raise HTTPException(400, "That code is not correct. Check the time on your phone and try again.")
    codes, hashes = SEC.new_recovery_codes()
    await db.users.update_one({"_id": u["_id"]}, {"$set": {"totp_enabled": True, "totp_secret_enc": SEC.encrypt_secret(secret),
                                                          "recovery_hashes": hashes, "totp_enabled_at": iso(now_utc())},
                                                 "$unset": {"totp_pending_enc": "", "totp_pending_at": ""}})
    await SEC.log_event("2fa_enabled", request, u["email"])
    return {"ok": True, "recovery_codes": codes,
            "message": "Two-factor authentication is on. Save these 8 recovery codes offline; each works once and they will not be shown again."}


@router.post("/auth/2fa/disable")
async def twofa_disable(body: DisableIn, request: Request, a=Depends(admin_user)):
    u = await db.users.find_one({"_id": ObjectId(a["id"])})
    if not u.get("totp_enabled"):
        return {"ok": True, "message": "Two-factor authentication is already off."}
    ok, _ = _check_second_factor(u, body.code)
    if not check_pw(body.password, u["password_hash"]) or not ok:
        await SEC.log_event("2fa_disable_failed", request, u["email"], ok=False)
        raise HTTPException(400, "Password or code is not correct.")
    await db.users.update_one({"_id": u["_id"]}, {"$set": {"totp_enabled": False},
                                                 "$unset": {"totp_secret_enc": "", "recovery_hashes": ""}})
    await SEC.log_event("2fa_disabled", request, u["email"])
    return {"ok": True, "message": "Two-factor authentication is off."}


async def ensure_indexes():
    await db.users.create_index("email", unique=True)
    await db.users.create_index("username", unique=True, partialFilterExpression={"username": {"$type": "string"}})
    await db.auth_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.rate_events.create_index("expires_at", expireAfterSeconds=0)
    await db.rate_events.create_index([("key", 1), ("at", -1)])
