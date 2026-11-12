from dotenv import load_dotenv
load_dotenv()

import os
import io
import csv
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional
import bcrypt
import jwt
from bson import ObjectId
from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends, Query
from fastapi.responses import PlainTextResponse
from starlette.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from core import db, now_utc, iso, parse_iso, clean, get_settings, DEFAULT_SETTINGS, IST, finite, ist_date, APP_NAME, APP_SLUG
import sources
from sources import REGISTRY, WS
import engine
import recovery
import validation as VA
import journal as JN
import planner as PL
import insights
import alert_mode
from strategy import sided_ok
from urllib.parse import urlparse
import uuid


def oid(x):
    try:
        return ObjectId(x)
    except Exception:
        raise HTTPException(404, "record not found")

logging.basicConfig(level=logging.INFO)
app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None)
api = APIRouter(prefix="/api")
JWT_ALG = "HS256"
FRONTEND_URL = os.environ["FRONTEND_URL"]


# ---------------- auth (see auth_core.py / accounts.py) ----------------
from auth_core import hash_pw, check_pw, current_user, admin_user
import accounts
import security as SEC


# ---------------- live state ----------------
def sources_public():
    out = [s.public() for s in REGISTRY.values()]
    out.append({"key": "delta_ws", "family": "delta", "name": "Delta public WebSocket (ticker, L2 book, trades)", "instrument": "BTCUSD perpetual",
                "unit": "USD / contracts", "url": sources.DELTA_WS, "status": "ok" if WS["status"] == "connected" else "error",
                "error": WS.get("error") if WS["status"] != "connected" else None, "last_success": WS.get("last_msg"), "last_attempt": WS.get("connected_at"),
                "interval_sec": 0, "freshness_sec": 60, "fail_count": WS.get("reconnects", 0)})
    return out


def rec_public():
    return {k: v for k, v in recovery.RECOVERY.items() if k != "ws_task"}


@api.get("/state")
async def state(u=Depends(current_user)):
    srcs = sources_public()
    if u["role"] != "admin":
        pub = SEC.public_state(engine.STATE, srcs, rec_public())
        return {**pub, "brief": insights.brief(pub, pub.get("sources")), "server_time": iso(now_utc()), "user": u}
    return {**engine.STATE, "sources": srcs, "recovery": rec_public(), "server_time": iso(now_utc()), "user": u,
            "brief": insights.brief(engine.STATE, srcs), "config_warnings": CONFIG_WARNINGS,
            "alert_mode_armed": (await alert_mode.get_mode())["effective_armed"],
            "monitoring_note": "Data adapters, calculations, swing-plan monitoring, auto-capture and the daily recap timer run inside the server 24/7 — your own PC or browser can be off."}


@api.get("/candles")
async def candles(tf: str = "5m", u=Depends(current_user)):
    c = REGISTRY["delta_candles"].data or {}
    if tf not in c:
        raise HTTPException(404, "timeframe unavailable")
    return {"tf": tf, "bars": c[tf]["bars"][-150:], "metrics": engine.frames().get(tf, {}), "retrieved": iso(REGISTRY["delta_candles"].last_success),
            "future_rejected": c[tf]["future_rejected"], "note": "Completed candles only; t = candle opening time (UTC)."}


@api.get("/market")
async def market(u=Depends(current_user)):
    p = REGISTRY["delta_product"]
    p.active_until = now_utc() + timedelta(seconds=60)
    live = WS["status"] == "connected" and WS.get("book")
    book = WS.get("book") if live else (REGISTRY["delta_ticker"].data or {}).get("book")
    ok = REGISTRY["okx"].data or {}
    return {"book": book and {"bids": book["bids"][:15], "asks": book["asks"][:15], "event_time": book.get("event_time"), "source": "WebSocket" if live else "REST backup"},
            "trades": [{**t, "time": iso(t["ts"])} for t in WS["trades"][-40:][::-1]], "product": p.data, "product_retrieved": iso(p.last_success),
            "ticker": engine.STATE.get("ticker"), "quote": engine.STATE.get("quote"), "ws": engine.STATE.get("ws"),
            "okx": {k: ok.get(k) for k in ("instrument", "last", "bid", "ask", "ticker_time", "oi_btc", "oi_usd", "oi_time", "funding_fraction", "next_funding_time", "funding_history", "long_short_account_ratio", "ct_val_btc", "settle")},
            "oi_history": [x async for x in db.oi_history.find({}, {"_id": 0}).sort("t", -1).limit(60)],
            "scanner_note": "Contract specification is rechecked about every 15 s while this page is open."}


@api.get("/oi-history")
async def oi_history(hours: int = Query(24, ge=1, le=168), venue: str = "OKX", u=Depends(current_user)):
    """Stored OI/funding snapshots for charts (oldest first, at most ~300 points)."""
    since = iso(now_utc() - timedelta(hours=hours))
    rows = [x async for x in db.oi_history.find({"venue": venue, "t": {"$gte": since}}, {"_id": 0, "t": 1, "oi_btc": 1, "oi_usd": 1, "funding_fraction": 1}).sort("t", 1)]
    step = max(1, len(rows) // 300)
    return {"venue": venue, "hours": hours, "points": rows[::step]}


# ---------------- settings ----------------
class VersionedIn(BaseModel):
    version: int
    values: dict


@api.get("/settings")
async def settings_get(u=Depends(admin_user)):
    v, ver = await get_settings()
    return {"values": v, "version": ver, "defaults": DEFAULT_SETTINGS}


@api.put("/settings")
async def settings_put(body: VersionedIn, u=Depends(admin_user)):
    cur, ver = await get_settings()
    if body.version != ver:
        raise HTTPException(409, f"Settings changed elsewhere (server version {ver}). Reload to merge; your draft is kept.")
    new = dict(cur)
    for k, v in body.values.items():
        if k not in DEFAULT_SETTINGS:
            continue
        d = DEFAULT_SETTINGS[k]
        if isinstance(d, bool):
            new[k] = bool(v)
        elif isinstance(d, (int, float)):
            try:
                x = float(v)
            except (TypeError, ValueError):
                raise HTTPException(422, f"{k} must be a number")
            if not finite(x):
                raise HTTPException(422, f"{k} must be finite")
            new[k] = x
        elif isinstance(d, list):
            if not isinstance(v, list) or len(v) != len(d):
                raise HTTPException(422, f"{k} must be a list of {len(d)} numbers")
            new[k] = [float(x) for x in v]
        elif k == "registration_mode":
            if v not in ("open", "approval", "closed"):
                raise HTTPException(422, "registration_mode must be open, approval or closed")
            new[k] = v
        elif k == "macro_mode":
            if v not in ("publication", "strict"):
                raise HTTPException(422, "macro_mode must be publication or strict")
            new[k] = v
    r = await db.settings.update_one({"_id": "global", "version": ver}, {"$set": {"values": new, "updated_at": iso(now_utc()), "updated_by": u["email"]}, "$inc": {"version": 1}})
    if not r.modified_count:
        raise HTTPException(409, "Concurrent update — reload")
    changed = sorted(k for k in new if new.get(k) != cur.get(k))
    await SEC.log_event("settings_changed", None, u["email"], detail=", ".join(changed)[:300] or "no change")
    return {"values": new, "version": ver + 1}


# ---------------- daily review (F31) ----------------
class ReviewIn(BaseModel):
    version: int = 0
    risk: Optional[bool] = None
    news: Optional[bool] = None
    venue: Optional[bool] = None
    journal: Optional[bool] = None
    event_decision: Optional[str] = None
    flow_decision: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=2000)


@api.get("/review")
async def review_get(u=Depends(current_user)):
    d = await db.reviews.find_one({"_id": ist_date()}) or {"_id": ist_date(), "version": 0}
    return {**clean(d), "status": engine.STATE.get("review")}


@api.put("/review")
async def review_put(body: ReviewIn, u=Depends(admin_user)):
    day = ist_date()
    cur = await db.reviews.find_one({"_id": day}) or {"version": 0}
    cv = cur.get("version", 0)
    if body.version != cv:
        raise HTTPException(409, "Review changed elsewhere — reload")
    upd = {}
    for k in ("risk", "news", "venue", "journal"):
        v = getattr(body, k)
        if v is not None:
            upd[k] = {"ok": v, "at": iso(now_utc()), "by": u["email"]}
    for k in ("event_decision", "flow_decision"):
        v = getattr(body, k)
        if v is not None:
            if v not in ("PASS", "REVIEW", "BLOCKED"):
                raise HTTPException(422, f"{k} must be PASS, REVIEW or BLOCKED")
            upd[k], upd[k + "_at"] = v, iso(now_utc())
    if body.note is not None:
        upd["note"] = body.note[:1000]
    upd["version"] = cv + 1
    if cv:
        r = await db.reviews.update_one({"_id": day, "version": cv}, {"$set": upd})
        if not r.matched_count:
            raise HTTPException(409, "Review changed elsewhere — reload")
    else:
        await db.reviews.update_one({"_id": day}, {"$set": upd}, upsert=True)
    engine._REVIEW_NEXT_REFRESH = 0.0
    return clean(await db.reviews.find_one({"_id": day}))


# ---------------- news & events ----------------
@api.get("/news")
async def news(category: Optional[str] = None, impact: Optional[str] = None, page: int = 1, size: int = 30, u=Depends(current_user)):
    q = {}
    if category:
        q["categories"] = category
    if impact:
        q["impact"] = impact
    total = await db.news_items.count_documents(q)
    items = [clean(x) async for x in db.news_items.find(q).sort([("published_at", -1)]).skip((page - 1) * size).limit(size)]
    errors = [clean(x) async for x in db.news_items.find({"time_error": {"$ne": None}}).limit(20)]
    return {"items": items, "total": total, "page": page, "size": size, "time_errors": errors,
            "classification_note": "Category, impact and keyword direction are automatic keyword classification — not a verified interpretation of the event.",
            "feeds": [s.public() for s in REGISTRY.values() if s.family == "news"]}


class NewsReviewIn(BaseModel):
    status: str


@api.put("/news/{nid}/review")
async def news_review(nid: str, body: NewsReviewIn, u=Depends(admin_user)):
    if body.status not in ("UNREVIEWED", "REVIEWED", "IGNORED"):
        raise HTTPException(422, "invalid status")
    await db.news_items.update_one({"_id": nid}, {"$set": {"review_status": body.status, "reviewed_by": u["email"], "reviewed_at": iso(now_utc())}})
    return {"ok": True}


class EventIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    scheduled_ist: str
    impact: str
    source_url: str = Field(min_length=8, max_length=500)
    reviewed: bool = False
    notes: Optional[str] = Field(default=None, max_length=2000)


@api.get("/events")
async def events(u=Depends(current_user)):
    calendar = REGISTRY["bls"].data or {}
    return {"manual": [clean(x) async for x in db.manual_events.find().sort("scheduled_utc", 1)], "calendar": REGISTRY["bls"].public(),
            "calendar_data": calendar.get("events", [])[:100],
            "calendar_coverage": {k: v for k, v in calendar.items() if k != "events"},
            "note": "Official free US schedules: BLS, BEA and Federal Reserve. Coverage is limited to these publishers; forecasts are not supplied. Failed or partial coverage means event risk is unknown."}


def _ev_doc(b: EventIn, u):
    if b.impact not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        raise HTTPException(422, "impact must be LOW, MEDIUM, HIGH or CRITICAL")
    if not b.source_url.startswith(("http://", "https://")):
        raise HTTPException(422, "source URL must start with http(s)://")
    try:
        t = datetime.fromisoformat(b.scheduled_ist).replace(tzinfo=IST)
    except ValueError:
        raise HTTPException(422, "scheduled_ist must be YYYY-MM-DDTHH:MM")
    return {**b.model_dump(), "scheduled_utc": iso(t), "updated_by": u["email"], "updated_at": iso(now_utc())}


@api.post("/events")
async def ev_create(b: EventIn, u=Depends(admin_user)):
    d = _ev_doc(b, u) | {"version": 1}
    r = await db.manual_events.insert_one(d)
    return clean(await db.manual_events.find_one({"_id": r.inserted_id}))


@api.put("/events/{eid}")
async def ev_update(eid: str, b: EventIn, u=Depends(admin_user)):
    await db.manual_events.update_one({"_id": oid(eid)}, {"$set": _ev_doc(b, u), "$inc": {"version": 1}})
    return clean(await db.manual_events.find_one({"_id": oid(eid)}))


@api.delete("/events/{eid}")
async def ev_delete(eid: str, u=Depends(admin_user)):
    r = await db.manual_events.delete_one({"_id": oid(eid)})
    if not r.deleted_count:
        raise HTTPException(404, "record not found")
    return {"ok": True}


# ---------------- swing plans ----------------
@api.get("/plans")
async def plans(u=Depends(current_user)):
    return {"active": [clean(x) async for x in db.swing_plans.find()],
            "archive": [clean(x) async for x in db.plan_archive.find().sort("archived_at", -1).limit(100)]}


@api.post("/plans/{pid}/cancel")
async def plan_cancel(pid: str, u=Depends(admin_user)):
    p = await db.swing_plans.find_one({"_id": pid})
    if not p:
        raise HTTPException(404, "plan not active")
    await engine.archive_plan(p, "CANCELLED", f"cancelled by {u['email']}")
    return {"ok": True}


# ---------------- journals ----------------
@api.get("/signals")
async def signals(page: int = 1, size: int = 25, u=Depends(current_user)):
    total = await db.signals.count_documents({})
    rows = [clean(x) async for x in db.signals.find().sort("captured_at", -1).skip((page - 1) * size).limit(size)]
    return {"items": rows, "total": total, "page": page, "size": size,
            "note": "Model signals are automatic captures. Candle touches are observations — never actual exchange fills."}


class FillIn(BaseModel):
    version: Optional[int] = None
    strategy: str
    direction: str
    entry_time: str
    entry_price: float
    qty_btc: float
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    fees_usd: Optional[float] = None
    funding_usd: Optional[float] = None
    stop_price: Optional[float] = None
    exchange_id: Optional[str] = None
    plan_id: Optional[str] = None
    signal_id: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


def _fill_validate(b: FillIn):
    if b.strategy not in ("scalp", "swing", "other"):
        raise HTTPException(422, "strategy must be scalp, swing or other")
    if b.direction not in ("LONG", "SHORT"):
        raise HTTPException(422, "direction must be LONG or SHORT")
    for k in ("entry_price", "qty_btc"):
        v = getattr(b, k)
        if not finite(v) or v <= 0:
            raise HTTPException(422, f"{k} must be positive")
    if b.exit_price is not None and (not finite(b.exit_price) or b.exit_price <= 0):
        raise HTTPException(422, "exit_price must be positive")
    if (b.exit_price is None) != (b.exit_time in (None, "")):
        raise HTTPException(422, "exit price and exit time must both be provided")
    d = b.model_dump(exclude={"version"})
    for k in ("entry_time", "exit_time"):
        v = d.get(k)
        if v:
            try:
                t = datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(422, f"{k} must be ISO 8601 with timezone")
            if t.tzinfo is None:
                raise HTTPException(422, f"{k} must include a timezone")
            if t > now_utc() + timedelta(minutes=1):
                raise HTTPException(422, f"{k} is in the future")
            d[k] = iso(t)
    if d.get("exit_time") and d["exit_time"] < d["entry_time"]:
        raise HTTPException(422, "exit time is before entry time")
    return d


@api.get("/fills")
async def fills(u=Depends(admin_user)):
    s, _ = await get_settings()
    rows = [clean(x) async for x in db.fills.find().sort("entry_time", -1)]
    q = engine.STATE.get("quote") or {}
    mark = (q["bid"] + q["ask"]) / 2 if q.get("bid") and q.get("ask") else None
    for r in rows:
        r["pnl"] = JN.fill_pnl(r, mark)
    open_n = sum(1 for r in rows if r.get("exit_price") is None)
    cap = int(s["journal_capacity"])
    return {"items": rows, "stats": JN.stats(rows, s["capital"]), "mark": mark,
            "capacity": {"open": open_n, "limit": cap, "total_records": len(rows),
                         "notice": f"Working capacity reached ({open_n}/{cap} open records). History is never discarded — export or archive anytime." if open_n >= cap else None}}


@api.post("/fills")
async def fill_create(b: FillIn, u=Depends(admin_user)):
    d = _fill_validate(b)
    if d.get("exchange_id") and await db.fills.find_one({"exchange_id": d["exchange_id"]}):
        raise HTTPException(409, "A fill with this exchange ID already exists")
    d.update(version=1, created_at=iso(now_utc()), created_by=u["email"], source="manual")
    r = await db.fills.insert_one(d)
    if d.get("plan_id"):
        p = await db.swing_plans.find_one({"_id": d["plan_id"]})
        if p:
            await engine.archive_plan(p, "CONSUMED", f"actual execution recorded ({r.inserted_id})")
    return clean(await db.fills.find_one({"_id": r.inserted_id}))


@api.put("/fills/{fid}")
async def fill_update(fid: str, b: FillIn, u=Depends(admin_user)):
    d = _fill_validate(b)
    r = await db.fills.update_one({"_id": oid(fid), "version": b.version}, {"$set": {**d, "manual_edit": True, "updated_at": iso(now_utc()), "updated_by": u["email"]}, "$inc": {"version": 1}})
    if not r.matched_count:
        raise HTTPException(409, "This record was changed elsewhere — reload; your draft is kept")
    return clean(await db.fills.find_one({"_id": oid(fid)}))


@api.delete("/fills/{fid}")
async def fill_delete(fid: str, u=Depends(admin_user)):
    doc = await db.fills.find_one({"_id": oid(fid)})
    if not doc:
        raise HTTPException(404, "record not found")
    if doc:
        doc["deleted_at"], doc["deleted_by"] = iso(now_utc()), u["email"]
        await db.fills_archive.insert_one(doc)
        await db.fills.delete_one({"_id": oid(fid)})
        await SEC.log_event("fill_deleted", None, u["email"], detail=fid)
    return {"ok": True, "note": "moved to fills archive"}


class CsvIn(BaseModel):
    csv_text: str = Field(max_length=2_000_000)


@api.post("/fills/import")
async def fill_import(b: CsvIn, u=Depends(admin_user)):
    await SEC.log_event("fills_import", None, u["email"], detail=f"{len(b.csv_text)} chars")
    rd = csv.DictReader(io.StringIO(b.csv_text.strip()))
    added, dup, errors = 0, 0, []
    for i, row in enumerate(rd, 2):
        try:
            num = lambda k: float(row[k]) if row.get(k) not in (None, "") else None
            fi = FillIn(strategy=row.get("strategy") or "other", direction=(row.get("direction") or "").upper(), entry_time=row.get("entry_time") or "",
                        entry_price=num("entry_price"), qty_btc=num("qty_btc"), exit_time=row.get("exit_time") or None, exit_price=num("exit_price"),
                        fees_usd=num("fees_usd"), funding_usd=num("funding_usd"), stop_price=num("stop_price"),
                        exchange_id=row.get("exchange_id") or None, notes=row.get("notes"))
            d = _fill_validate(fi)
            if d.get("exchange_id") and await db.fills.find_one({"exchange_id": d["exchange_id"]}):
                dup += 1
                continue
            d.update(version=1, created_at=iso(now_utc()), created_by=u["email"], source="csv")
            await db.fills.insert_one(d)
            added += 1
        except HTTPException as e:
            errors.append(f"row {i}: {e.detail}")
        except Exception as e:
            errors.append(f"row {i}: {type(e).__name__}")
    return {"added": added, "duplicates_skipped": dup, "errors": errors[:50]}


def _csv(rows, cols):
    buf = io.StringIO()
    w = csv.writer(buf)
    tcols = [c for c in cols if c.endswith(("_time", "_at", "_open"))]
    w.writerow(cols + [c + "_IST" for c in tcols])
    for r in rows:
        ist = []
        for c in tcols:
            try:
                ist.append(parse_iso(r.get(c)).astimezone(IST).isoformat() if r.get(c) else "")
            except Exception:
                ist.append("")
        w.writerow([SEC.csv_safe(r.get(c, "")) if not isinstance(r.get(c), (dict, list)) else "" for c in cols] + ist)
    return buf.getvalue()


@api.get("/export/{kind}")
async def export(kind: str, u=Depends(admin_user)):
    spec = {"fills": (db.fills, ["id", "exchange_id", "strategy", "direction", "entry_time", "entry_price", "qty_btc", "exit_time", "exit_price", "fees_usd", "funding_usd", "stop_price", "plan_id", "signal_id", "notes"]),
            "signals": (db.signals, ["id", "strategy", "direction", "confirm_bar_open", "entry", "stop", "target", "qty_btc", "net_r", "captured_at", "quote_time", "observation", "observed_at"]),
            "validation": (db.validation_snapshots, ["id", "model", "direction", "score", "capture_time", "quote_time", "entry", "stop", "target", "stop_fraction", "target_ratio", "outcome", "outcome_at", "frozen"]),
            "plans": (db.plan_archive, ["id", "direction", "entry", "stop", "target", "qty_btc", "atr", "breakout_ref", "captured_at", "expires_at", "status", "status_reason", "archived_at"])}
    if kind not in spec:
        raise HTTPException(404, "unknown export")
    coll, cols = spec[kind]
    await SEC.log_event("export", None, u["email"], detail=kind)
    rows = [clean(x) async for x in coll.find()]
    return PlainTextResponse(_csv(rows, cols), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={APP_SLUG}-{kind}-{now_utc().strftime('%Y%m%dT%H%MZ')}.csv"})


# ---------------- validation ----------------
@api.get("/validation")
async def validation(page: int = 1, size: int = 25, model: Optional[str] = None, u=Depends(current_user)):
    q = {"model": model} if model else {}
    total = await db.validation_snapshots.count_documents(q)
    rows = [clean(x) async for x in db.validation_snapshots.find(q).sort("capture_ts", -1).skip((page - 1) * size).limit(size)]
    agg = {}
    async for x in db.validation_snapshots.aggregate([{"$group": {"_id": {"m": "$model", "o": "$outcome"}, "n": {"$sum": 1}}}]):
        agg.setdefault(x["_id"]["m"], {})[x["_id"]["o"]] = x["n"]
    return {"items": rows, "total": total, "page": page, "size": size, "history": VA.HIST, "outcomes_by_model": agg,
            "backtest": VA.BACKTEST, "note": "Outcome times are candle-end observations, not exchange execution times."}


class SnapIn(BaseModel):
    version: Optional[int] = None
    direction: str
    entry: float
    stop: float
    target: float
    note: Optional[str] = Field(default=None, max_length=2000)


@api.post("/validation")
async def snap_create(b: SnapIn, u=Depends(admin_user)):
    if b.direction not in ("LONG", "SHORT"):
        raise HTTPException(422, "direction must be LONG or SHORT")
    if not sided_ok(b.direction, b.entry, b.stop, b.target):
        raise HTTPException(422, "levels are missing, non-positive or incorrectly sided")
    q = engine.STATE.get("quote") or {}
    ts = now_utc()
    sid = f"MANUAL-{int(ts.timestamp())}-{uuid.uuid4().hex[:6]}"
    await db.validation_snapshots.insert_one({"_id": sid, "snapshot_id": sid, "model": "MANUAL", "auto": False, "capture_time": iso(ts), "capture_ts": int(ts.timestamp()),
                                              "quote_time": q.get("event_time"), "direction": b.direction, "score": None, "entry": b.entry, "stop": b.stop, "target": b.target,
                                              "venue": "Delta Exchange India", "symbol": "BTCUSD", "stop_fraction": abs(b.entry - b.stop) / b.entry,
                                              "target_ratio": abs(b.target - b.entry) / abs(b.entry - b.stop), "outcome": "PENDING", "frozen": False, "version": 1, "note": b.note})
    return clean(await db.validation_snapshots.find_one({"_id": sid}))


@api.put("/validation/{sid}")
async def snap_update(sid: str, b: SnapIn, u=Depends(admin_user)):
    if b.direction not in ("LONG", "SHORT"):
        raise HTTPException(422, "direction must be LONG or SHORT")
    if not sided_ok(b.direction, b.entry, b.stop, b.target):
        raise HTTPException(422, "levels are missing, non-positive or incorrectly sided")
    r = await db.validation_snapshots.update_one({"_id": sid, "version": b.version}, {"$set": {
        "entry": b.entry, "stop": b.stop, "target": b.target, "direction": b.direction, "outcome": "PENDING", "outcome_at": None, "frozen": False,
        "edited_at": iso(now_utc()), "edited_by": u["email"], "edit_note": "levels edited — previous result invalidated"}, "$inc": {"version": 1}})
    if not r.matched_count:
        raise HTTPException(409, "Snapshot changed elsewhere — reload")
    return clean(await db.validation_snapshots.find_one({"_id": sid}))


# ---------------- planner ----------------
class PlannerIn(BaseModel):
    direction: str = "LONG"
    entry_mode: str = "spot"
    limit_price: Optional[float] = None
    stop_fraction: Optional[float] = None
    target_r: Optional[float] = None


@api.post("/planner")
async def planner(b: PlannerIn, u=Depends(current_user)):
    if b.direction not in ("LONG", "SHORT") or b.entry_mode not in ("spot", "limit"):
        raise HTTPException(422, "direction LONG/SHORT and entry_mode spot/limit required")
    s, _ = await get_settings()
    bars = (REGISTRY["delta_candles"].data or {}).get("1d", {}).get("bars", [])
    return PL.plan(b.model_dump(), s, engine.STATE, bars, REGISTRY["delta_product"].data)


# ---------------- audit & recovery ----------------
SHEET_MAP = {"Trading Dashboard": "BTC Dashboard", "BTC Direction Validation": "Trade Validation", "Scalping": "Scalping Signal", "Swing": "Swing Signal",
             "Trade Journal": "Trade Journal", "System Audit": "System Audit", "BTC News": "News & Events", "Event": "News & Events → manual events",
             "Live macro": "Five Pillars → Macro", "OI + Funding": "Five Pillars → Positioning; Live Market", "Backtest": "Trade Validation → rule backtest",
             "Decision data": "BTC Dashboard → decision + readiness", "Five Pillar": "Five Pillars", "Dashboard": "BTC Dashboard (duplicate merged)",
             "Live Feed Setup": "System Audit → source registry", "Command Center": "BTC Dashboard → execution controls", "Market Monitor": "Live Market",
             "Contract Specs": "Live Market → contract scanner", "Trade Plan": "Trade Planner", "Pro Trading Desk": "BTC Dashboard + Live Market (merged)",
             "BTC Bias & Probability": "Five Pillars → original four/six-factor models", "Query1": "Server-side source adapters (replaces Power Query)",
             "Fund Controls": "Settings → risk controls", "News Impact": "News & Events → impact classification", "OI History": "Live Market → OI history"}
DEPENDENCIES = [
    {"output": "Scalping / Swing 11 active checks (F31 excluded)", "inputs": ["delta_candles", "delta_ws / delta_ticker", "delta_product", "settings", "fills"]},
    {"output": "Five pillars", "inputs": ["okx", "fed_h10 + fed_h10_meta", "fed_h15 + fed_h15_meta", "eia_brent + eia_meta"]},
    {"output": "Confirmed BTC direction", "inputs": ["five pillars (Money Flow, Liquidity, Technical required, ≥3/5)", "two consecutive completed 15m candles", "persisted direction_state + direction_snapshots"]},
    {"output": "Original four-factor", "inputs": ["macro (USD 1m, 2Y 5-obs, Brent)", "etf_bitbo", "funding_multi (proxy)", "fng"]},
    {"output": "Original six-factor", "inputs": ["five pillars (all current)", "four-factor (complete)"]},
    {"output": "Buy/Sell observation", "inputs": ["delta_ws trades + book", "delta_product contract value"]},
    {"output": "Forward validation + backtest", "inputs": ["history_5m (30 days Delta 5m)", "validation_snapshots"]},
]


@api.get("/audit")
async def audit(u=Depends(admin_user)):
    st = await db.command("dbstats")
    colls = {c: await db[c].count_documents({}) for c in ["settings", "reviews", "swing_plans", "plan_archive", "fills", "fills_archive", "signals", "validation_snapshots",
                                                          "manual_events", "news_items", "upgrade_requests", "repair_history", "history_5m", "oi_history", "source_cache", "direction_state", "direction_snapshots", "users"]}
    srcs = sources_public()
    return {"current_issues": [s for s in srcs if s["status"] != "ok"], "sources": srcs,
            "storage": {"ok": st.get("ok") == 1, "data_mb": round(st.get("dataSize", 0) / 1e6, 2), "collections": colls,
                        "limits": "Durable MongoDB storage. No record is silently discarded. Market history (history_5m) is stored separately from workspace state and pruned only beyond 37 days."},
            "release_schedules": engine.STATE.get("macro"), "dependencies": DEPENDENCIES,
            "workbook": workbook_report(),
            "historical_findings_note": "Historical workbook findings come from the attached workbook (cached values and formulas as saved). They are separate from the current application/source issues above.",
            "repair_history": [clean(x) async for x in db.repair_history.find().sort("at", -1).limit(50)], "recovery": rec_public(),
            "compute_error": engine.STATE.get("compute_error")}


import json as _json
_WB = None


def workbook_report():
    global _WB
    if _WB is None:
        p = os.path.join(os.path.dirname(__file__), "workbook_audit.json")
        if os.path.exists(p):
            with open(p) as fh:
                _WB = _json.load(fh)
    if not _WB:
        return {"inspected": False, "note": "No workbook audit file found.", "coverage": []}
    cov = []
    for s in _WB["sheets"]:
        notes = []
        if s["error_cells"]:
            notes.append(f"{s['error_cells']} cached error cells (#REF!/#N/A/#VALUE…) — corrected by server-side calculation")
        if s["backup_unique_rows"]:
            notes.append(f"{s['backup_unique_rows']} rows unique to backup — preserved in read-only archive")
        if s["state"] != "visible":
            notes.append("hidden sheet")
        cov.append({"sheet": s["sheet"], "backup": s["backup"], "app_feature": SHEET_MAP.get(s["sheet"], "—"), "state": s["state"], "dims": s["dims"],
                    "formulas": s["formulas"], "values": s["values"], "functions": ", ".join(s["top_functions"]),
                    "status": "implemented" + (" + corrected" if s["error_cells"] else ""), "findings": "; ".join(notes) or "no issues found"})
    bad_names = [n for n, t in _WB["defined_names"] if "#REF" in str(t)]
    return {"inspected": True, "file": _WB["file"], "inspected_at": _WB["inspected_at"], "sheet_count": _WB["sheet_count"],
            "hidden": _WB["hidden"], "defined_names": _WB["defined_names"], "queries": _WB["queries"],
            "note": f"Audited {_WB['sheet_count']} worksheets (25 primary + 25 backups named __bpstmp_*), including {len(_WB['hidden'])} hidden sheets. "
                    f"{len(_WB['queries'])} Power Query connections replaced by server-side adapters. Broken named ranges: {', '.join(bad_names) or 'none'} (replaced by fixed Delta adapter config). "
                    "Workbook instructions were treated as reference content only. The app runs without Excel.",
            "unavailable": ["Excel Power Query refresh schedules (replaced by adapter intervals)", "Conditional-formatting rules (not supported by the parser; visual only)"],
            "coverage": cov, "five_pillar_fields": _WB.get("five_pillar_fields", [])}


@api.get("/reference/sheets")
async def ref_sheets(u=Depends(admin_user)):
    rows = [x async for x in db.workbook_archive.aggregate([{"$group": {"_id": {"s": "$sheet", "src": "$source"}, "n": {"$sum": 1}}}, {"$sort": {"_id.s": 1}}])]
    out = {}
    for r in rows:
        out.setdefault(r["_id"]["s"], {})[r["_id"]["src"]] = r["n"]
    return {"sheets": out, "note": "Read-only reference archive of the original workbook (cached values). Loaded only on request; never used as live data."}


@api.get("/reference/sheet")
async def ref_sheet(name: str, source: str = "primary", page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200), u=Depends(admin_user)):
    q = {"sheet": name, "source": source}
    total = await db.workbook_archive.count_documents(q)
    rows = [x async for x in db.workbook_archive.find(q, {"_id": 0}).sort("row", 1).skip((page - 1) * size).limit(size)]
    return {"rows": rows, "total": total, "page": page}


@api.get("/source-download")
async def source_download(u=Depends(admin_user)):
    await SEC.log_event("source_download", None, u["email"])
    from fastapi.responses import StreamingResponse
    from starlette.concurrency import run_in_threadpool
    buf = await run_in_threadpool(_build_source_zip)
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{APP_SLUG}-source.zip"'})


def _build_source_zip():
    import io as _io
    import zipfile
    from dotenv import dotenv_values
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    harmless = {"EMAIL_FROM_NAME", "DB_NAME", "CORS_ORIGINS", "MONGO_URL", "FRONTEND_URL"}
    import re as _re
    scrub = []
    for envf in (".env", "backend/.env", "frontend/.env"):
        for k, v in dotenv_values(os.path.join(root, envf)).items():
            if not v or len(v) < 6 or k in harmless or k.startswith("REACT_APP_"):
                continue
            if v.lower() in {"ignore", "none", "null", "changeme", "disabled", "true", "false", "placeholder", "unused"}:
                continue  # placeholder words for unused integrations are not secrets and collide with code
            pat = _re.compile(rb"(?<![A-Za-z0-9_])" + _re.escape(v.encode()) + rb"(?![A-Za-z0-9_])")
            scrub.append((len(v), pat, f"<{k}>".encode()))
    # In Docker, secrets arrive as environment variables (not .env files): scrub those values too.
    sens = _re.compile(r"(SECRET|PASSWORD|TOKEN|API_KEY|_SID|_KEY$|ADMIN_EMAIL|ALERT_EMAIL|ALERT_PHONE|CALLMEBOT_USER|CHAT_ID|SMTP_USER|EMAIL_FROM$|VIEWER_EMAIL|ACME_EMAIL)")
    for k, v in os.environ.items():
        if v and len(v) >= 6 and sens.search(k) and v.lower() not in {"true", "false", "none", "changeme"}:
            scrub.append((len(v), _re.compile(rb"(?<![A-Za-z0-9_])" + _re.escape(v.encode()) + rb"(?![A-Za-z0-9_])"), f"<{k}>".encode()))
    scrub.sort(key=lambda x: -x[0])
    skip_dirs = {"node_modules", "build", "__pycache__", ".git", "downloads", "backups", ".cache", "test_reports", ".pytest_cache"}
    skip_files = {"test_credentials.md"}

    def add(z, full, arc):
        data = open(full, "rb").read()
        for _, pat, ph in scrub:
            data = pat.sub(ph, data)
        z.writestr(arc, data)

    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for top in ("backend", "frontend", "deploy", "docs", "reference"):
            base = os.path.join(root, top)
            for d, dirs, files in os.walk(base):
                dirs[:] = [x for x in dirs if x not in skip_dirs]
                for f in files:
                    if f.startswith(".env") or f.endswith((".pyc", ".pem", ".key")) or f in skip_files:
                        continue
                    full = os.path.join(d, f)
                    add(z, full, os.path.relpath(full, root))
        for f in ("README.md", "LICENSE", "design_guidelines.json", ".gitignore", "docker-compose.yml", ".env.example"):
            if os.path.exists(os.path.join(root, f)):
                add(z, os.path.join(root, f), f)
        z.writestr("SECRETS_NOT_INCLUDED.txt", "API keys, passwords, personal contact details and .env files are intentionally excluded or replaced with <PLACEHOLDER> names. Set your own values in your hosting environment (see README.md).\n")
    buf.seek(0)
    return buf


@api.get("/alerts")
async def alerts_get(u=Depends(admin_user)):
    to = os.environ.get("ALERT_EMAIL", "")
    masked = (to.split("@")[0][:2] + "…@" + to.split("@")[-1]) if "@" in to else None
    import messaging
    ph = os.environ.get("ALERT_PHONE", "")
    import alerts as _al
    mode = await alert_mode.get_mode()
    return {"email_to": masked, "phone_to": (ph[:5] + "…" + ph[-3:]) if ph else None,
            "mode": {k: mode[k] for k in ("armed", "effective_armed", "kill_switch", "channels", "version", "armed_at", "armed_by", "disarmed_at", "disarmed_by")},
            "confirm_word": alert_mode.CONFIRM_WORD,
            "email": _al.email_status() + ("" if to else " · ALERT_EMAIL (recipient) not set"),
            "daily_recap": "every day at 20:00 IST (built-in server timer)",
            "sms": "configured (Twilio)" if messaging.sms_configured() else "not configured — needs Twilio Account SID, Auth Token and sender number",
            "telegram": "configured" if messaging.telegram_configured() else "not configured — needs bot token and chat ID",
            "call": messaging.call_status(),
            "log": [clean(x) | {"to": masked} async for x in db.alerts_log.find().sort("at", -1).limit(30)]}


@api.post("/alerts/test")
async def alerts_test(u=Depends(admin_user)):
    import alerts
    last = await db.alerts_log.find_one({"test": True}, sort=[("at", -1)])
    if last and (now_utc() - parse_iso(last["at"])).total_seconds() < 60:
        raise HTTPException(429, "Test alert already sent in the last minute")
    r = await alerts.notify(None, test=True)
    return {"result": r["result"], "sms": r.get("sms"), "telegram": r.get("telegram"), "call": r.get("call")}


@api.get("/alerts/telegram-chats")
async def tg_chats(u=Depends(admin_user)):
    import messaging
    try:
        return {"chats": await messaging.telegram_find_chat(), "note": "Send any message to your bot first, then copy your chat ID into TELEGRAM_CHAT_ID."}
    except Exception as e:
        raise HTTPException(400, str(e))


@api.post("/cron/daily-summary")
async def cron_daily_summary(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    import hmac as _h
    import daily_summary
    auth = request.headers.get("authorization", "")
    sec = os.environ.get("WEBHOOK_CRON_SECRET", "")
    if not sec or not auth.startswith("Bearer ") or not _h.compare_digest(auth[7:], sec):
        raise HTTPException(401, "unauthorized")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid body")
    run_id = request.headers.get("x-webhook-id") or (body or {}).get("run_id")
    if not run_id:
        raise HTTPException(400, "missing run id")
    asyncio.create_task(daily_summary.send(f"cron-{run_id}", scheduled=True))
    return {"accepted": True}


@api.post("/summary/send-now")
async def summary_now(u=Depends(admin_user)):
    import daily_summary
    last = await db.daily_summaries.find_one({"manual": True}, sort=[("at", -1)])
    if last and (now_utc() - parse_iso(last["at"])).total_seconds() < 120:
        raise HTTPException(429, "A recap was sent in the last 2 minutes")
    rid = f"manual-{int(now_utc().timestamp())}"
    await daily_summary.send(rid)
    await db.daily_summaries.update_one({"_id": rid}, {"$set": {"manual": True}})
    return clean(await db.daily_summaries.find_one({"_id": rid}))


@api.get("/summary")
async def summary_get(u=Depends(admin_user)):
    import daily_summary
    d = await daily_summary.build()
    text, _ = daily_summary.render(d)
    return {"preview": text, "schedule": "daily 20:00 IST (built-in server timer; sent only while alert mode is ARMED)", "history": [clean(x) async for x in db.daily_summaries.find({}, {"text": 0}).sort("at", -1).limit(10)]}


@api.get("/delta-sync")
async def dsync_get(u=Depends(admin_user)):
    import delta_sync
    return {**delta_sync.SYNC, "configured": delta_sync.configured(), "server_ip_note": "Delta India API keys require IP whitelisting; add this server's outbound IP to the key.",
            "mode": "read-only: GET /v2/fills and /v2/wallet/transactions only; never places orders"}


@api.post("/delta-sync/run")
async def dsync_run(u=Depends(admin_user)):
    import delta_sync
    if delta_sync.SYNC["status"] == "checking":
        raise HTTPException(409, "sync already running")
    return await delta_sync.sync()


@api.post("/recovery/run")
async def recovery_run(u=Depends(admin_user)):
    r = await recovery.recovery_pass(manual=True)
    return clean(r) if isinstance(r, dict) and "_id" in r else r


# ---------------- one-time import from the old BTC Command Desk ----------------
_IMPORT_COLLS = {"signals": "signals", "validation_snapshots": "validation_snapshots", "plan_archive": "plan_archive", "reviews": "reviews"}


@api.post("/import/legacy")
async def import_legacy(request: Request, u=Depends(admin_user)):
    """Admin uploads the JSON exported from the old site. Records are added by their original IDs, so
    importing twice never duplicates anything. Settings are applied only for known keys."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Not a valid JSON export file")
    if not isinstance(data, dict) or data.get("format") != "vantage-import-v1":
        raise HTTPException(422, "This is not a BTC Command Desk export file (format vantage-import-v1)")
    out = {}
    for key, coll in _IMPORT_COLLS.items():
        rows = data.get(key) or []
        if not isinstance(rows, list):
            raise HTTPException(422, f"{key} must be a list")
        added = 0
        for r in rows[:50000]:
            if not isinstance(r, dict) or not r.get("id"):
                continue
            doc = {k: v for k, v in r.items() if k not in ("id", "_id")}
            doc["_id"] = str(r["id"])
            doc["imported_from"] = "btc-command-desk"
            res = await db[coll].update_one({"_id": doc["_id"]}, {"$setOnInsert": doc}, upsert=True)
            added += 1 if res.upserted_id else 0
        out[key] = {"in_file": len(rows), "added": added, "already_present": len(rows) - added}
    vals = data.get("settings")
    if isinstance(vals, dict):
        cur, ver = await get_settings()
        new = dict(cur)
        for k, v in vals.items():
            d = DEFAULT_SETTINGS.get(k)
            if d is None or v is None:
                continue
            if isinstance(d, bool) and isinstance(v, bool):
                new[k] = v
            elif isinstance(d, (int, float)) and not isinstance(d, bool) and isinstance(v, (int, float)) and not isinstance(v, bool) and finite(float(v)):
                new[k] = float(v)
            elif isinstance(d, list) and isinstance(v, list) and len(v) == len(d) and all(isinstance(x, (int, float)) for x in v):
                new[k] = [float(x) for x in v]
            elif k == "registration_mode" and v in ("open", "approval", "closed"):
                new[k] = v
            elif k == "macro_mode" and v in ("publication", "strict"):
                new[k] = v
        changed = sorted(k for k in new if new.get(k) != cur.get(k))
        if changed:
            await db.settings.update_one({"_id": "global", "version": ver}, {"$set": {"values": new, "updated_at": iso(now_utc()), "updated_by": u["email"]}, "$inc": {"version": 1}})
        out["settings"] = {"changed": changed}
    await SEC.log_event("legacy_import", request, u["email"], detail=str({k: v.get("added") for k, v in out.items() if isinstance(v, dict) and "added" in v})[:300])
    return {"ok": True, "exported_from": data.get("exported_from"), "exported_at": data.get("exported_at"), "result": out}


# ---------------- upgrade requests ----------------
class UpgradeIn(BaseModel):
    version: Optional[int] = None
    title: Optional[str] = Field(default=None, max_length=200)
    prompt: str = Field(min_length=1, max_length=20000)
    expected: Optional[str] = Field(default=None, max_length=2000)
    done: bool = False


@api.get("/upgrades")
async def upgrades(u=Depends(admin_user)):
    s, _ = await get_settings()
    return {"items": [clean(x) async for x in db.upgrade_requests.find().sort("created_at", -1)], "limit": int(s["upgrade_limit"]), "max_chars": int(s["upgrade_chars"])}


@api.post("/upgrades")
async def upgrade_create(b: UpgradeIn, u=Depends(admin_user)):
    s, _ = await get_settings()
    if len(b.prompt) > s["upgrade_chars"]:
        raise HTTPException(422, f"prompt exceeds {int(s['upgrade_chars'])} characters")
    if await db.upgrade_requests.count_documents({}) >= s["upgrade_limit"]:
        raise HTTPException(422, f"limit of {int(s['upgrade_limit'])} saved requests reached — download and delete older ones")
    d = b.model_dump(exclude={"version"}) | {"version": 1, "created_at": iso(now_utc()), "updated_at": iso(now_utc())}
    r = await db.upgrade_requests.insert_one(d)
    return clean(await db.upgrade_requests.find_one({"_id": r.inserted_id}))


@api.put("/upgrades/{uid}")
async def upgrade_update(uid: str, b: UpgradeIn, u=Depends(admin_user)):
    s, _ = await get_settings()
    if len(b.prompt) > s["upgrade_chars"]:
        raise HTTPException(422, f"prompt exceeds {int(s['upgrade_chars'])} characters")
    r = await db.upgrade_requests.update_one({"_id": oid(uid), "version": b.version}, {"$set": b.model_dump(exclude={"version"}) | {"updated_at": iso(now_utc())}, "$inc": {"version": 1}})
    if not r.matched_count:
        raise HTTPException(409, "Request changed elsewhere — reload; your draft is kept")
    return clean(await db.upgrade_requests.find_one({"_id": oid(uid)}))


@api.delete("/upgrades/{uid}")
async def upgrade_delete(uid: str, u=Depends(admin_user)):
    r = await db.upgrade_requests.delete_one({"_id": oid(uid)})
    if not r.deleted_count:
        raise HTTPException(404, "record not found")
    return {"ok": True}


@api.get("/upgrades/{uid}/prepared")
async def upgrade_prepared(uid: str, u=Depends(admin_user)):
    d = await db.upgrade_requests.find_one({"_id": oid(uid)})
    if not d:
        raise HTTPException(404, "not found")
    S = engine.STATE
    issues = [f"- {s['name']}: {s['status']} — {s['error'] or 'stale'}" for s in sources_public() if s["status"] != "ok"]
    ld, ff, sx = S.get("live_direction", {}), S.get("four_factor", {}), S.get("six_factor", {})
    rr = recovery.RECOVERY.get("last_result") or {}
    txt = (f"{APP_NAME} — upgrade request\nWebsite: {FRONTEND_URL}\nPrepared: {iso(now_utc())} (UTC)\n\nTitle: {d.get('title') or '(untitled)'}\n\n"
           f"Request:\n{d['prompt']}\n\nExpected improvement:\n{d.get('expected') or '(not specified)'}\n\n--- Current diagnostics (no credentials included) ---\n"
           f"Five-pillar coverage: {ld.get('coverage')}/5, live direction {ld.get('direction')}, excluded: {', '.join(ld.get('excluded', [])) or 'none'}\n"
           f"Original four-factor complete: {ff.get('complete')} (missing: {', '.join(ff.get('missing', [])) or 'none'})\n"
           f"Original six-factor complete: {sx.get('complete')}\n"
           f"Scalping {S.get('scalp', {}).get('status')}, failing checks: {', '.join(S.get('scalp', {}).get('failing', [])) or 'none'}\n"
           f"Swing {S.get('swing', {}).get('status')}, failing checks: {', '.join(S.get('swing', {}).get('failing', [])) or 'none'}\n"
           f"Source issues:\n{chr(10).join(issues) or '- none'}\n"
           f"Latest recovery result: {rr.get('result', 'none')}{(' (' + str(rr.get('family')) + ' at ' + str(rr.get('at')) + ')') if rr else ''}\n")
    return PlainTextResponse(txt)


@api.get("/security/events")
async def security_events(limit: int = Query(200, ge=1, le=500), u=Depends(admin_user)):
    rows = [clean(x) async for x in db.security_events.find({}, {"expires_at": 0}).sort("at", -1).limit(limit)]
    return {"events": rows, "retention_days": SEC.EVENT_TTL_DAYS, "config_warnings": CONFIG_WARNINGS}


STARTED_AT = now_utc()


@api.get("/health")
async def health():
    return {"ok": True, "time": iso(now_utc())}


@api.get("/health/deep")
async def health_deep(response: Response):
    """Public liveness check for Docker / uptime monitors. Returns 503 if the 2-second
    calculation loop has stalled, so the container is restarted automatically."""
    age = None
    if engine.STATE.get("computed_at"):
        age = (now_utc() - parse_iso(engine.STATE["computed_at"])).total_seconds()
    up = (now_utc() - STARTED_AT).total_seconds()
    stalled = (age is None and up > 180) or (age is not None and age > 120)
    try:
        await asyncio.wait_for(db.command("ping"), 5)
        db_ok = True
    except Exception:
        db_ok = False
    ok = db_ok and not stalled
    if not ok:
        response.status_code = 503
    return {"ok": ok, "time": iso(now_utc()), "db": db_ok, "engine_age_sec": round(age, 1) if age is not None else None,
            "market_stream": WS.get("status"), "uptime_sec": int(up)}


class AlertModeIn(BaseModel):
    armed: bool
    version: int
    channels: Optional[dict] = None
    confirm: Optional[str] = Field(default=None, max_length=20)


@api.put("/alerts/mode")
async def alerts_mode_put(body: AlertModeIn, request: Request, u=Depends(admin_user)):
    cur = await alert_mode.get_mode()
    if body.armed and not cur["armed"] and (body.confirm or "").strip().upper() != alert_mode.CONFIRM_WORD:
        raise HTTPException(422, f"Type {alert_mode.CONFIRM_WORD} to confirm turning alert mode ON")
    if body.armed and cur["kill_switch"]:
        raise HTTPException(409, "ALERTS_KILL_SWITCH is set in the server environment; remove it and restart before arming")
    new = await alert_mode.set_mode(armed=body.armed, channels=body.channels, version=body.version, by=u["email"])
    if new is None:
        raise HTTPException(409, "Alert mode changed elsewhere — reload")
    await SEC.log_event("alerts_armed" if new["armed"] else "alerts_disarmed", request, u["email"],
                        detail="channels: " + ", ".join(k for k, v in new["channels"].items() if v))
    return {k: new[k] for k in ("armed", "effective_armed", "kill_switch", "channels", "version", "armed_at", "armed_by", "disarmed_at", "disarmed_by")}


# ---------------- app wiring ----------------
CONFIG_WARNINGS = []


@app.middleware("http")
async def security_layer(request: Request, call_next):
    if SEC.body_too_large(request):
        resp = PlainTextResponse("Request body too large", status_code=413)
    else:
        wait = SEC.rate_check(request)
        resp = SEC.too_many(wait) if wait else await call_next(request)
    SEC.apply_headers(request.url.path, resp.headers)
    return resp


app.add_exception_handler(Exception, SEC.unhandled_error)


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
        o = request.headers.get("origin")
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        allowed = {u.strip().rstrip("/") for u in FRONTEND_URL.split(",")}
        if o and o.rstrip("/") not in allowed and urlparse(o).netloc != host:
            return PlainTextResponse("Cross-origin write rejected", status_code=403)
    return await call_next(request)


app.include_router(api)
app.include_router(accounts.router)
app.add_middleware(CORSMiddleware, allow_origins=[u.strip() for u in FRONTEND_URL.split(",")], allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type", "Authorization"])


async def seed_users():
    seeds = [(os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"], "admin", "Admin", "admin")]
    if os.environ.get("SEED_VIEWER", "").lower() == "true" and os.environ.get("VIEWER_EMAIL") and os.environ.get("VIEWER_PASSWORD"):
        seeds.append((os.environ["VIEWER_EMAIL"], os.environ["VIEWER_PASSWORD"], "user", "Demo User", "demouser"))
    for email, pw, role, name, uname in seeds:
        email = email.lower()
        ex = await db.users.find_one({"email": email})
        if not ex:
            await db.users.insert_one({"email": email, "username": uname, "password_hash": hash_pw(pw), "role": role, "name": name, "status": "active",
                                       "email_verified": True, "pv": 0, "created_at": iso(now_utc())})
        else:
            fix = {k: v for k, v in (("username", uname), ("status", "active"), ("email_verified", True), ("pv", 0)) if k not in ex}
            if ex.get("role") != role:
                fix["role"] = role
            if not ex.get("password_changed_at") and not check_pw(pw, ex["password_hash"]):
                fix["password_hash"] = hash_pw(pw)
            if fix:
                await db.users.update_one({"_id": ex["_id"]}, {"$set": fix})


async def restore_cache():
    keep = ("fed_h10", "fed_h15", "eia_brent", "fed_h10_meta", "fed_h15_meta", "eia_meta", "fng", "delta_product")
    async for c in db.source_cache.find({"_id": {"$in": list(keep)}}):
        s = REGISTRY.get(c["_id"])
        if s and c.get("data") is not None:
            s.data, s.last_success = c["data"], parse_iso(c["last_success"])


@app.on_event("startup")
async def startup():
    await accounts.ensure_indexes()
    await SEC.ensure_indexes()
    CONFIG_WARNINGS[:] = SEC.config_warnings()
    await db.login_attempts.create_index("identifier")
    await db.news_items.create_index([("published_at", -1)])
    await db.fills.create_index("exchange_id", sparse=True)
    await db.validation_snapshots.create_index([("model", 1), ("capture_ts", -1)])
    await db.oi_history.create_index([("venue", 1), ("t", -1)])
    await seed_users()
    await restore_cache()
    recovery.start_ws()
    async def delta_loop():
        import delta_sync
        while True:
            await asyncio.sleep(60)
            if delta_sync.configured():
                await delta_sync.sync()
            await asyncio.sleep(240)

    async def maintenance_loop():
        # Keep the free-tier disk small: prune high-volume logs. Trading records are never pruned.
        while True:
            try:
                now = now_utc()
                await db.oi_history.delete_many({"t": {"$lt": iso(now - timedelta(days=37))}})
                await db.alerts_log.delete_many({"at": {"$lt": iso(now - timedelta(days=180))}, "test": {"$ne": False}})
                await db.alerts_log.delete_many({"dry_run": True, "at": {"$lt": iso(now - timedelta(days=60))}})
                await db.news_items.delete_many({"published_at": {"$lt": iso(now - timedelta(days=120))}, "review_status": {"$nin": ["REVIEWED"]}})
            except Exception:
                logging.getLogger("maintenance").exception("prune failed")
            await asyncio.sleep(6 * 3600)

    import daily_summary
    import workbook_archive
    for coro in (workbook_archive.ensure_loaded(), recovery.scheduler_loop(), engine.compute_loop(), engine.slow_loop(), recovery.recovery_loop(), delta_loop(),
                 daily_summary.scheduler_loop(), maintenance_loop()):
        asyncio.create_task(coro)


@app.on_event("shutdown")
async def shutdown():
    db.client.close()
