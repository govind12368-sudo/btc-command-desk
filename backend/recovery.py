"""Bounded recovery of server-owned adapters; never invent fresh market data."""
import asyncio
import logging
import uuid
from datetime import timedelta
from core import db, now_utc, iso, age_sec, parse_iso
import sources
from sources import REGISTRY, WS

log = logging.getLogger("recovery")
RECOVERY = {"last_pass": None, "last_manual_pass": None, "cooldown_sec": 120,
            "last_result": None, "state": "idle", "ws_task": None,
            "current_run_id": None, "history": []}
# This allowlist comes only from installed server code, never request parameters.
ALLOWED_ADAPTER_KEYS = frozenset(REGISTRY)
ADAPTER_TIMEOUT_SEC = 45
PASS_TIMEOUT_SEC = 120
WS_CONNECT_TIMEOUT_SEC = 5
RECOMPUTE_TIMEOUT_SEC = 15
_pass_lock = asyncio.Lock()
_running = set()


async def _run(src):
    # The scheduler and manual recovery share the same atomic claim.
    if src.key in _running:
        return None
    _running.add(src.key)
    try:
        return await src.run()
    finally:
        _running.discard(src.key)


async def scheduler_loop():
    while True:
        now = now_utc()
        for s in REGISTRY.values():
            if s.key in _running or s.fail_count > 0:
                continue
            if s.key == "delta_product" and s.active_until and s.active_until > now and s.last_attempt and age_sec(s.last_attempt) >= 15:
                s.next_run = now
            if now >= s.next_run:
                asyncio.create_task(_run(s))
        await asyncio.sleep(1)


def start_ws():
    task = RECOVERY.get("ws_task")
    if not task or task.done():
        RECOVERY["ws_task"] = asyncio.create_task(sources.delta_ws_loop())


def _ws_down():
    # A connection with no received messages is not verified live data.
    return WS["status"] != "connected" or not WS.get("last_msg") or age_sec(WS["last_msg"]) > 60


def _evidence(src):
    return {"status": src.health(), "adapter_status": src.status,
            "error": src.error, "last_attempt": iso(src.last_attempt),
            "last_success": iso(src.last_success), "data_present": src.data is not None,
            "fail_count": src.fail_count, "next_retry": iso(src.next_run),
            "freshness_sec": src.freshness}


def _new_success(before, src):
    old, new = parse_iso(before["last_success"]), parse_iso(src.last_success)
    return bool(new and (not old or new > old) and src.data is not None
                and src.health() == "ok" and src.fail_count == 0)


async def _repair_one(src, deadline):
    entry = {"source": src.key, "family": src.family, "before": _evidence(src),
             "attempted": False, "action": "recheck installed adapter"}
    now = now_utc()
    if src.key in _running:
        entry.update(outcome="deferred", reason="adapter already running")
    elif getattr(src, "page_only", False) or (src.status == "access_needed" and
            ("not configured" in (src.error or "").lower() or src.next_run > now)):
        entry.update(outcome="configuration_required", reason=src.error or "provider access or input required")
    elif src.status == "rate_limited" and src.next_run > now:
        entry.update(outcome="deferred", reason="provider Retry-After is still active")
    elif deadline <= asyncio.get_running_loop().time():
        entry.update(outcome="deferred", reason="recovery time budget reached")
    else:
        entry["attempted"] = True
        remaining = deadline - asyncio.get_running_loop().time()
        try:
            await asyncio.wait_for(_run(src), timeout=min(ADAPTER_TIMEOUT_SEC, remaining))
            if _new_success(entry["before"], src):
                entry["outcome"] = "fixed"
            elif src.status == "access_needed":
                entry.update(outcome="configuration_required", reason=src.error)
            elif src.status == "rate_limited":
                entry.update(outcome="deferred", reason=src.error or "provider rate limit")
            else:
                entry.update(outcome="still_failing", reason=src.error or "no new verified successful fetch")
        except asyncio.TimeoutError:
            # Retain the last successful cache and its timestamp; mark the failed fetch.
            src.status, src.error = "error", "Recovery adapter fetch timed out"
            src.fail_count += 1
            src.next_run = now_utc() + timedelta(seconds=min(900, src.interval * 2 ** min(src.fail_count, 8)))
            entry.update(outcome="still_failing", reason=src.error)
        except Exception as exc:
            src.status, src.error = "error", f"{type(exc).__name__}: {str(exc)[:200]}"
            src.fail_count += 1
            src.next_run = now_utc() + timedelta(seconds=min(900, src.interval * 2 ** min(src.fail_count, 8)))
            entry.update(outcome="still_failing", reason=src.error)
    entry["after"] = _evidence(src)
    return entry


async def _repair_ws():
    before = {k: WS.get(k) for k in ("status", "last_msg", "connected_at", "error")}
    entry = {"source": "delta_ws", "family": "delta_ws", "before": before,
             "attempted": True, "action": "reconnect Delta public WebSocket"}
    task = RECOVERY.get("ws_task")
    if task and not task.done():
        task.cancel()
        done, pending = await asyncio.wait([task], timeout=2)
        if pending:
            entry.update(outcome="still_failing", reason="old WebSocket task did not stop; duplicate connection avoided")
            entry["after"] = {k: WS.get(k) for k in before}
            return entry
    WS["status"], WS["last_msg"] = "connecting", None
    start_ws()
    deadline = asyncio.get_running_loop().time() + WS_CONNECT_TIMEOUT_SEC
    while _ws_down() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(min(0.1, WS_CONNECT_TIMEOUT_SEC))
    entry["after"] = {k: WS.get(k) for k in before}
    if not _ws_down():
        entry["outcome"] = "fixed"
    else:
        entry.update(outcome="still_failing", reason=WS.get("error") or "no fresh WebSocket message received")
    return entry


async def recovery_pass(manual=False):
    if _pass_lock.locked():
        return {"result": "already running", "run_id": RECOVERY.get("current_run_id"), "state": RECOVERY["state"]}
    async with _pass_lock:
        now = now_utc()
        cooldown_since = RECOVERY.get("last_manual_pass") if manual else RECOVERY["last_pass"]
        if cooldown_since and age_sec(cooldown_since) < RECOVERY["cooldown_sec"]:
            return {"result": "cooling down", "retry_in_sec": max(1, int(RECOVERY["cooldown_sec"] - age_sec(cooldown_since))),
                    "last_run_id": (RECOVERY.get("last_result") or {}).get("run_id")}
        families = {}
        for src in REGISTRY.values():
            if src.key not in ALLOWED_ADAPTER_KEYS:
                continue
            bad = src.fail_count > 0 or src.health() != "ok" or not src.last_success
            # Automatic recovery stays gentle: one due failing family per pass.
            if bad and (manual or (src.fail_count > 0 and src.key not in _running and now >= src.next_run)):
                families.setdefault(src.family, []).append(src)
        ws_bad = _ws_down()
        if not manual and not families and not ws_bad:
            RECOVERY["state"] = "idle"
            return {"result": "nothing to repair"}
        if not manual and families:
            name, group = min(families.items(), key=lambda kv: min(x.next_run for x in kv[1]))
            families = {name: group}
            ws_bad = False
        run_id = uuid.uuid4().hex
        RECOVERY.update(last_pass=iso(now), state="checking", current_run_id=run_id)
        if manual:
            RECOVERY["last_manual_pass"] = iso(now)
        started = asyncio.get_running_loop().time()
        deadline = started + PASS_TIMEOUT_SEC
        try:
            sem = asyncio.Semaphore(3)

            async def repair_family(group):
                # Serialise adapters sharing a provider; unrelated providers may proceed.
                async with sem:
                    return [await _repair_one(src, deadline) for src in group]

            results = await asyncio.gather(*(repair_family(group) for group in families.values()))
            actions = [action for group in results for action in group]
            if ws_bad:
                actions.append(await _repair_ws())
            counts = {"checked": len(actions), "attempted": sum(a["attempted"] for a in actions),
                      "fixed": sum(a["outcome"] == "fixed" for a in actions),
                      "still_failing": sum(a["outcome"] == "still_failing" for a in actions),
                      "configuration_required": sum(a["outcome"] == "configuration_required" for a in actions),
                      "deferred": sum(a["outcome"] == "deferred" for a in actions)}
            remaining = counts["still_failing"] + counts["configuration_required"] + counts["deferred"]
            result = "nothing to repair" if not actions else "recovered" if not remaining else "partially recovered" if counts["fixed"] else "provider access/input needed" if counts["configuration_required"] == len(actions) else "still needs attention"
            entry = {"run_id": run_id, "at": iso(now), "manual": manual,
                     "family": "all" if manual else next(iter(families), "delta_ws"),
                     "action": "full audit and recovery pass" if manual else "recheck due adapter family",
                     "sources": [a["source"] for a in actions], "actions": actions, "counts": counts,
                     "before": {a["source"]: a["before"] for a in actions},
                     "after": {a["source"]: a["after"] for a in actions},
                     "backoff": {a["source"]: a["after"].get("next_retry") for a in actions if a["outcome"] != "fixed"},
                     "result": result, "recomputed": False}
            if counts["fixed"]:
                try:
                    import engine
                    await asyncio.wait_for(engine.compute(), timeout=RECOMPUTE_TIMEOUT_SEC)
                    entry["recomputed"] = True
                except Exception as exc:
                    entry["recompute_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            entry.update(finished_at=iso(now_utc()), duration_sec=round(asyncio.get_running_loop().time() - started, 3))
            try:
                await asyncio.wait_for(db.repair_history.insert_one(dict(entry)), timeout=5)
                entry["history_saved"] = True
            except Exception as exc:
                entry["history_saved"] = False
                entry["history_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                log.exception("could not persist recovery history")
            RECOVERY["last_result"] = entry
            RECOVERY["history"] = (RECOVERY["history"] + [entry])[-20:]
            RECOVERY["state"] = result
            return entry
        finally:
            RECOVERY["current_run_id"] = None
            if RECOVERY["state"] == "checking":
                RECOVERY["state"] = "interrupted"


async def recovery_loop():
    await asyncio.sleep(20)
    while True:
        try:
            await recovery_pass()
        except Exception:
            log.exception("recovery pass failed")
        await asyncio.sleep(10)
