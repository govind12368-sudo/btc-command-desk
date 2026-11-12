import os
import math
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from motor.motor_asyncio import AsyncIOMotorClient

IST = ZoneInfo("Asia/Kolkata")
# Brand name shown in emails, alerts, exports and the API title. Change it with the APP_NAME env variable.
APP_NAME = os.environ.get("APP_NAME", "Vantage BTC").strip() or "Vantage BTC"
APP_SLUG = "".join(c if c.isalnum() else "-" for c in APP_NAME.lower()).strip("-")
client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]

DEFAULT_SETTINGS = {
    "capital": 100000.0, "risk_fraction": 0.002, "max_order_notional": 10000.0, "leverage_cap": 5.0,
    "fee_per_side": 0.00059, "slippage_bps": 2.0, "funding_bps": 3.0, "max_spread_bps": 1.0,
    "max_quote_age_sec": 120, "daily_loss_limit": 800.0, "max_entries_per_day": 4, "max_positions": 2,
    "journal_capacity": 20, "scalp_stop": 0.005, "scalp_target_r": 1.5, "swing_stop": 0.02,
    "swing_target_r": 2.0, "min_scalp_volume_ratio": 0.9, "swing_volume_ratio": 1.0,
    "close_extreme_atr": 0.3, "min_candidate_checks": 9,
    # Scalp V2 (30-day Delta 5m review, Nov 2026): wider stop, nearer target, confirmation, no chasing.
    "scalp_min_net_r": 0.8, "swing_min_net_r": 1.0,
    "scalp_confirm_signals": 2, "scalp_confirm_lookback_min": 120, "scalp_max_ext_atr": 3.0,
    "max_open_model_signals": 1,
    # Execution V2: Delta India futures fees incl. 18% GST (0.02% maker, 0.05% taker). Slippage applies to taker legs only.
    # Scalp plan = post-only limit entry at the completed close (cancelled after N bars), limit take-profit, stop-market stop.
    "fee_maker_per_side": 0.000236, "fee_taker_per_side": 0.00059,
    "scalp_entry_mode": 1, "scalp_limit_offset_atr": 0.0, "scalp_limit_wait_bars": 2, "scalp_target_exit_limit": 1,
    # Data pipeline hygiene.
    "macro_score_max_age_h": 72, "snapshot_min_score": 0.15,
    # Access: new sign-ups need admin approval by default (open / approval / closed).
    "registration_mode": "approval",
    "critical_news_window_min": 180, "high_news_window_min": 360, "event_before_min": 60, "event_after_min": 30,
    "macro_mode": "publication", "strict_macro_age_days": 7, "macro_meta_max_age_min": 60,
    "usd_cap_h": 264, "y2_cap_h": 120, "brent_cap_h": 240,
    "trades_book_age_min": 5, "oi_funding_age_min": 10, "tech_candle_age_min": 15,
    "planner_hold_hours": 24, "planner_win_rate": 0.40, "conviction_scaling": False,
    "tranche_fractions": [0.5, 0.3, 0.2], "atr_offsets": [0, -0.5, -1],
    "upgrade_limit": 20, "upgrade_chars": 6000,
}


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    if dt is None:
        return None
    if isinstance(dt, (int, float)):
        dt = datetime.fromtimestamp(dt, timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(s):
    if not s:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def ist_date(dt=None):
    return (dt or now_utc()).astimezone(IST).strftime("%Y-%m-%d")


def ist_day_bounds(dt=None):
    d = (dt or now_utc()).astimezone(IST)
    start = d.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def age_sec(dt):
    if dt is None:
        return None
    return (now_utc() - parse_iso(dt)).total_seconds()


def finite(*xs):
    return all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in xs)


def tick_ceil(x, step):
    return round(math.ceil(x / step - 1e-9) * step, 10)


def tick_floor(x, step):
    return round(math.floor(x / step + 1e-9) * step, 10)


def clean(doc):
    if doc is None:
        return None
    d = dict(doc)
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    d.pop("password_hash", None)
    return d


async def get_settings():
    doc = await db.settings.find_one({"_id": "global"})
    if not doc:
        doc = {"_id": "global", "values": DEFAULT_SETTINGS, "version": 1, "updated_at": iso(now_utc())}
        await db.settings.insert_one(doc)
    doc = await _migrate_scalp_v2(doc)
    doc = await _migrate_exec_v2(doc)
    vals = {**DEFAULT_SETTINGS, **doc["values"]}
    return vals, doc["version"]


# Settings saved before Scalp V2 still hold the old 0.3% / 2.33R defaults. Move them to the V2
# defaults once, only if the admin never changed them. The admin can set them back on the Settings page.
_SCALP_V1 = {"scalp_stop": 0.003, "scalp_target_r": 2.33}
_SCALP_V2 = {"scalp_stop": 0.005, "scalp_target_r": 1.5}


async def _migrate_scalp_v2(doc):
    if (doc.get("migrations") or {}).get("scalp_v2"):
        return doc
    vals = doc.get("values") or {}
    untouched = all(vals.get(k) == v for k, v in _SCALP_V1.items())
    new_vals = {**vals, **_SCALP_V2} if untouched else vals
    upd = {"$set": {"values": new_vals, "migrations.scalp_v2": {"at": iso(now_utc()), "applied": untouched, "previous": {k: vals.get(k) for k in _SCALP_V1}}}}
    if untouched:
        upd["$inc"] = {"version": 1}
    r = await db.settings.update_one({"_id": "global", "version": doc["version"]}, upd)
    if r.modified_count:
        doc = {**doc, "values": new_vals, "version": doc["version"] + (1 if untouched else 0),
               "migrations": {**(doc.get("migrations") or {}), "scalp_v2": True}}
    return doc


# Execution V2: the old flat fee default (0.05%) ignored 18% GST. Move it to 0.059% once, only if never changed.
async def _migrate_exec_v2(doc):
    if (doc.get("migrations") or {}).get("exec_v2"):
        return doc
    vals = doc.get("values") or {}
    untouched = vals.get("fee_per_side") == 0.0005
    new_vals = {**vals, "fee_per_side": 0.00059} if untouched else vals
    upd = {"$set": {"values": new_vals, "migrations.exec_v2": {"at": iso(now_utc()), "applied": untouched, "previous": {"fee_per_side": vals.get("fee_per_side")}}}}
    if untouched:
        upd["$inc"] = {"version": 1}
    r = await db.settings.update_one({"_id": "global", "version": doc["version"]}, upd)
    if r.modified_count:
        doc = {**doc, "values": new_vals, "version": doc["version"] + (1 if untouched else 0),
               "migrations": {**(doc.get("migrations") or {}), "exec_v2": True}}
    return doc
