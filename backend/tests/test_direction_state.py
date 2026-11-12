"""Direction regression tests use in-memory Mongo-shaped collections only."""
import copy
import logging
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import direction_state as ds
BASE = (1700000000 // 900) * 900
SETTINGS = {"trades_book_age_min": 5, "tech_candle_age_min": 15}
class Collection:
    def __init__(self):
        self.docs = {}
    async def find_one(self, query):
        doc = self.docs.get(query["_id"])
        return copy.deepcopy(doc) if doc and all(doc.get(k) == v for k, v in query.items()) else None
    async def update_one(self, query, update, upsert=False):
        doc = self.docs.get(query["_id"])
        if doc and all(doc.get(k) == v for k, v in query.items()):
            doc.update(copy.deepcopy(update.get("$set", {})))
            return SimpleNamespace(matched_count=1, upserted_id=None)
        if doc is None and upsert:
            self.docs[query["_id"]] = {**query, **copy.deepcopy(update.get("$setOnInsert", {})), **copy.deepcopy(update.get("$set", {}))}
            return SimpleNamespace(matched_count=0, upserted_id=query["_id"])
        return SimpleNamespace(matched_count=0, upserted_id=None)
class Source:
    key, url, freshness, error = "okx", "https://www.okx.com/api/v5", 120, None
    def __init__(self, anchor):
        self.last_success = datetime.fromtimestamp(anchor + 10, timezone.utc)
        self.status = "ok"
        self.data = {"instrument": "BTC-USDT-SWAP", "candles_15m": [
            {"t": anchor - (60 - i) * 900, "o": 100 + i, "c": 100.5 + i,
             "h": 101 + i, "l": 99 + i, "v": 10 + i} for i in range(60)]}
    def health(self):
        return self.status
def inputs(anchor, score=0.4, optional=True):
    source = Source(anchor)
    observed = ds._iso(anchor + 5)
    pillars = {k: {"valid": True, "score": 1, "observed_at": observed, "inputs": {}, "reason": None}
               for k in ("money_flow", "liquidity", "positioning", "macro", "technical")}
    pillars["technical"]["observed_at"] = ds._iso(anchor)
    if not optional:
        pillars["macro"].update(valid=False, score=None, reason="macro unavailable")
    weights = {"money_flow": .2, "liquidity": .15, "positioning": .15, "macro": .2, "technical": .2}
    valid = [k for k, v in pillars.items() if v["valid"]]
    total = sum(weights[k] for k in valid)
    raw = {"coverage": len(valid), "active_shares": {k: weights[k] / total for k in valid},
           "excluded": [k for k in weights if k not in valid], "partial": len(valid) < 5,
           "normalized_scores": {k: 1 for k in valid}, "weights_original": weights,
           "score": score, "strength": "MODERATE", "direction": "LONG" if score >= .15 else "SHORT" if score <= -.15 else "NEUTRAL"}
    return source, pillars, raw
class DirectionTests(unittest.TestCase):
    def _callTestMethod(self, method):
        # All adapters are immediate in-memory coroutines. Drive them without
        # creating an OS event loop/socketpair, keeping these tests offline.
        coroutine = method()
        try:
            coroutine.send(None)
        except StopIteration:
            return
        finally:
            coroutine.close()
        self.fail("in-memory test unexpectedly requested external asynchronous IO")
    def setUp(self):
        self.db = SimpleNamespace(direction_state=Collection(), direction_snapshots=Collection())
    async def assess(self, anchor, score=.4, optional=True, mutate=None, settings=None, settings_version=1):
        source, pillars, raw = inputs(anchor, score, optional)
        if mutate:
            mutate(source, pillars, raw)
        return await ds.stable_direction(self.db, pillars, raw, source, settings or SETTINGS, settings_version,
                                         datetime.fromtimestamp(anchor + 10, timezone.utc))
    async def confirm_long(self):
        first = await self.assess(BASE)
        self.assertEqual(first["direction"], "WAIT")
        self.assertEqual(first["pending_count"], 1)
        second = await self.assess(BASE + 900)
        self.assertEqual(second["direction"], "LONG")
        return second
    async def test_refresh_uses_immutable_snapshot_and_never_adds_confirmation(self):
        first = await self.assess(BASE)
        for _ in range(10):
            again = await self.assess(BASE, -.6)
        self.assertEqual(again["direction"], "WAIT")
        self.assertEqual(again["pending_count"], 1)
        self.assertEqual(again["score"], .4)
        self.assertEqual(again["input_hash"], first["input_hash"])
        self.assertEqual(len(self.db.direction_snapshots.docs), 1)
    async def test_news_event_settings_and_global_revision_cannot_reset_confirmation(self):
        first = await self.assess(BASE)
        news_settings = {**SETTINGS, "critical_news_window_min": 360,
                         "high_news_window_min": 720, "event_before_min": 180,
                         "event_after_min": 120}
        # A revision between the first and second closed candle must still
        # allow the second vote when only information-page settings changed.
        confirmed = await self.assess(BASE + 900, settings=news_settings, settings_version=2)
        self.assertEqual(first["pending_count"], 1)
        self.assertEqual(confirmed["direction"], "LONG")
        self.assertEqual(confirmed["pending_count"], 2)
        again = await self.assess(BASE + 900, settings={**news_settings, "event_before_min": 5},
                                  settings_version=3)
        self.assertEqual(again["direction"], "LONG")
        self.assertEqual(again["status"], "CONFIRMED")
        self.assertEqual(again["input_hash"], confirmed["input_hash"])
        self.assertEqual(again["provenance"]["settings_version"], 2)
    async def test_direction_relevant_setting_change_still_requires_fresh_confirmation(self):
        await self.confirm_long()
        changed_settings = {**SETTINGS, "tech_candle_age_min": 16}
        changed = await self.assess(BASE + 900, settings=changed_settings, settings_version=2)
        self.assertEqual(changed["direction"], "WAIT")
        self.assertEqual(changed["confirmed_direction"], "LONG")
        self.assertEqual(changed["pending_count"], 0)
        self.assertIn("settings changed", changed["reason"])
        first = await self.assess(BASE + 1800, settings=changed_settings, settings_version=2)
        self.assertEqual(first["pending_count"], 1)
        self.assertEqual(first["direction"], "WAIT")
        second = await self.assess(BASE + 2700, settings=changed_settings, settings_version=2)
        self.assertEqual(second["direction"], "LONG")
    async def test_reversal_needs_two_distinct_closes_and_state_survives_caller_restart(self):
        confirmed = await self.confirm_long()
        intrabar = await self.assess(BASE + 900, -.4)
        self.assertEqual(intrabar["score"], .4)
        self.assertEqual(intrabar["confirmed_since"], confirmed["confirmed_since"])
        pending = await self.assess(BASE + 1800, -.4)
        self.assertEqual(pending["direction"], "LONG")
        self.assertEqual(pending["candidate_direction"], "SHORT")
        self.assertEqual(pending["status"], "CHANGE PENDING")
        # No process cache is needed: a new caller reads the same persisted DB.
        done = await self.assess(BASE + 2700, -.4)
        self.assertEqual(done["direction"], "SHORT")
        self.assertEqual(done["confirmed_since"], ds._iso(BASE + 2700))
    async def test_hysteresis_holds_direction_and_neutral_exit_is_confirmed(self):
        await self.confirm_long()
        self.assertEqual((await self.assess(BASE + 1800, .08))["direction"], "LONG")
        one = await self.assess(BASE + 2700, .02)
        self.assertEqual(one["direction"], "LONG")
        self.assertEqual(one["candidate_direction"], "NEUTRAL")
        self.assertEqual((await self.assess(BASE + 3600, .02))["direction"], "NEUTRAL")
    async def test_required_failure_pauses_immediately_and_same_bar_cannot_reactivate(self):
        await self.confirm_long()
        def broken(source, pillars, raw):
            pillars["liquidity"].update(valid=False, reason="stale book")
            raw.update(direction="WAIT", score=None)
        paused = await self.assess(BASE + 900, mutate=broken)
        self.assertEqual(paused["direction"], "WAIT")
        self.assertIsNone(paused["score"])
        self.assertEqual(paused["confirmed_direction"], "LONG")
        self.assertTrue(paused["decision_paused"])
        self.assertEqual((await self.assess(BASE + 900))["direction"], "WAIT")
        self.assertEqual((await self.assess(BASE + 1800))["pending_count"], 1)
        self.assertEqual((await self.assess(BASE + 2700))["direction"], "LONG")
    async def test_coverage_change_does_not_reweight_same_bar_and_restarts_confirmation(self):
        await self.confirm_long()
        changed = await self.assess(BASE + 900, optional=False)
        self.assertEqual(changed["direction"], "WAIT")
        self.assertEqual(changed["data_quality"]["current_coverage"], 4)
        self.assertEqual(changed["pending_count"], 0)
        first = await self.assess(BASE + 1800, optional=False)
        self.assertEqual(first["direction"], "WAIT")
        self.assertEqual(first["coverage"], 4)
        self.assertEqual((await self.assess(BASE + 2700, optional=False))["direction"], "LONG")
    async def test_skipped_candle_cannot_supply_second_consecutive_vote(self):
        await self.assess(BASE)
        skipped = await self.assess(BASE + 1800)
        self.assertEqual(skipped["direction"], "WAIT")
        self.assertEqual(skipped["pending_count"], 1)
        self.assertEqual((await self.assess(BASE + 2700))["direction"], "LONG")
    async def test_future_candle_and_gap_fail_closed_without_poisoning_future_recovery(self):
        await self.confirm_long()
        def future(source, pillars, raw):
            source.data["candles_15m"][-1]["t"] += 86400 * 365
        invalid = await self.assess(BASE + 1800, mutate=future)
        self.assertEqual(invalid["direction"], "WAIT")
        self.assertTrue(any("future" in issue for issue in invalid["data_quality"]["issues"]))
        self.assertEqual((await self.assess(BASE + 1800))["pending_count"], 1)
        self.assertEqual((await self.assess(BASE + 2700))["direction"], "LONG")
        def gap(source, pillars, raw):
            source.data["candles_15m"].pop(-4)
        self.assertEqual((await self.assess(BASE + 3600, mutate=gap))["direction"], "WAIT")
    async def test_source_failure_pauses_even_with_valid_cached_pillars(self):
        await self.confirm_long()
        def failed(source, pillars, raw):
            source.status = "rate_limited"
            source.error = "429"
        paused = await self.assess(BASE + 900, mutate=failed)
        self.assertEqual(paused["direction"], "WAIT")
        self.assertFalse(paused["data_quality"]["valid"])
    async def test_polling_boundary_grace_preserves_votes_but_true_staleness_pauses(self):
        await self.assess(BASE)
        source, pillars, raw = inputs(BASE)
        boundary_now = datetime.fromtimestamp(BASE + 905, timezone.utc)
        source.last_success = boundary_now
        for key in ("money_flow", "liquidity"):
            pillars[key]["observed_at"] = ds._iso(BASE + 900)
        boundary = await ds.stable_direction(self.db, pillars, raw, source, SETTINGS, 1, boundary_now)
        self.assertTrue(boundary["data_quality"]["valid"])
        self.assertEqual(boundary["pending_count"], 1)
        self.assertEqual(boundary["data_quality"]["boundary_grace_sec"], 30)
        self.assertEqual((await self.assess(BASE + 900))["direction"], "LONG")
        stale_now = datetime.fromtimestamp(BASE + 900 + 931, timezone.utc)
        source, pillars, raw = inputs(BASE + 900)
        source.last_success = stale_now
        for key in ("money_flow", "liquidity"):
            pillars[key]["observed_at"] = ds._iso(BASE + 900 + 930)
        stale = await ds.stable_direction(self.db, pillars, raw, source, SETTINGS, 1, stale_now)
        self.assertEqual(stale["direction"], "WAIT")
        self.assertFalse(stale["data_quality"]["valid"])
    async def test_database_failure_cannot_expose_actionable_raw_direction(self):
        async def fail(*args, **kwargs):
            raise ConnectionError("database offline")
        self.db.direction_state.update_one = fail
        with self.assertLogs("direction_state", level=logging.ERROR):
            paused = await self.assess(BASE)
        self.assertEqual(paused["direction"], "WAIT")
        self.assertFalse(paused["state_persisted"])
        self.assertIsNone(paused["score"])
if __name__ == "__main__":
    unittest.main()
