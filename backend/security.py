"""Security layer: response headers, request size limits, API rate limits, generic error handler,
security-event log, CSV formula escaping, two-factor (TOTP) helpers and startup configuration warnings.

Everything here is additive: no trading logic lives in this module."""
import base64
import hashlib
import io
import logging
import os
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import timedelta

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

from core import db, now_utc, iso, APP_NAME

log = logging.getLogger("security")

# ---------------------------------------------------------------- headers
SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def apply_headers(path, headers):
    for k, v in SECURITY_HEADERS.items():
        headers.setdefault(k, v)
    if path.startswith("/api/"):
        headers["Cache-Control"] = "no-store"
        headers.setdefault("Pragma", "no-cache")


# ---------------------------------------------------------------- body size
MAX_BODY = 1_000_000
MAX_BODY_IMPORT = 2_500_000  # /api/fills/import accepts a 2 MB CSV inside JSON


def body_limit(path):
    if path.startswith("/api/import/legacy"):
        return 20_000_000  # one-time admin data import from the old desk
    return MAX_BODY_IMPORT if path.startswith("/api/fills/import") else MAX_BODY


def body_too_large(request: Request):
    if request.method not in ("POST", "PUT", "PATCH"):
        return False
    cl = request.headers.get("content-length")
    if cl is None:
        return False
    try:
        return int(cl) > body_limit(request.url.path)
    except ValueError:
        return True


# ---------------------------------------------------------------- rate limits (in-memory sliding window)
RATE_PER_USER = 300   # requests per minute per signed-in user
RATE_PER_IP = 600     # requests per minute per client IP
RATE_EXEMPT = ("/api/health", "/api/cron/")


class SlidingWindow:
    def __init__(self, limit, window=60.0):
        self.limit, self.window = limit, window
        self.hits = defaultdict(deque)

    def hit(self, key, now=None):
        """Record a hit. Returns 0 if allowed, else seconds until the oldest hit leaves the window."""
        now = time.monotonic() if now is None else now
        q = self.hits[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return max(1, int(q[0] + self.window - now) + 1)
        q.append(now)
        if len(self.hits) > 50_000:  # bound memory: drop idle keys
            for k in [k for k, v in self.hits.items() if not v][:10_000]:
                self.hits.pop(k, None)
        return 0


USER_WINDOW = SlidingWindow(RATE_PER_USER)
IP_WINDOW = SlidingWindow(RATE_PER_IP)


def request_ip(request: Request):
    # Same rule as accounts.client_ip: the ingress appends the real client address as the LAST X-Forwarded-For hop.
    xff = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    return xff[-1] if xff else (request.client.host if request.client else "x")


def token_subject(request: Request):
    """User id from the access token, used ONLY as a rate-limit key (signature still checked, expiry ignored)."""
    tok = request.cookies.get("access_token")
    h = request.headers.get("Authorization", "")
    if not tok and h.startswith("Bearer "):
        tok = h[7:]
    if not tok:
        return None
    try:
        return jwt.decode(tok, os.environ["JWT_SECRET"], algorithms=["HS256"], options={"verify_exp": False}).get("sub")
    except Exception:
        return None


def rate_check(request: Request):
    path = request.url.path
    if not path.startswith("/api/") or path.startswith(RATE_EXEMPT):
        return 0
    wait = IP_WINDOW.hit(f"ip:{request_ip(request)}")
    if wait:
        return wait
    sub = token_subject(request)
    return USER_WINDOW.hit(f"user:{sub}") if sub else 0


def too_many(wait):
    return JSONResponse({"detail": "Too many requests. Please slow down."}, status_code=429, headers={"Retry-After": str(wait)})


# ---------------------------------------------------------------- generic error handler
async def unhandled_error(request: Request, exc: Exception):
    ref = uuid.uuid4().hex[:10]
    log.exception(f"unhandled error ref={ref} {request.method} {request.url.path}")
    return JSONResponse({"detail": "Internal error", "ref": ref}, status_code=500)


# ---------------------------------------------------------------- security events (90-day TTL)
EVENT_TTL_DAYS = 90


async def log_event(kind, request: Request = None, user=None, detail=None, ok=True):
    try:
        doc = {"kind": kind, "ok": bool(ok), "at": iso(now_utc()), "expires_at": now_utc() + timedelta(days=EVENT_TTL_DAYS),
               "detail": (str(detail)[:300] if detail else None)}
        if request is not None:
            doc["ip"] = request_ip(request)
            doc["user_agent"] = (request.headers.get("user-agent") or "")[:200]
        if user:
            doc["user"] = user.get("email") if isinstance(user, dict) else str(user)
        await db.security_events.insert_one(doc)
    except Exception:
        log.warning("security event not recorded", exc_info=True)


async def ensure_indexes():
    await db.security_events.create_index("expires_at", expireAfterSeconds=0)
    await db.security_events.create_index([("at", -1)])
    await db.sessions.create_index("expires_at", expireAfterSeconds=0)
    await db.sessions.create_index("user_id")
    await db.login_ip_failures.create_index("expires_at", expireAfterSeconds=0)
    await db.login_ip_failures.create_index("ip")


# ---------------------------------------------------------------- CSV formula injection
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(v):
    """Prefix text cells that a spreadsheet would run as a formula. Numbers (including negatives) stay numbers."""
    if isinstance(v, str) and v.startswith(_FORMULA_START):
        try:
            float(v)
            return v
        except ValueError:
            return "'" + v
    return v


# ---------------------------------------------------------------- two-factor (TOTP) helpers
def _fernet():
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(hashlib.sha256(("2fa:" + os.environ["JWT_SECRET"]).encode()).digest())
    return Fernet(key)


def encrypt_secret(s):
    return _fernet().encrypt(s.encode()).decode()


def decrypt_secret(s):
    return _fernet().decrypt(s.encode()).decode()


def new_totp_secret():
    import pyotp
    return pyotp.random_base32()


def totp_uri(secret, account):
    import pyotp
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=APP_NAME)


def totp_ok(secret, code):
    import pyotp
    code = (code or "").strip().replace(" ", "")
    return code.isdigit() and len(code) == 6 and pyotp.TOTP(secret).verify(code, valid_window=1)


def qr_svg(text):
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def hash_code(code):
    return hashlib.sha256(("rc:" + code.strip().lower().replace("-", "")).encode()).hexdigest()


def new_recovery_codes(n=8):
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]
    return codes, [hash_code(c) for c in codes]


# ---------------------------------------------------------------- startup configuration warnings
def config_warnings():
    """Weak secrets are reported (log + admin banner) instead of refusing to start, so a live site is never taken down."""
    from auth_core import password_problems
    out = []
    if len(os.environ.get("JWT_SECRET", "")) < 32:
        out.append("JWT_SECRET is shorter than 32 characters. Replace it with a long random value in the server environment.")
    pw = os.environ.get("ADMIN_PASSWORD", "")
    if pw and password_problems(pw, "admin", os.environ.get("ADMIN_EMAIL", "")):
        out.append("ADMIN_PASSWORD in the server environment is weak. It is only used to create the admin account the first time; "
                   "make sure the live admin password has been changed, and set a strong ADMIN_PASSWORD.")
    for w in out:
        log.warning("SECURITY CONFIG: " + w)
    return out


# ---------------------------------------------------------------- state filtering for non-admin users
ADMIN_ONLY_STATE_KEYS = ("limits", "settings_version", "settings_errors", "recovery", "ws", "user")


def _redact_checks(ev):
    """F33 detail contains today's entries and realized P&L; viewers only see pass/fail."""
    if not isinstance(ev, dict) or not isinstance(ev.get("checks"), list):
        return ev
    ev = dict(ev)
    ev["checks"] = [({**c, "detail": "daily risk limits " + ("within limits" if c.get("status") == "PASS" else "reached")}
                     if c.get("id") == "F33" else c) for c in ev["checks"]]
    return ev


def public_state(state, sources, recovery=None):
    """Copy of the dashboard state for viewers: no P&L, no limits, no internals, no source URLs or error text."""
    out = {k: v for k, v in state.items() if k not in ADMIN_ONLY_STATE_KEYS}
    out["ws"] = {"status": (state.get("ws") or {}).get("status")}
    out["recovery"] = {"state": (recovery or {}).get("state")}
    for k in ("scalp", "swing"):
        if k in out:
            out[k] = _redact_checks(out[k])
    out["sources"] = [{k: s.get(k) for k in ("key", "name", "family", "status", "last_success")} for s in (sources or [])]
    rv = out.get("review")
    if isinstance(rv, dict):
        out["review"] = {k: rv.get(k) for k in ("ok", "status", "advisory", "affects_decision", "affects_direction")}
    return out
