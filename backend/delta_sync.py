"""Read-only Delta Exchange India fill import. Never places or cancels orders (only GET endpoints are signed)."""
import os
import time
import hmac
import hashlib
import logging
from datetime import timedelta
from urllib.parse import urlencode
import httpx
from core import db, now_utc, iso, parse_iso, finite
from sources import REGISTRY

log = logging.getLogger("delta_sync")
BASE = "https://api.india.delta.exchange"
SYNC = {"status": "not configured", "last_attempt": None, "last_success": None, "error": None, "imported": 0, "updated": 0, "raw_fills": 0}


def configured():
    return bool(os.environ.get("DELTA_API_KEY") and os.environ.get("DELTA_API_SECRET"))


async def signed_get(path, params):
    qs = ("?" + urlencode(params)) if params else ""
    ts = str(int(time.time()))
    sig = hmac.new(os.environ["DELTA_API_SECRET"].encode(), f"GET{ts}{path}{qs}".encode(), hashlib.sha256).hexdigest()
    headers = {"api-key": os.environ["DELTA_API_KEY"], "timestamp": ts, "signature": sig, "User-Agent": "VantageBTC-readonly", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(BASE + path + qs, headers=headers)
    j = r.json() if "json" in r.headers.get("content-type", "") else {}
    if r.status_code >= 400 or not j.get("success", False):
        err = (j.get("error") or {})
        code = err.get("code") if isinstance(err, dict) else err
        hint = " — whitelist this server's IP on your Delta API key" if code in ("ip_not_whitelisted_for_api_key", "unauthorized_ip") else ""
        raise RuntimeError(f"Delta refused {path} ({r.status_code} {code or r.text[:100]}){hint}")
    return j


async def _paged(path, params, max_pages=40):
    out, after = [], None
    for _ in range(max_pages):
        p = dict(params, page_size=100)
        if after:
            p["after"] = after
        j = await signed_get(path, p)
        out += j.get("result", [])
        after = (j.get("meta") or {}).get("after")
        if not after:
            break
    return out


def _us(dt):
    return int(dt.timestamp() * 1_000_000)


async def fetch_fills(since):
    rows = await _paged("/v2/fills", {"product_ids": 27, "start_time": _us(since), "end_time": _us(now_utc())})
    n = 0
    for f in rows:
        if f.get("product_symbol", "BTCUSD") != "BTCUSD":
            continue
        doc = {"_id": str(f["id"]), "side": f["side"], "size": int(f["size"]), "price": float(f["price"]),
               "commission": float(f["commission"]) if f.get("commission") not in (None, "") else None,
               "created_at": iso(parse_iso(f["created_at"])), "order_id": str(f.get("order_id")), "role": f.get("role"), "raw": f}
        r = await db.delta_fills.update_one({"_id": doc["_id"]}, {"$setOnInsert": doc}, upsert=True)
        n += 1 if r.upserted_id else 0
    return n


async def fetch_funding(since):
    rows = await _paged("/v2/wallet/transactions", {"transaction_types": "funding", "start_time": _us(since), "end_time": _us(now_utc())})
    for t in rows:
        await db.delta_funding.update_one({"_id": str(t.get("id") or t.get("uuid"))}, {"$setOnInsert": {
            "amount": float(t["amount"]), "created_at": iso(parse_iso(t["created_at"])), "symbol": (t.get("meta_data") or {}).get("product_symbol"), "raw": t}}, upsert=True)
    return len(rows)


def build_round_trips(fills, cv):
    trades, cur, pos = [], None, 0
    for f in fills:
        signed = f["size"] if f["side"] == "buy" else -f["size"]
        rem, comm_per = signed, (f["commission"] / f["size"]) if finite(f["commission"]) and f["size"] else None
        while rem:
            if pos == 0:
                cur = {"direction": "LONG" if rem > 0 else "SHORT", "open_c": 0, "open_val": 0.0, "close_c": 0, "close_val": 0.0,
                       "fees": 0.0, "fees_known": True, "entry_time": f["created_at"], "exit_time": None, "fill_ids": []}
            same = (pos >= 0 and rem > 0) or (pos <= 0 and rem < 0)
            take = abs(rem) if same else min(abs(rem), abs(pos))
            if same:
                cur["open_c"] += take; cur["open_val"] += take * f["price"]; pos += take if rem > 0 else -take
            else:
                cur["close_c"] += take; cur["close_val"] += take * f["price"]; pos += take if rem > 0 else -take
            if comm_per is None:
                cur["fees_known"] = False
            else:
                cur["fees"] += comm_per * take
            cur["fill_ids"].append(f["_id"])
            rem += -take if rem > 0 else take
            if pos == 0:
                cur["exit_time"] = f["created_at"]
                trades.append(cur); cur = None
    if cur:
        trades.append(cur)
    out = []
    for t in trades:
        closed = t["exit_time"] is not None
        out.append({"exchange_id": f"DELTA-{t['fill_ids'][0]}", "strategy": "delta-import", "direction": t["direction"],
                    "entry_time": t["entry_time"], "entry_price": round(t["open_val"] / t["open_c"], 2), "qty_btc": round(t["open_c"] * cv, 6),
                    "exit_time": t["exit_time"], "exit_price": round(t["close_val"] / t["close_c"], 2) if closed else None,
                    "fees_usd": round(t["fees"], 4) if t["fees_known"] else None, "fill_ids": t["fill_ids"],
                    "partial_exit_contracts": t["close_c"] if not closed and t["close_c"] else None})
    return out


async def sync(full_days=30):
    if not configured():
        SYNC.update(status="not configured", error="DELTA_API_KEY / DELTA_API_SECRET not set")
        return SYNC
    SYNC.update(status="checking", last_attempt=iso(now_utc()))
    try:
        last = await db.delta_fills.find_one(sort=[("created_at", -1)])
        since = parse_iso(last["created_at"]) - timedelta(hours=1) if last else now_utc() - timedelta(days=full_days)
        new = await fetch_fills(since)
        funding_ok, funding_err = True, None
        try:
            await fetch_funding(since)
        except Exception as e:
            funding_ok, funding_err = False, str(e)[:160]
        prod = REGISTRY["delta_product"].data or {}
        cv = float(prod.get("contract_value") or 0)
        if cv <= 0:
            raise RuntimeError("live contract value unavailable — cannot convert contracts to BTC")
        fills = [x async for x in db.delta_fills.find().sort([("created_at", 1), ("_id", 1)])]
        fund = [x async for x in db.delta_funding.find()]
        imported = updated = 0
        for t in build_round_trips(fills, cv):
            if funding_ok and t["exit_time"]:
                a, b = parse_iso(t["entry_time"]), parse_iso(t["exit_time"])
                # Delta wallet amount: negative = paid. Journal convention: paid = positive cost.
                t["funding_usd"] = round(-sum(x["amount"] for x in fund if a <= parse_iso(x["created_at"]) <= b and x.get("symbol") in (None, "BTCUSD")), 4)
            else:
                t["funding_usd"] = None
            t["notes"] = "Imported read-only from Delta fills" + ("" if funding_ok else f"; funding unknown ({funding_err})")
            ex = await db.fills.find_one({"exchange_id": t["exchange_id"]})
            if not ex:
                await db.fills.insert_one({**t, "source": "delta-import", "version": 1, "created_at": iso(now_utc()), "created_by": "delta-sync"})
                imported += 1
            elif ex.get("source") == "delta-import" and not ex.get("manual_edit") and (ex.get("exit_time") != t["exit_time"] or ex.get("fill_ids") != t["fill_ids"]):
                await db.fills.update_one({"_id": ex["_id"]}, {"$set": {**t, "updated_at": iso(now_utc())}, "$inc": {"version": 1}})
                updated += 1
        SYNC.update(status="ok", last_success=iso(now_utc()), error=None if funding_ok else f"fills ok; funding: {funding_err}",
                    imported=imported, updated=updated, raw_fills=len(fills), new_raw=new)
    except Exception as e:
        SYNC.update(status="error", error=str(e)[:300])
    return SYNC
