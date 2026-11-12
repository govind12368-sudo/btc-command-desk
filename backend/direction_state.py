"""Persisted, candle-paced confirmation for the existing five-pillar model.

The technical anchor is a completed 15m candle. Trade/book/funding/macro inputs
are sampled when that candle is assessed; they are not historical close-time
observations. Immutable snapshots make that distinction auditable.
"""
import asyncio
import copy
import hashlib
import json
import logging
import math
from datetime import datetime, timezone

from indicators import frame_metrics


log = logging.getLogger("direction_state")
MODEL_VERSION = "five-pillar-15m-confirmed-v2"
STATE_ID = MODEL_VERSION
BAR_SECONDS = 900
CONFIRMATIONS = 2
ENTER_THRESHOLD = 0.15
EXIT_THRESHOLD = 0.05
REQUIRED = ("money_flow", "liquidity", "technical")
DIRECTION_SETTING_KEYS = ("trades_book_age_min", "oi_funding_age_min", "tech_candle_age_min",
                          "max_order_notional", "macro_mode", "strict_macro_age_days",
                          "macro_meta_max_age_min", "usd_cap_h", "y2_cap_h", "brent_cap_h")
_lock = asyncio.Lock()


def _iso(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(value, timezone.utc)
    value = value.replace(tzinfo=value.tzinfo or timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
        except (ValueError, OverflowError):
            return None
    return None


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def technical_grace(source):
    """Permit two polling intervals at a boundary, capped by feed freshness."""
    interval = getattr(source, "interval", 15)
    freshness = getattr(source, "freshness", 120)
    if not _finite(interval) or interval <= 0 or not _finite(freshness) or freshness <= 0:
        return 0
    return min(freshness, max(5, 2 * interval))


def initial_state():
    return {"_id": STATE_ID, "model_version": MODEL_VERSION, "revision": 0,
            "last_bar_close": None, "snapshot_id": None, "context": None,
            "confirmed_direction": None, "confirmed_since": None, "confirmed_context": None,
            "pending_direction": None, "pending_count": 0,
            "validation_needed": True, "invalid_through_bar": None, "pause_reason": None}


def _candidate(score, confirmed):
    # Separate entry and exit bands prevent a small move around the entry
    # threshold from immediately cancelling an already confirmed direction.
    if confirmed == "LONG" and score >= EXIT_THRESHOLD:
        return "LONG"
    if confirmed == "SHORT" and score <= -EXIT_THRESHOLD:
        return "SHORT"
    return "LONG" if score >= ENTER_THRESHOLD else "SHORT" if score <= -ENTER_THRESHOLD else "NEUTRAL"


def pause_state(state, reason, anchor=None):
    new = copy.deepcopy(state)
    known = [x for x in (anchor, state.get("last_bar_close"), state.get("invalid_through_bar")) if x is not None]
    new.update(validation_needed=True, pending_direction=None, pending_count=0,
               invalid_through_bar=max(known) if known else None, pause_reason=reason)
    return new


def advance_state(state, snapshot):
    """Pure transition: repeated, older and skipped candles cannot add votes."""
    new = copy.deepcopy(state)
    anchor, context = snapshot["bar_close"], snapshot["context"]
    last = state.get("last_bar_close")
    if last is not None and anchor <= last:
        return new
    if state.get("invalid_through_bar") is not None and anchor <= state["invalid_through_bar"]:
        return new
    consecutive = last is not None and anchor - last == BAR_SECONDS
    same_context = context == state.get("context")
    if not consecutive or not same_context:
        new.update(pending_direction=None, pending_count=0, validation_needed=True)
    prior = new.get("confirmed_direction") if context == new.get("confirmed_context") else None
    candidate = _candidate(snapshot["score"], prior)
    count = min(CONFIRMATIONS, new.get("pending_count", 0) + 1) if new.get("pending_direction") == candidate else 1
    new.update(last_bar_close=anchor, snapshot_id=snapshot["_id"], context=context,
               pending_direction=candidate, pending_count=count, pause_reason=None)
    if count >= CONFIRMATIONS:
        if new.get("confirmed_direction") != candidate or new.get("validation_needed"):
            new["confirmed_since"] = _iso(anchor)
        new.update(confirmed_direction=candidate, confirmed_context=context, validation_needed=False,
                   invalid_through_bar=None)
    return new


def inspect_inputs(pillars, raw, source, settings, settings_version, now):
    """Check current required data on every tick, even with a frozen snapshot."""
    now_ts = _timestamp(now)
    issues, anchor = [], None
    data = source.data or {}
    if source.health() != "ok":
        issues.append(f"OKX source is {source.health()}: {source.error or 'not healthy'}")
    retrieved = _timestamp(source.last_success)
    if retrieved is None or not -5 <= now_ts - retrieved <= source.freshness:
        issues.append("source retrieval timestamp missing, stale or in the future")
    for key in REQUIRED:
        p = pillars.get(key) or {}
        if not p.get("valid") or not _finite(p.get("score")):
            issues.append(f"{key}: {p.get('reason') or 'required input unavailable'}")
    for key, max_age in (("money_flow", settings["trades_book_age_min"] * 60),
                         ("liquidity", settings["trades_book_age_min"] * 60)):
        observed = _timestamp((pillars.get(key) or {}).get("observed_at"))
        if observed is None or not -5 <= now_ts - observed <= max_age:
            issues.append(f"{key}: invalid or stale observation timestamp")
    bars = data.get("candles_15m") or []
    if bars:
        anchor = bars[-1].get("t")
        anchor = anchor + BAR_SECONDS if _finite(anchor) else None
    if len(bars) < 51:
        issues.append("fewer than 51 completed technical candles")
    else:
        previous = None
        for b in bars:
            if not all(_finite(b.get(k)) for k in ("t", "o", "h", "l", "c", "v")):
                issues.append("non-finite technical candle values")
                break
            if (b["t"] % BAR_SECONDS or b["t"] + BAR_SECONDS > now_ts or
                    min(b["o"], b["h"], b["l"], b["c"]) <= 0 or b["v"] < 0 or
                    b["h"] < max(b["o"], b["c"]) or b["l"] > min(b["o"], b["c"]) or b["l"] > b["h"]):
                issues.append("invalid, future, or unclosed technical candle")
                break
            if previous is not None and b["t"] - previous != BAR_SECONDS:
                issues.append("technical candle sequence has a gap, duplicate or reversed timestamp")
                break
            previous = b["t"]
        if not issues:
            metrics = frame_metrics(bars, BAR_SECONDS)
            if not metrics.get("valid"):
                issues.extend(metrics.get("issues") or ["technical indicators invalid"])
    candle_max_age = settings["tech_candle_age_min"] * 60 + technical_grace(source)
    if anchor is None or not 0 <= now_ts - anchor <= candle_max_age:
        issues.append("completed 15m candle is missing, stale or in the future")
    observed_close = _timestamp((pillars.get("technical") or {}).get("observed_at"))
    if observed_close != anchor:
        issues.append("technical pillar timestamp does not match its completed candle")
    if not _finite(raw.get("score")) or raw.get("direction") == "WAIT":
        issues.append(raw.get("reason") or "model score unavailable")
    valid_mask = sorted(k for k, p in pillars.items() if p.get("valid"))
    # The global revision also changes when an owner edits news/event settings.
    # Only settings used by the direction inputs may restart confirmation.
    context_doc = {"model": MODEL_VERSION, "source": source.key, "url": source.url,
                   "instrument": data.get("instrument"), "valid_pillars": valid_mask,
                   "active_shares": raw.get("active_shares", {}),
                   "direction_settings": {key: settings.get(key) for key in DIRECTION_SETTING_KEYS}}
    context = _digest(context_doc)
    quality = {"valid": not issues, "issues": list(dict.fromkeys(issues)), "required_pillars": list(REQUIRED),
               "valid_pillars": valid_mask, "current_coverage": raw.get("coverage", 0),
               "partial": raw.get("partial", True), "checked_at": _iso(now), "source_health": source.health()}
    quality.update(technical_max_age_sec=candle_max_age, boundary_grace_sec=technical_grace(source))
    return quality, anchor, context, context_doc


def _public(raw, state, snapshot, quality, reason=None):
    out = copy.deepcopy(snapshot["result"] if snapshot else raw)
    confirmed = state.get("confirmed_direction")
    ready = bool(quality["valid"] and not reason and confirmed and not state.get("validation_needed"))
    pending = state.get("pending_direction")
    if reason:
        status = "PAUSED"
    elif not ready:
        status = "CONFIRMING"
        reason = f"waiting for {CONFIRMATIONS} consecutive completed 15m candles; {state.get('pending_count', 0)}/{CONFIRMATIONS} confirmed"
    elif pending != confirmed:
        status = "CHANGE PENDING"
        reason = f"{pending} candidate {state.get('pending_count', 0)}/{CONFIRMATIONS}; retaining confirmed {confirmed} bias"
    else:
        status = "CONFIRMED"
        reason = "confirmed on consecutive completed 15m candles; refreshes do not add confirmations"
    if not quality["valid"]:
        out.update(score=None, strength=None)
    out.update(direction=confirmed if ready else "WAIT", status=status, reason=reason,
               confirmed_direction=confirmed, confirmed_since=state.get("confirmed_since"),
               candidate_direction=pending, pending_count=state.get("pending_count", 0),
               confirmation_required=CONFIRMATIONS, data_quality=quality,
               snapshot_id=snapshot.get("_id") if snapshot else state.get("snapshot_id"),
               candle_close=_iso(snapshot["bar_close"]) if snapshot else _iso(state.get("last_bar_close")),
               assessed_at=snapshot.get("assessed_at") if snapshot else None,
               provenance=snapshot.get("provenance") if snapshot else None,
               input_hash=snapshot.get("input_hash") if snapshot else None,
               model_version=MODEL_VERSION, state_persisted=True,
               last_confirmed_direction=confirmed, decision_paused=not ready,
               policy={"timeframe": "15m", "consecutive_closed_candles": CONFIRMATIONS,
                       "entry_threshold": ENTER_THRESHOLD, "exit_threshold": EXIT_THRESHOLD},
               tilt_label="model tilt on a −1…+1 scale; not a measured win probability",
               observation_note="15m technical close anchors an immutable assessment. Trade/book/funding/macro values are sampled at assessment time, not reconstructed candle-close observations.")
    return out


async def _store_state(collection, state, updated):
    if state == updated:
        latest = await collection.find_one({"_id": STATE_ID, "revision": state["revision"]})
        return latest is not None
    values = {k: v for k, v in updated.items() if k != "_id"}
    values["revision"] = state["revision"] + 1
    result = await collection.update_one({"_id": STATE_ID, "revision": state["revision"]}, {"$set": values})
    return result.matched_count == 1


async def stable_direction(db, pillars, raw, source, settings, settings_version, now):
    """Fail closed on storage failure; use Mongo CAS as well as process locking."""
    async with _lock:
        try:
            quality, anchor, context, context_doc = inspect_inputs(pillars, raw, source, settings, settings_version, now)
            # Source adapters refresh independently. Copy the assessed inputs
            # before the first await so the stored history cannot move ahead.
            assessed_pillars, assessed_raw = copy.deepcopy(pillars), copy.deepcopy(raw)
            assessed_bars = copy.deepcopy((source.data or {}).get("candles_15m") or [])
            assessed_retrieved = _iso(source.last_success)
            states, snapshots = db.direction_state, db.direction_snapshots
            await states.update_one({"_id": STATE_ID}, {"$setOnInsert": initial_state()}, upsert=True)
            for _ in range(4):
                state = await states.find_one({"_id": STATE_ID})
                if not state:
                    raise RuntimeError("direction state could not be read")
                last_snapshot = await snapshots.find_one({"_id": state["snapshot_id"]}) if state.get("snapshot_id") else None
                reason = "; ".join(quality["issues"]) if not quality["valid"] else None
                if not reason and state.get("last_bar_close") is not None and anchor < state["last_bar_close"]:
                    reason = "received candle is older than the committed direction state"
                # An optional pillar disappearing/returning changes the model's
                # denominator. Pause rather than reweighting the same candle.
                if not reason and state.get("context") and context != state["context"] and anchor <= (state.get("last_bar_close") or 0):
                    reason = "input source, coverage or settings changed within this candle; awaiting fresh closed-candle confirmation"
                if reason:
                    safe_anchor = anchor if anchor is not None and anchor <= _timestamp(now) else None
                    updated = pause_state(state, reason, safe_anchor)
                    if await _store_state(states, state, updated):
                        return _public(raw, updated, last_snapshot, quality, reason)
                    continue
                if state.get("invalid_through_bar") is not None and anchor <= state["invalid_through_bar"]:
                    return _public(raw, state, last_snapshot, quality, "data recovered; awaiting a new completed 15m candle before confirmation restarts")
                sid = f"{MODEL_VERSION}:{int(anchor)}"
                payload = {"context": context_doc, "pillars": assessed_pillars, "result": assessed_raw,
                           "candle_history": assessed_bars}
                doc = {"_id": sid, "model_version": MODEL_VERSION, "bar_close": anchor,
                       "assessed_at": _iso(now), "context": context, "score": raw["score"],
                       "result": assessed_raw, "pillars": assessed_pillars,
                       "candle_history": assessed_bars,
                       "input_hash": _digest(payload),
                       "provenance": {**context_doc, "settings_version": settings_version,
                                      "candle_close": _iso(anchor), "retrieved_at": assessed_retrieved,
                                      "sample_times": {k: v.get("observed_at") for k, v in assessed_pillars.items()}}}
                await snapshots.update_one({"_id": sid}, {"$setOnInsert": doc}, upsert=True)
                snapshot = await snapshots.find_one({"_id": sid})
                if not snapshot:
                    raise RuntimeError("immutable direction snapshot could not be read")
                # A first writer with a different coverage mask wins this candle;
                # never overwrite it or silently assess another composition.
                if snapshot["context"] != context:
                    reason = "current input coverage differs from the immutable candle snapshot; awaiting a new completed candle"
                    updated = pause_state(state, reason, anchor)
                    if await _store_state(states, state, updated):
                        return _public(raw, updated, snapshot, quality, reason)
                    continue
                updated = advance_state(state, snapshot)
                if await _store_state(states, state, updated):
                    return _public(raw, updated, snapshot, quality)
            raise RuntimeError("direction state changed concurrently; retry on next compute")
        except Exception as exc:
            log.exception("persisted direction unavailable")
            quality = {"valid": False, "issues": [f"direction persistence unavailable: {type(exc).__name__}"],
                       "current_coverage": raw.get("coverage", 0), "checked_at": _iso(now)}
            out = _public(raw, initial_state(), None, quality, quality["issues"][0])
            out["state_persisted"] = False
            return out
