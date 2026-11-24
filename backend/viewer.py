"""What a normal user (non-admin) account may receive (owner, 24 Nov 2026).

Two protections, both applied on the server so that hiding something on a page is never the only barrier:

1. Pages an admin hid for all users (Customize menu → "All users"): the page's own data endpoints answer 403 to non-admins.
   An endpoint shared with a page that is still visible stays open (the BTC Dashboard is always visible).
2. Strategy pages a user can open (Flush Short, Elite 4 Edge, Option Picks, Fund Manager) show signals, trades and results
   only. The rules, parameters, skip reasons and "how it works" text are removed from the data before it leaves the server.
   The functions below are pure: they return a new, reduced copy and never change the engine's own state.

Admins always get the full data.
"""
import time

from fastapi import Depends, HTTPException

from auth_core import current_user

_CACHE = {"at": 0.0, "hidden": frozenset()}


async def hidden_for_users():
    """Pages hidden for all users (cached a few seconds: this runs on every guarded request)."""
    if time.time() - _CACHE["at"] > 5:
        import ui_prefs
        _CACHE.update(at=time.time(), hidden=frozenset(await ui_prefs.global_hidden()))
    return _CACHE["hidden"]


def forget_cache():
    _CACHE["at"] = 0.0


def page(*paths):
    """Dependency for a page's data endpoint: users get 403 while every page that uses it is hidden for all users."""
    async def dep(u=Depends(current_user)):
        if u["role"] != "admin":
            hidden = await hidden_for_users()
            if paths and all(p in hidden for p in paths):
                raise HTTPException(403, "This page is not available on your account")
        return u
    return dep


def is_admin(u):
    return (u or {}).get("role") == "admin"


def _quiet(res):
    """Users are told an update is late, not the raw error text (it can name hosts, keys or internals)."""
    if res.get("error"):
        res["error"] = "delayed"
    return res


def _keep(d, keys):
    return {k: d[k] for k in keys if k in (d or {})}


def _drop(d, keys):
    return {k: v for k, v in (d or {}).items() if k not in keys}


# ---------------------------------------------------------------- BTC Dashboard
def desk_view(out):
    """No day-type switch card for users: its books and the Auto-Trade state are not sent."""
    return {**out, "books": None, "auto": None, "support_now": None, "viewer": True}


# ---------------------------------------------------------------- Flush Short
FLUSH_ROW = ("signal_id", "time_ist", "period", "rule", "status", "result", "trade_side", "entry", "sl", "tp", "size", "fill_ist", "exit_ist", "exit_price",
             "mark", "gross", "fees", "net", "r", "running_total")


def flush_view(d):
    """Trades and results only: no skipped signals, no RSI / ATR / trend readings, no reasons, no settings, no evidence text."""
    out = _drop(d, ("settings", "evidence", "signals_checked"))
    if d.get("rows") is not None:
        out["rows"] = [_keep(r, FLUSH_ROW) for r in d["rows"] if r.get("status") in ("OPEN", "CLOSED")]
    if d.get("market_now"):
        out["market_now"] = {"active": bool(d["market_now"].get("active"))}
    if d.get("daily") is not None:
        out["daily"] = [_drop(x, ("signals",)) for x in d["daily"]]
    out["viewer"] = True
    return _quiet(out)


# ---------------------------------------------------------------- Elite 4 Edge
ELITE_TRADE_DROP = ("reason", "name", "d", "rr", "chg30", "signal_rule", "rule")


def _elite_journal(j):
    if not j:
        return j
    out = dict(j)
    out["trades"] = [_drop(r, ELITE_TRADE_DROP) for r in j.get("trades") or []]
    out["per_strategy"] = [_drop(s, ("name", "rule", "signal_rule")) for s in j.get("per_strategy") or []]
    out.pop("backtest_skips", None)
    return out


def elite_live_view(lv):
    if not lv:
        return lv
    out = _drop(lv, ("real_money", "events", "compare", "signals"))
    out["events"], out["compare"] = [], None
    out["signals"] = [_keep(s, ("id", "t", "ist", "key", "strategy", "wb", "side", "status", "market")) for s in lv.get("signals") or [] if s.get("status") in ("TAKEN", "TAKING")]
    out["trades"] = [_drop(t, ELITE_TRADE_DROP + ("exchange", "order_id", "sl_order_id", "tp_order_id")) for t in lv.get("trades") or []]
    if lv.get("open_trade"):
        out["open_trade"] = _drop(lv["open_trade"], ELITE_TRADE_DROP + ("time_left_h", "time_exit_t", "exchange", "order_id", "sl_order_id", "tp_order_id"))
    if lv.get("book"):
        out["book"] = _drop(lv["book"], ("drawdown", "peak", "limit"))
    if lv.get("halt"):
        out["halt"] = {"at": lv["halt"].get("at"), "reason": "stopped by the desk"}
    return out


def elite_view(out):
    """Journals and live trades with the strategies' short codes only: no rule cards, names, filters, reasons, settings or logs."""
    res = dict(out)
    res["strategies"] = [_keep(s, ("key", "wb", "priority")) for s in out.get("strategies") or []]
    res["settings"] = _keep(out.get("settings") or {}, ("paper_capital",))
    for k in ("A", "B"):
        if k in out:
            res[k] = _elite_journal(out[k])
    if "live" in out:
        res["live"] = elite_live_view(out["live"])
    res["viewer"] = True
    return _quiet(res)


# ---------------------------------------------------------------- Option Picks
OPT_LEG_DROP = ("stop", "stop_ist", "high", "cap", "taker")
OPT_TRADE_DROP = ("chg_1731", "net_v2", "skip", "reason")


def _opt_trade(t):
    out = _drop(t, OPT_TRADE_DROP)
    if t.get("legs"):
        out["legs"] = [_drop(l, OPT_LEG_DROP) for l in t["legs"]]
    return out


def optpicks_view(out):
    """Paper and backtest trades and totals: no strategy name, code, entry / stop rule, verdict, sizing formula or method notes."""
    res = _drop(out, ("not_checked", "sizing", "size_label", "next_entry"))
    res["strategies"] = [{**_keep(s, ("key", "pick", "capital", "enabled")), "name": f"Pick {s.get('pick')}"} for s in out.get("strategies") or []]
    bt = {}
    for k, b in (out.get("backtest") or {}).items():
        if not b:
            bt[k] = b
            continue
        x = dict(b)
        x["trades"] = [_opt_trade(t) for t in b.get("trades") or []]
        x["realdata"] = [_drop(r, ("verdict", "size", "view2_net")) for r in b.get("realdata") or []]
        if b.get("stats"):
            x["stats"] = _drop(b["stats"], ("view2_net",))
        bt[k] = x
    res["backtest"] = bt
    res["live"] = [_opt_trade(t) for t in out.get("live") or [] if t.get("status") in ("OPEN", "CLOSED")]
    res["live_stats"] = {k: _drop(v, ("view2_net",)) for k, v in (out.get("live_stats") or {}).items()}
    res["compare"] = [_drop(c, ("bt_reason", "live_reason")) for c in out.get("compare") or [] if c.get("live_status") in ("OPEN", "CLOSED")]
    res["viewer"] = True
    return _quiet(res)


# ---------------------------------------------------------------- Fund Manager (if an admin shows it to users)
def fund_view(out):
    """Fund value, allocation, journal and history. No strategy descriptions, signal-check details, plan reasoning or Delta balance."""
    res = _drop(out, ("why_defaults", "exchange", "defaults"))
    res["strategies"] = [_drop(s, ("what",)) for s in out.get("strategies") or []]
    res["decisions"] = [d for d in out.get("decisions") or [] if d.get("key") in ("fund", "real")]
    if out.get("integrity"):
        res["integrity"] = {"summary": out["integrity"].get("summary")}
    if out.get("history"):
        res["history"] = _drop(out["history"], ("flush_note",))
    res["events"] = [e for e in out.get("events") or [] if e.get("kind") in ("start", "target", "closed", "taken")]
    res["viewer"] = True
    return _quiet(res)
