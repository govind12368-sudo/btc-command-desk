"""Live push to the browser (WebSocket /api/ws).

Once a second the server sends a small "pulse" to every open page: the live BTCUSD price from the Delta websocket and a version
marker per data topic (strategies, journals, Auto-Trade, Elite, Option Picks …). A page re-loads its data only when its topic's
marker changes, instead of asking the server every few seconds. Pages fall back to normal polling when the socket is down.
Auth: the same httpOnly access-token cookie as the API (sent with the WebSocket handshake), or a token in the first message.
"""
import asyncio
import json
import logging
import os
import time

import jwt
from fastapi import WebSocket, WebSocketDisconnect

from core import db, iso, now_utc

log = logging.getLogger("stream")
CLIENTS = {}                 # websocket -> {"user": email, "role": role, "q": Queue}
STATS = {"started": None, "pulses": 0, "last_pulse": None, "peak_clients": 0}
_last = {"price": None, "v": None, "t": 0.0}
_markers = {"v": {}, "at": 0.0}
TOPICS = ("state", "runs", "trio", "elite", "optpicks", "flush", "plan_a", "regime", "observer", "autotrade", "signals", "news", "intel")


def _ca(mod):
    try:
        st = __import__(mod).STATE
        return st.get("computed_at") or st.get("updated")
    except Exception:
        return None


async def _db_latest(coll, field="at"):
    try:
        d = await db[coll].find_one({}, {field: 1}, sort=[(field, -1)])
        return d.get(field) if d else None
    except Exception:
        return None


async def markers():
    """Version marker per topic (cheap: module timestamps plus a few indexed 'latest event' reads every 2 s)."""
    if time.time() - _markers["at"] < 2:
        return _markers["v"]
    import engine
    from sources import REGISTRY, WS
    srcs = tuple(sorted((k, s.status) for k, s in REGISTRY.items()))
    S = engine.STATE
    core_state = [S.get("decision"), (S.get("scalp") or {}).get("status"), (S.get("swing") or {}).get("status"), (S.get("plan") or {}).get("id"),
                  (S.get("readiness") or {}).get("status") if isinstance(S.get("readiness"), dict) else None]
    v = {
        "state": hash((srcs, WS.get("status"), json.dumps(core_state, sort_keys=True, default=str))),
        "runs": _ca("runs"), "trio": _ca("trio"), "flush": _ca("flush"), "plan_a": _ca("plan_a"), "regime": _ca("regime"), "observer": _ca("observer"), "fund": _ca("fundmgr"),
        "elite": f"{_ca('elite')}|{await _db_latest('elite_events')}",
        "optpicks": f"{_ca('optpicks')}|{await _db_latest('opt_live', 'created')}",
        "autotrade": await _db_latest("autotrade_events"),
        "signals": await _db_latest("signals", "captured_at"),
        "news": await _db_latest("news_items", "published_at"),
    }
    _markers.update(v={k: str(x) for k, x in v.items()}, at=time.time())
    return _markers["v"]


def _price():
    from sources import WS
    q = WS.get("quote") or {}
    return {"last": q.get("last"), "bid": q.get("bid"), "ask": q.get("ask"), "mark": q.get("mark"), "ws": WS.get("status")}


async def pulse_loop():
    STATS["started"] = iso(now_utc())
    while True:
        try:
            if CLIENTS:
                p, v = _price(), await markers()
                msg = json.dumps({"type": "pulse", "t": iso(now_utc()), "price": p, "v": v, "clients": len(CLIENTS)}, default=str)
                for c in list(CLIENTS.values()):
                    q = c["q"]
                    if q.full():
                        try:
                            q.get_nowait()                 # a slow tab only ever gets the newest pulse
                        except asyncio.QueueEmpty:
                            pass
                    q.put_nowait(msg)
                STATS["pulses"] += 1
                STATS["last_pulse"] = iso(now_utc())
        except Exception as e:
            log.warning("pulse: %s", e)
        await asyncio.sleep(1)


async def _user_from_token(token):
    from auth_core import JWT_ALG, load_valid_user, public_user
    p = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[JWT_ALG])
    if p.get("type") != "access":
        raise ValueError("not an access token")
    return public_user(await load_valid_user(p))


async def handle(ws: WebSocket):
    await ws.accept()
    token = ws.cookies.get("access_token")
    try:
        if not token:
            first = json.loads(await asyncio.wait_for(ws.receive_text(), 5))
            token = first.get("token")
        user = await _user_from_token(token)
    except Exception:
        await ws.close(code=4401)
        return
    q = asyncio.Queue(maxsize=2)
    CLIENTS[ws] = {"user": user["email"], "role": user["role"], "q": q, "since": iso(now_utc())}
    STATS["peak_clients"] = max(STATS["peak_clients"], len(CLIENTS))
    try:
        await ws.send_text(json.dumps({"type": "hello", "t": iso(now_utc()), "price": _price(), "v": await markers()}, default=str))
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), 20)
            except asyncio.TimeoutError:
                msg = json.dumps({"type": "ping", "t": iso(now_utc())})
            await ws.send_text(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as e:
        log.info("stream client closed: %s", e)
    finally:
        CLIENTS.pop(ws, None)


def public_stats():
    return {**STATS, "clients": len(CLIENTS)}
