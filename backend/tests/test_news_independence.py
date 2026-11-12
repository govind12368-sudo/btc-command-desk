"""News/calendar/review must never enter market eligibility or delay computation."""
import asyncio
import copy
import importlib.util
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
BACKEND = Path(__file__).resolve().parents[1]
def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BACKEND / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
fixture = load("news_independence_strategy_fixtures", "tests/test_strategy_policy.py")
core, strategy = fixture.load_actual_modules()
indicators = load("news_independence_indicators", "indicators.py")
with patch.dict(sys.modules, {"indicators": indicators}):
    direction = load("news_independence_direction", "direction_state.py")
with patch.dict(sys.modules, {"core": core, "indicators": indicators, "direction_state": direction}):
    models = load("news_independence_models", "models_calc.py")
class Cursor:
    def __aiter__(self):
        self.rows = iter(())
        return self
    async def __anext__(self):
        try:
            return next(self.rows)
        except StopIteration:
            raise StopAsyncIteration
class ReadinessTests(unittest.TestCase):
    def strategy_result(self, failed=()):
        checks = [{"id": key, "status": "FAIL" if key in failed else "PASS"}
                  for key in ["F26", "F27", "F28", "F29", "F30", "F32", "F33", "F34", "F35", "F36", "F37"]]
        return {"checks": checks, "passed": sum(c["status"] == "PASS" for c in checks)}
    def readiness(self, scalp, swing, coverage=5, limits=True, risk=True):
        with patch.dict(sys.modules, {"strategy": strategy}):
            return models.readiness(scalp, swing, coverage, limits, risk)
    def test_full_market_completion_reaches_one_without_news_component(self):
        good = self.strategy_result()
        result = self.readiness(good, good)
        self.assertAlmostEqual(result["score"], 1)
        self.assertAlmostEqual(sum(x["weight"] for x in result["components"].values()), 1)
        self.assertEqual(set(result["excluded_inputs"]), {"news", "calendar", "daily_review"})
        self.assertNotIn("news_event_flow_gate", result["components"])
    def test_actual_market_and_risk_failures_still_lower_readiness(self):
        good = self.strategy_result()
        full = self.readiness(good, good)["score"]
        for result in [self.readiness(self.strategy_result(("F27",)), good),
                       self.readiness(good, good, coverage=3),
                       self.readiness(good, good, limits=False),
                       self.readiness(good, good, risk=False)]:
            self.assertLess(result["score"], full)
class ComputeIndependenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = copy.deepcopy(core.DEFAULT_SETTINGS)
        source = lambda: types.SimpleNamespace(data={}, last_success=fixture.NOW)
        self.sources = types.ModuleType("sources")
        self.sources.REGISTRY = {key: source() for key in ["delta_product", "delta_candles", "delta_ticker", "okx"]}
        self.sources.WS = {"trades": [], "status": "connected"}
        self.core = types.ModuleType("core")
        for key in ["now_utc", "iso", "parse_iso", "age_sec", "ist_date", "finite", "tick_ceil", "tick_floor"]:
            setattr(self.core, key, getattr(core, key))
        self.core.db = types.SimpleNamespace(fills=types.SimpleNamespace(find=lambda: Cursor()),
                                             swing_plans=types.SimpleNamespace(find_one=AsyncMock(return_value=None)))
        self.core.get_settings = AsyncMock(return_value=(self.settings, 1))
        self.st = types.ModuleType("strategy")
        good = ReadinessTests().strategy_result()
        self.st.evaluate = lambda name, context, settings, *args: {
            **copy.deepcopy(good), "strategy": name, "status": "BUY", "direction": "LONG",
            "levels": {"direction": "LONG"}, "failing": [], "plan_id": None}
        self.st.validate_settings = lambda settings: []
        self.st.MANDATORY = strategy.MANDATORY
        self.mc = types.ModuleType("models_calc")
        for key in ["macro_block", "five_pillars", "live_direction", "four_factor", "flow_observation"]:
            setattr(self.mc, key, lambda *args: {})
        self.mc.six_factor = lambda *args: {"complete": False}
        self.mc.readiness = models.readiness
        self.ds = types.ModuleType("direction_state")
        self.ds.stable_direction = AsyncMock(return_value={"direction": "LONG", "score": .4, "coverage": 5})
        self.journal = types.ModuleType("journal")
        self.journal.today_limits = lambda *args: {"issues": []}
        self.validation = types.ModuleType("validation")
        self.validation.mtf_alignment = lambda *args: {}
        self.module_patch = patch.dict(sys.modules, {"core": self.core, "sources": self.sources,
                                                   "indicators": indicators, "strategy": self.st,
                                                   "models_calc": self.mc, "direction_state": self.ds,
                                                   "journal": self.journal, "validation": self.validation})
        self.module_patch.start()
        self.engine = load("news_independence_engine", "engine.py")
        self.engine.frames = lambda: {"1h": {"valid": True}}
        self.engine.quote = lambda: {"bid": 100, "ask": 101}
        self.engine.maybe_capture_plan = AsyncMock()
        self.engine.maybe_capture_signal = AsyncMock()
        self.engine.log_direction = AsyncMock()
    async def asyncTearDown(self):
        task = self.engine._REVIEW_TASK
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.module_patch.stop()
    async def compute(self):
        await asyncio.wait_for(self.engine.compute(), .5)
        state = self.engine.STATE
        return copy.deepcopy({key: state[key] for key in ["decision", "live_direction", "readiness", "scalp", "swing"]})
    async def test_blocked_missing_or_failed_review_cannot_change_decision_or_direction(self):
        baseline = None
        for status in ["PASS", "REVIEW", "BLOCKED", "UNAVAILABLE"]:
            self.engine.STATE["review"] = {"status": status, "ok": status == "PASS", "gate_fraction": 0}
            self.engine._REVIEW_NEXT_REFRESH = float("inf")
            result = await self.compute()
            if baseline is None:
                baseline = result
            self.assertEqual(result, baseline)
        self.assertEqual(baseline["decision"]["decision"], "BUY")
        self.assertEqual(baseline["live_direction"]["direction"], "LONG")
        self.assertEqual(self.engine.maybe_capture_signal.await_count, 8)
    async def test_news_database_failure_does_not_stop_market_compute_or_capture(self):
        self.engine.review_status = AsyncMock(side_effect=RuntimeError("news database unavailable"))
        with self.assertLogs(self.engine.log, level="WARNING"):
            result = await self.compute()
            await self.engine._REVIEW_TASK
        self.assertEqual(result["decision"]["decision"], "BUY")
        self.assertEqual(self.ds.stable_direction.await_count, 1)
        self.assertEqual(self.engine.maybe_capture_signal.await_count, 2)
        self.assertEqual(self.engine.STATE["review"]["status"], "UNAVAILABLE")
        self.assertFalse(self.engine.STATE["review"]["affects_direction"])
    async def test_slow_calendar_review_is_not_awaited_by_market_compute(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        async def slow_review(*args):
            entered.set()
            await release.wait()
            return {"status": "BLOCKED"}
        self.engine.review_status = slow_review
        result = await self.compute()
        await asyncio.wait_for(entered.wait(), .5)
        self.assertEqual(result["decision"]["decision"], "BUY")
        self.assertFalse(self.engine._REVIEW_TASK.done())
        release.set()
        await self.engine._REVIEW_TASK
        self.assertFalse(self.engine.STATE["review"]["affects_decision"])
    async def test_advisory_review_has_bounded_timeout(self):
        self.engine._REVIEW_TIMEOUT_SECONDS = .01
        async def hang(*args):
            await asyncio.sleep(10)
        self.engine.review_status = hang
        with self.assertLogs(self.engine.log, level="WARNING"):
            await self.engine._refresh_advisory_review(self.settings)
        self.assertEqual(self.engine.STATE["review"]["status"], "UNAVAILABLE")
if __name__ == "__main__":
    unittest.main()
