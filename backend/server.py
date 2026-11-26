from dotenv import load_dotenv
load_dotenv()

import os
import io
import csv
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Literal, Optional
import bcrypt
import jwt
from bson import ObjectId
from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends, Query, WebSocket
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
import channel_config
import plan_a
import regime
import flush
import runs
import trio
import elite
import sysmon
import optpicks
import stream
import datahealth
import ui_prefs
import observer
import autotrade
import daytype
import scanner
import webpush
import intel
import desk
import support
import broker_keys
import fundmgr
import viewer
import real_account
import straddle
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
        return {**pub, "brief": insights.brief(pub, pub.get("sources")), "server_time": iso(now_utc()), "user": u, "day_type": daytype.public()}
    return {**engine.STATE, "day_type": daytype.public(), "sources": srcs, "recovery": rec_public(), "server_time": iso(now_utc()), "user": u,
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
async def market(u=Depends(viewer.page("/market"))):
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
async def oi_history(hours: int = Query(24, ge=1, le=168), venue: str = "OKX", u=Depends(viewer.page("/market"))):
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
async def plans(u=Depends(viewer.page("/scalping", "/swing"))):
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
async def signals(page: int = 1, size: int = 25, day: str = "", playbook: str = "", support_level: str = "", u=Depends(current_user)):
    if (day and day not in support.DAY_TYPES) or (playbook and playbook not in support.PLAYBOOKS) or (support_level and support_level not in support.LEVEL_KEYS):
        raise HTTPException(422, "unknown filter value")
    q, filt = {}, None
    if day or playbook or support_level:                  # a viewing filter only: it never changes a signal, a book or a total
        ids = await support.filter_ids(day, playbook, support_level)
        q = {"signal_id": {"$in": list(ids)}}
        listed = {x["signal_id"] async for x in db.signals.find(q, {"signal_id": 1})}      # only signals this list shows (older ones live on the Plan A page)
        filt = support.follow_summary(plan_a.STATE["rows"], listed)
    total = await db.signals.count_documents(q)
    all_total = await db.signals.count_documents({}) if q else total
    rows = [clean(x) async for x in db.signals.find(q).sort("captured_at", -1).skip((page - 1) * size).limit(size)]
    tags = {t["_id"]: t async for t in db.signal_tags.find({"_id": {"$in": [r.get("signal_id") for r in rows]}})}
    pa = {r["signal_id"]: r for r in plan_a.STATE["rows"]}
    for r in rows:  # day type frozen at capture + the same-rules result of following this signal
        t, p = tags.get(r.get("signal_id")) or {}, pa.get(r.get("signal_id")) or {}
        r.update(day_type=t.get("type"), day_label=t.get("label"), playbook=t.get("playbook"),
                 follow_result=p.get("follow_result"), follow_net=p.get("follow_net"), follow_target=p.get("follow_target"), follow_why=p.get("follow_why"), follow_exit_ist=p.get("follow_exit_ist"), context=p.get("context"), plan_result=p.get("plan_result"), plan_net=p.get("net"))
    return {"items": rows, "total": total, "all_total": all_total, "filter_summary": filt, "page": page, "size": size,
            "note": "Model signals are automatic captures. Candle touches are observations — never actual exchange fills."}


@api.get("/switch-support")
async def switch_support(u=Depends(viewer.page("/journal", "/plan-a"))):
    """Support snapshots saved when signals fired (information only)."""
    return {"items": await support.all_snaps()}


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
    from channel_config import CFG
    await channel_config.load()
    to = CFG.get("ALERT_EMAIL", "") or ""
    masked = (to.split("@")[0][:2] + "…@" + to.split("@")[-1]) if "@" in to else None
    import messaging
    ph = CFG.get("ALERT_PHONE", "") or ""
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
    return {"ok": True, "time": iso(now_utc()), "bot": straddle.alive()}


@api.get("/admin/server")
async def admin_server(u=Depends(admin_user)):
    """Server capacity and how the site uses it (admin only): CPU, memory, disk, load, backend, MongoDB, 7-day history."""
    return await sysmon.report()


@api.get("/health/deep")
async def health_deep(response: Response):
    """Public liveness check for Docker / uptime monitors. Returns 503 if the 2-second
    calculation loop has stalled, so the container is restarted automatically."""
    age = None
    if engine.STATE.get("computed_at"):
        age = (now_utc() - parse_iso(engine.STATE["computed_at"])).total_seconds()
    up = (now_utc() - STARTED_AT).total_seconds()
    stalled = (age is None and up > 180) or (age is not None and age > 120)
    if straddle.hold_restart():      # the straddle bot is handling a trade on its own thread: a stalled website must not get the container restarted now
        return {"ok": True, "held_for_trade": True}
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


def _plan_a_token():
    import hashlib
    import hmac
    return hmac.new(os.environ["JWT_SECRET"].encode(), b"plan-a-sheet-feed-v1", hashlib.sha256).hexdigest()[:40]


@api.get("/regime-journal")
async def regime_journal_get(u=Depends(viewer.page("/regime"))):
    """Regime Strategy paper journal + its reverse book (paper only; no orders are ever sent)."""
    st = regime.STATE
    if st["data"] is None and plan_a.STATE.get("rows"):
        await regime.compute()
    return {**(st["data"] or {"name": regime.NAME, "paper_only": True}), "error": st["error"], "computed_at": st["computed_at"]}


@api.get("/flush-short")
async def flush_short_get(u=Depends(viewer.page("/flush-short"))):
    """Flush Short v2 paper journal (paper only; no orders are ever sent). Users get trades and results only (viewer.py)."""
    st = flush.STATE
    if st["data"] is None and plan_a.STATE.get("rows"):
        await flush.compute()
    out = {**(st["data"] or {"name": flush.NAME, "paper_only": True}), "error": st["error"], "computed_at": st["computed_at"], "is_admin": viewer.is_admin(u)}
    return out if viewer.is_admin(u) else viewer.flush_view(out)


@api.get("/candle-runs")
async def candle_runs_get(u=Depends(viewer.page("/candle-runs"))):
    """Candle Runs: 5 paper strategies from the owner's backtest workbook (paper only, no alerts, no orders)."""
    st = runs.STATE
    return {**(st["data"] or {"name": runs.NAME, "paper_only": True}), "error": st["error"], "computed_at": st["computed_at"]}


@api.get("/delta-top3")
async def delta_top3_get(u=Depends(viewer.page("/delta-top3"))):
    """Delta Top 3: 3 paper strategies from the owner's Top20 sheet (paper only, no alerts, no orders, not used by Auto-Trade)."""
    st = trio.STATE
    return {**(st["data"] or {"name": trio.NAME, "paper_only": True}), "error": st["error"], "computed_at": st["computed_at"]}


# ---------------- Menu pages: hide / show (per user, and for everyone by an admin) ----------------
class HiddenPagesIn(BaseModel):
    pages: list[str] = Field(default_factory=list, max_length=60)


@api.get("/ui/config")
async def ui_config(u=Depends(current_user)):
    return await ui_prefs.config(u)


@api.put("/ui/hidden")
async def ui_hidden(body: HiddenPagesIn, u=Depends(current_user)):
    return await ui_prefs.set_user(u, body.pages)


@api.put("/ui/hidden-global")
async def ui_hidden_global(body: HiddenPagesIn, request: Request, u=Depends(admin_user)):
    out = await ui_prefs.set_global(u, body.pages)
    await SEC.log_event("ui_hidden_global", request, u["email"], detail=",".join(out["hidden_global"]))
    return out


# ---------------- Data health (every feed and calculation loop) and the live push stream ----------------
@api.get("/data-health")
async def data_health(u=Depends(admin_user)):
    return await datahealth.report(True)


@api.websocket("/ws")
async def ws_stream(ws: WebSocket):
    await stream.handle(ws)


# ---------------- Option Picks (BTC daily options on Delta India; paper only, no orders, no alerts) ----------------
@api.get("/option-picks")
async def option_picks_get(u=Depends(viewer.page("/option-picks"))):
    out = {**await optpicks.dashboard(), "is_admin": u["role"] == "admin"}
    return out if viewer.is_admin(u) else viewer.optpicks_view(out)


class OptSizingIn(BaseModel):
    put_capital: float = Field(gt=0, le=1_000_000)
    put_leverage: float = Field(ge=1, le=100)
    call_capital: float = Field(gt=0, le=1_000_000)
    call_leverage: float = Field(ge=1, le=100)


@api.post("/option-picks/sizing")
async def option_picks_sizing(body: OptSizingIn, request: Request, u=Depends(admin_user)):
    """Admin: size per leg (capital x leverage). Every backtest and paper trade is re-sized from its stored facts."""
    try:
        await optpicks.set_sizing(body.model_dump(), u["email"])
    except ValueError as e:
        raise HTTPException(409, str(e))
    await SEC.log_event("option_picks_sizing", request, u["email"], detail=str(body.model_dump()))
    return {**await optpicks.dashboard(), "is_admin": True}


@api.get("/option-picks/trades.csv")
async def option_picks_csv(key: Literal["IFLY", "SP16", "SSTR", "BPS", "BCS"], u=Depends(admin_user)):
    return PlainTextResponse(optpicks.trades_csv(key), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="option_picks_{key}_backtest.csv"'})


# ---------------- Elite 4 Edge (one trade at a time; paper by default, real orders only when an admin switches it on) ----------------
class EliteControlIn(BaseModel):
    action: Literal["stop_all", "resume"]


class EliteModeIn(BaseModel):
    mode: Literal["PAPER", "TESTNET", "LIVE"]
    confirm: Optional[str] = Field(default=None, max_length=10)


class FundSettingsIn(BaseModel):
    capital: Optional[float] = Field(default=None, ge=100, le=10_000_000)
    alloc: Optional[dict[str, float]] = None
    target_pct: Optional[float] = Field(default=None, ge=0.5, le=100)
    month_loss_pct: Optional[float] = Field(default=None, ge=1, le=100)
    fund_dd_pct: Optional[float] = Field(default=None, ge=1, le=100)
    strat_dd_pct: Optional[dict[str, float]] = None
    max_use_pct: Optional[float] = Field(default=None, ge=5, le=100)
    lock_at_target: Optional[bool] = None


class FundControlIn(BaseModel):
    action: Literal["pause", "resume", "stop_all", "resume_all", "restart"]
    strategy: Optional[Literal["elite", "optpicks", "flush"]] = None
    confirm: Optional[str] = Field(default=None, max_length=10)


@api.get("/fund")
async def fund_get(u=Depends(viewer.page("/fund-manager"))):
    """Fund Manager: a paper fund over Elite 4 Edge, Option Picks and Flush Short. No order is ever sent from here."""
    out = await fundmgr.dashboard(u["role"] == "admin")
    return out if viewer.is_admin(u) else viewer.fund_view(out)


@api.put("/fund/settings")
async def fund_settings_put(body: FundSettingsIn, request: Request, u=Depends(admin_user)):
    try:
        await fundmgr.save_settings(body.model_dump(exclude_none=True), u["email"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    await SEC.log_event("fund_settings", request, u["email"])
    await fundmgr.sync()
    return await fundmgr.dashboard(True)


@api.post("/fund/control")
async def fund_control(body: FundControlIn, request: Request, u=Depends(admin_user)):
    if body.action == "restart" and (body.confirm or "").strip().upper() != "RESTART":
        raise HTTPException(400, "Type RESTART to begin a new fund book")
    try:
        await fundmgr.control(body.action, body.strategy, u["email"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    await SEC.log_event("fund_control", request, u["email"], detail=f"{body.action} {body.strategy or ''}".strip())
    return await fundmgr.dashboard(True)


# ---------------- Straddle bot (admin only; PAPER by default, real orders only after the admin types LIVE) ----------------
class StraddleSettingsIn(BaseModel):
    patch: dict


class StraddleModeIn(BaseModel):
    mode: Literal["PAPER", "TESTNET", "LIVE"]
    confirm: Optional[str] = None


class StraddleControlIn(BaseModel):
    action: Literal["kill", "enable", "disable", "clear_halt"]


class StraddleCheckIn(BaseModel):
    kind: Literal["connection", "round_trip"]
    mode: Literal["TESTNET", "LIVE"] = "TESTNET"


@api.get("/straddle")
async def straddle_get(u=Depends(admin_user)):
    """Short ATM BTC straddle bot: state, today's trade, backtest vs live numbers, monthly tables and curves."""
    return await straddle.dashboard()


@api.get("/straddle/backtest")
async def straddle_backtest(u=Depends(admin_user)):
    return await straddle.backtest_view()


@api.get("/straddle/journal")
async def straddle_journal(src: str = "mark_fixed", offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000), u=Depends(admin_user)):
    try:
        return await straddle.journal_page(src, offset, limit)
    except ValueError as e:
        raise HTTPException(400, str(e))


@api.get("/straddle/journal.csv")
async def straddle_journal_csv(src: str = "mark_fixed", u=Depends(admin_user)):
    try:
        text = await straddle.journal_csv(src)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return PlainTextResponse(text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="straddle_{src.lower()}_journal.csv"'})


@api.post("/straddle/settings")
async def straddle_settings(body: StraddleSettingsIn, request: Request, u=Depends(admin_user)):
    try:
        await straddle.save_settings(body.patch, u["email"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    await SEC.log_event("straddle_settings", request, u["email"], detail=", ".join(sorted(body.patch))[:200])
    return await straddle.dashboard()


@api.post("/straddle/mode")
async def straddle_mode(body: StraddleModeIn, request: Request, u=Depends(admin_user)):
    try:
        await straddle.ENGINE.set_mode(body.mode, body.confirm, u["email"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Delta did not accept the {body.mode} key: {e}")
    await SEC.log_event("straddle_mode", request, u["email"], detail=body.mode)
    return await straddle.dashboard()


@api.post("/straddle/control")
async def straddle_control(body: StraddleControlIn, request: Request, u=Depends(admin_user)):
    if body.action == "kill":
        flat = await straddle.ENGINE.kill_switch(u["email"])
        if not flat:
            raise HTTPException(500, "Kill switch sent, but the position is not confirmed flat yet. Check Delta now.")
    elif body.action == "clear_halt":
        await straddle.set_state(halted=False, halt_reason=None)
        await straddle.event(f"Trading halt cleared by {u['email']}", "warn")
    else:
        await straddle.set_state(enabled=body.action == "enable")
        await straddle.event(f"Bot switched {'on' if body.action == 'enable' else 'off'} by {u['email']}", "warn")
    await SEC.log_event("straddle_control", request, u["email"], detail=body.action)
    return await straddle.dashboard()


@api.post("/straddle/check")
async def straddle_check(body: StraddleCheckIn, request: Request, u=Depends(admin_user)):
    """Rehearsals. connection: key, clock, wallet, contracts, prices, leverage and one resting order that cannot fill, cancelled at once.
    round_trip: Testnet only – the bot's own entry, backup stops and exit with 1 lot."""
    try:
        if body.kind == "round_trip":
            rec = await straddle.in_bot(straddle.round_trip(u["email"]))
        else:
            rec = await straddle.in_bot(straddle.connection_test(body.mode, u["email"]))
    except ValueError as e:
        raise HTTPException(400, str(e))
    await SEC.log_event("straddle_check", request, u["email"], detail=f"{body.kind} {body.mode} {'ok' if rec['ok'] else 'failed'}")
    return {"check": rec, "dashboard": await straddle.dashboard()}


@api.post("/straddle/backtest/import")
async def straddle_import(request: Request, u=Depends(admin_user)):
    """Body = the .xlsx file itself (sheets J_Mark_Fixed and J_Trade_Fixed give the day facts; the rest is re-computed)."""
    blob = await request.body()
    if not blob or len(blob) > 20_000_000:
        raise HTTPException(400, "Send one Excel file of at most 20 MB")
    try:
        out = await straddle.import_workbook(blob, (request.headers.get("x-filename") or "backtest.xlsx")[:120], u["email"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    await SEC.log_event("straddle_import", request, u["email"], detail=out["source"][:200])
    return {**out, "backtest": await straddle.backtest_view()}


# ---------------- Real Account Tested (admin only) ----------------
class RealTradeIn(BaseModel):
    entry_ist: str
    side: str
    entry: float
    sl: float
    tp: float
    result: str
    exit: float


@api.get("/real-account")
async def real_account_get(u=Depends(admin_user)):
    """Trades from the owner's real Delta Exchange account, entered by an admin; balances and P&L worked out from them."""
    return await real_account.dashboard()


@api.post("/real-account/trades")
async def real_account_add(body: RealTradeIn, request: Request, u=Depends(admin_user)):
    try:
        await real_account.add(body.model_dump(), u["email"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    await SEC.log_event("real_trade_add", request, u["email"], detail=f"{body.entry_ist} {body.side}")
    return await real_account.dashboard()


@api.delete("/real-account/trades/{trade_id}")
async def real_account_remove(trade_id: str, request: Request, u=Depends(admin_user)):
    try:
        await real_account.remove(trade_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    await SEC.log_event("real_trade_remove", request, u["email"], detail=trade_id)
    return await real_account.dashboard()


@api.get("/elite")
async def elite_get(u=Depends(viewer.page("/elite-4-edge"))):
    out = await elite.dashboard()
    out["is_admin"] = u["role"] == "admin"
    return out if viewer.is_admin(u) else viewer.elite_view(out)


@api.get("/elite/live")
async def elite_live_get(u=Depends(admin_user)):
    """Live paper trading (tab C) is for admins; users get journal A only."""
    return await elite.dashboard(journals=False)


@api.post("/elite/control")
async def elite_control(body: EliteControlIn, request: Request, u=Depends(admin_user)):
    try:
        await {"stop_all": elite.ENGINE.stop_all, "resume": elite.ENGINE.resume}[body.action](u["email"])
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(409, f"{body.action} failed: {str(e)[:200]}")
    await SEC.log_event("elite_" + body.action, request, u["email"])
    return await elite.dashboard()


@api.post("/elite/mode")
async def elite_mode(body: EliteModeIn, request: Request, u=Depends(admin_user)):
    try:
        await elite.ENGINE.set_mode(body.mode, body.confirm, u["email"])
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:  # Delta pre-flight failed (key, IP whitelist, leverage …) – mode is unchanged
        raise HTTPException(409, f"{body.mode} check failed, mode not changed: {str(e)[:200]}")
    await SEC.log_event("elite_mode", request, u["email"], detail=body.mode)
    return await elite.dashboard()


@api.get("/observer")
async def observer_get(u=Depends(viewer.page("/observer"))):
    """Strategy Observer: every page and journal compared, recent vs 26 months (read-only)."""
    st = observer.STATE
    if st["data"] is None and plan_a.STATE.get("rows"):
        await observer.compute()
    return {**(st["data"] or {}), "error": st["error"], "computed_at": st["computed_at"]}


@api.get("/plan-a")
async def plan_a_get(request: Request, u=Depends(viewer.page("/plan-a", "/journal"))):
    st = plan_a.STATE
    out = {"computed_at": st["computed_at"], "summary": st["summary"], "rows": st["rows"][-300:], "error": st["error"],
           "live_price": st["live_price"], "fees": plan_a.FEES,
           "rule": "4C reverse 1:2 + 6-hour exit, scalps only, one trade at a time",
           "books": {k: ({m: {x: y for x, y in b.items() if x != "picks"} for m, b in v.items()} if isinstance(v, dict) else v) for k, v in st["books"].items()},
           "day_type": daytype.public()}
    if u["role"] == "admin":
        base = os.environ["FRONTEND_URL"].split(",")[0].rstrip("/")
        out["feed_url"] = f"{base}/api/plan-a/feed.csv?token={_plan_a_token()}"
    return out


# ---------------- Phone pop-up notifications (Web Push) ----------------
class PushSubIn(BaseModel):
    endpoint: str = Field(max_length=1000)
    keys: dict


class PushEndpointIn(BaseModel):
    endpoint: str = Field(default="", max_length=1000)


@api.get("/push/key")
async def push_key(u=Depends(admin_user)):
    return {"public_key": (await webpush.keys())[1]}


@api.get("/push/status")
async def push_status(u=Depends(admin_user)):
    return await webpush.status(u["id"])


@api.post("/push/subscribe")
async def push_subscribe(body: PushSubIn, request: Request, u=Depends(admin_user)):
    try:
        n = await webpush.subscribe(body.model_dump(), u, request.headers.get("user-agent", ""))
    except ValueError as e:
        raise HTTPException(422, str(e))
    await SEC.log_event("push_subscribe", request, u["email"])
    return {"ok": True, "devices": n}


@api.post("/push/unsubscribe")
async def push_unsubscribe(body: PushEndpointIn, u=Depends(current_user)):
    await webpush.unsubscribe(body.endpoint)
    return {"ok": True}


@api.post("/push/test")
async def push_test(u=Depends(admin_user)):
    r = await webpush.send(f"{APP_NAME} test", "Notifications are working on this device. You'll get trade entries, exits and daily limits here.", "/auto-trade", "test", user_id=u["id"])
    if not r["devices"]:
        raise HTTPException(409, "No device is registered yet. Turn notifications on first.")
    return r


# ---------------- Market Intelligence ----------------
@api.get("/desk-summary")
async def desk_summary(u=Depends(current_user)):
    """Dashboard summary of every module with its weight. Read-only: it never touches signals or settings."""
    ev = list((REGISTRY["bls"].data or {}).get("events", [])) if "bls" in REGISTRY else []
    ev += [x async for x in db.manual_events.find({}, {"_id": 0, "title": 1, "scheduled_utc": 1, "impact": 1})]
    try:
        a = await autotrade.status()
        auto = {"mode": a["mode"], "status": a["status"], "day_type_only": a["settings"].get("direction_mode") == 2, "today": a["today"]["total"],
                "open_trade": {k: a["open_trade"].get(k) for k in ("plan", "day_type", "bot_dir", "entry", "stop", "target")} if a.get("open_trade") else None, "armed": len(a.get("armed") or [])}
    except Exception:
        auto = None
    snaps, now_sup = {}, None
    try:
        snaps = {x["_id"]: {"level": x.get("level"), "passed": x.get("passed")} async for x in db.switch_support.find({}, {"level": 1, "passed": 1})}
        I, read, soon, cascade = await support.context()
        now_sup = support.now_view(daytype.public(), I, read, soon, cascade)
    except Exception:
        pass
    out = desk.summary(engine.STATE, scanner.STATE["result"] or scanner.compute(engine.STATE), intel.STATE, ev, plan_a.STATE.get("books"), plan_a.STATE.get("rows"), auto, snaps, now_sup)
    return out if viewer.is_admin(u) else viewer.desk_view(out)


@api.get("/intel")
async def intel_get(u=Depends(viewer.page("/intel"))):
    return intel.public()


@api.get("/intel/chart")
async def intel_chart(tf: str = "1h", u=Depends(viewer.page("/intel"))):
    if tf not in intel.TF:
        raise HTTPException(422, "Unknown timeframe")
    return intel.chart(tf)


class FredKeyIn(BaseModel):
    key: str = ""


@api.get("/intel/fred-key")
async def intel_fred_get(u=Depends(admin_user)):
    return await intel.intel2.fred_status()          # never returns the key itself


@api.put("/intel/fred-key")
async def intel_fred_put(body: FredKeyIn, request: Request, u=Depends(admin_user)):
    try:
        out = await intel.intel2.save_fred_key(body.key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"FRED did not accept this key ({str(e)[:120]}). Nothing was saved.")
    await SEC.log_event("fred_key", request, u["email"], detail="saved" if out["set"] else "removed")
    return out


@api.get("/intel/health")
async def intel_health():
    """Which data providers answer from this server (names and status only; nothing private)."""
    return {"time": iso(now_utc()), "sources": [{k: s[k] for k in ("label", "module", "status", "age_sec", "error")} for s in intel.health()]}


@api.get("/strategy-audit")
async def strategy_audit(u=Depends(admin_user)):
    return plan_a.audit()


@api.get("/scanner")
async def scanner_get(u=Depends(viewer.page("/scanner"))):
    return await scanner.public(engine.STATE)


@api.get("/plan-a/feed.csv")
async def plan_a_feed(token: str = Query(..., max_length=80)):
    """Read-only CSV for Google Sheets IMPORTDATA. Protected by a secret link token (no login)."""
    import hmac
    if not hmac.compare_digest(token, _plan_a_token()):
        raise HTTPException(403, "invalid feed token")
    return Response(plan_a.feed_csv(), media_type="text/csv", headers={"Cache-Control": "no-store"})


class ChannelsIn(BaseModel):
    values: dict


# ---------------- Auto-trade (Plan A reverse 1:2 + 6h exit) ----------------
class AutoSettingsIn(BaseModel):
    values: dict


class AutoControlIn(BaseModel):
    action: Literal["pause", "resume", "close_all"]


class AutoModeIn(BaseModel):
    mode: Literal["PAPER", "TESTNET", "LIVE"]
    confirm: Optional[str] = Field(default=None, max_length=10)


@api.get("/autotrade")
async def autotrade_get(u=Depends(admin_user)):
    return await autotrade.status()


@api.put("/autotrade/settings")
async def autotrade_settings_put(body: AutoSettingsIn, request: Request, u=Depends(admin_user)):
    try:
        out = await autotrade.save_settings(body.values, u["email"])
    except ValueError as e:
        raise HTTPException(422, str(e))
    await autotrade.ENGINE.event("settings", "Settings changed by " + u["email"] + ": " + ", ".join(f"{k}={out[k]}" for k in sorted(body.values) if k in out))
    await SEC.log_event("autotrade_settings", request, u["email"], detail=", ".join(sorted(k for k in body.values if k in out)))
    return out


@api.post("/autotrade/control")
async def autotrade_control(body: AutoControlIn, request: Request, u=Depends(admin_user)):
    eng = autotrade.ENGINE
    try:
        await {"pause": eng.pause, "resume": eng.resume, "close_all": eng.close_everything}[body.action](u["email"])
    except ValueError as e:
        raise HTTPException(409, str(e))
    await SEC.log_event("autotrade_" + body.action, request, u["email"])
    return await autotrade.status()


@api.post("/autotrade/mode")
async def autotrade_mode(body: AutoModeIn, request: Request, u=Depends(admin_user)):
    try:
        await autotrade.ENGINE.set_mode(body.mode, body.confirm, u["email"])
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:  # Delta pre-flight failed (key, IP whitelist, leverage …) – mode is unchanged
        raise HTTPException(409, f"{body.mode} check failed, mode not changed: {str(e)[:200]}")
    await SEC.log_event("autotrade_mode", request, u["email"], detail=body.mode)
    return await autotrade.status()


class BrokerKeyIn(BaseModel):
    mode: str
    key: str = ""
    secret: str = ""


@api.get("/autotrade/keys")
async def autotrade_keys_get(u=Depends(admin_user)):
    return await broker_keys.status()                  # never the key or secret, only whether one is saved


@api.put("/autotrade/keys")
async def autotrade_keys_put(body: BrokerKeyIn, request: Request, u=Depends(admin_user)):
    try:
        out = await broker_keys.save(body.mode, body.key, body.secret, u["email"])
    except ValueError as e:
        await autotrade.ENGINE.event("settings", f"{body.mode} Delta API key was NOT saved: {e}", level="warn")   # the reason only, never the key
        raise HTTPException(400, str(e))
    await SEC.log_event("broker_key_saved", request, u["email"], detail=body.mode)
    await autotrade.ENGINE.event("settings", f"{body.mode} Delta API key saved and checked by {u['email']}")
    return out


@api.post("/autotrade/keys/{mode}/check")
async def autotrade_keys_check(mode: str, u=Depends(admin_user)):
    """Read the Delta balance again with the connected key (read-only)."""
    try:
        await broker_keys.check(mode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await broker_keys.status()


@api.delete("/autotrade/keys/{mode}")
async def autotrade_keys_delete(mode: str, request: Request, u=Depends(admin_user)):
    try:
        out = await broker_keys.remove(mode, u["email"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    await SEC.log_event("broker_key_removed", request, u["email"], detail=mode)
    await autotrade.ENGINE.event("settings", f"{mode} Delta API key removed by {u['email']}")
    return out


@api.get("/autotrade/journal.csv")
async def autotrade_journal(u=Depends(admin_user)):
    return Response(await autotrade.journal_csv(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{APP_SLUG}-autotrade-journal.csv"', "Cache-Control": "no-store"})


@api.get("/alerts/channels")
async def alerts_channels_get(u=Depends(admin_user)):
    return await channel_config.public()


@api.put("/alerts/channels")
async def alerts_channels_put(body: ChannelsIn, request: Request, u=Depends(admin_user)):
    try:
        out = await channel_config.save(body.values, u["email"])
    except ValueError as e:
        raise HTTPException(422, str(e))
    await SEC.log_event("alert_channels_updated", request, u["email"], detail=", ".join(sorted(k for k in body.values if k in channel_config.FIELDS)))
    return out


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


class ETagMiddleware:
    """GET /api JSON answers carry an ETag; a request with a matching If-None-Match gets an empty 304, so polling an unchanged
    page costs a few bytes. Answers are marked no-store (nothing is kept in the browser's disk cache); the app keeps the last
    copy in memory. Pure ASGI, so the WebSocket stream passes straight through."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "GET" or not scope["path"].startswith("/api/") or scope["path"].endswith(".csv"):
            return await self.app(scope, receive, send)
        import hashlib
        inm = dict(scope.get("headers") or []).get(b"if-none-match", b"").decode()
        start, chunks = {}, []

        async def capture(msg):
            if msg["type"] == "http.response.start":
                start.update(msg)
            elif msg["type"] == "http.response.body":
                chunks.append(msg.get("body", b""))
                if not msg.get("more_body"):
                    body = b"".join(chunks)
                    headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
                    ctype = dict(start.get("headers", [])).get(b"content-type", b"")
                    if start.get("status") == 200 and ctype.startswith(b"application/json"):
                        headers = [(k, v) for k, v in headers if k.lower() not in (b"etag", b"cache-control")]
                        tag = ('W/"' + hashlib.blake2b(body, digest_size=12).hexdigest() + '"').encode()
                        headers += [(b"etag", tag), (b"cache-control", b"no-store")]
                        if inm and tag.decode() in inm:
                            await send({"type": "http.response.start", "status": 304, "headers": [(b"etag", tag), (b"cache-control", b"no-store")]})
                            await send({"type": "http.response.body", "body": b""})
                            return
                    await send({"type": "http.response.start", "status": start.get("status", 200), "headers": headers + [(b"content-length", str(len(body)).encode())]})
                    await send({"type": "http.response.body", "body": body})
        await self.app(scope, receive, capture)


app.include_router(api)
app.include_router(accounts.router)
app.add_middleware(ETagMiddleware)
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
    await broker_keys.load()                          # pasted Delta keys, decrypted into memory before Auto-Trade starts
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
                await db.candles_1m.delete_many({"_id": {"$lt": int(now.timestamp()) - 45 * 86400}})
                await db.news_items.delete_many({"published_at": {"$lt": iso(now - timedelta(days=120))}, "review_status": {"$nin": ["REVIEWED"]}})
            except Exception:
                logging.getLogger("maintenance").exception("prune failed")
            await asyncio.sleep(6 * 3600)

    import daily_summary
    import workbook_archive
    try:
        await channel_config.load()
    except Exception:
        logging.getLogger("alerts").exception("could not load alert channel settings")
    for coro in (workbook_archive.ensure_loaded(), recovery.scheduler_loop(), engine.compute_loop(), engine.slow_loop(), recovery.recovery_loop(), delta_loop(),
                 daily_summary.scheduler_loop(), maintenance_loop(), plan_a.loop(), regime.loop(), flush.loop(), runs.loop(), trio.loop(), elite.loop(), sysmon.loop(), optpicks.loop(), stream.pulse_loop(), broker_keys.loop(), fundmgr.loop(), straddle.watchdog(), observer.loop(), autotrade.loop(), daytype.loop(), scanner.loop(lambda: engine.STATE), intel.start()):
        asyncio.create_task(coro)
    straddle.start(asyncio.get_running_loop())   # the straddle bot has its own thread, event loop and database connection


@app.on_event("shutdown")
async def shutdown():
    db.client.close()
