"""Execution V2: per-leg costs (maker limit entry / limit TP / stop-market SL), limit fill tracking,
market-vs-limit backtest, fee migration. Offline only."""
import copy
import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BACKEND / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load("exec_v2_strategy_fixtures", "tests/test_strategy_policy.py")
core, strategy = fixture.load_actual_modules()
indicators = load("exec_v2_indicators", "indicators.py")
with patch.dict(sys.modules, {"core": core}):
    journal = load("exec_v2_journal", "journal.py")
with patch.dict(sys.modules, {"core": core, "indicators": indicators, "journal": journal, "strategy": strategy,
                              "sources": types.ModuleType("sources")}):
    validation = load("exec_v2_validation", "validation.py")

S = copy.deepcopy(core.DEFAULT_SETTINGS)


class CostModelTests(unittest.TestCase):
    def test_legs_limit_and_market(self):
        lim = strategy.exec_legs(S, "scalp")
        self.assertEqual((lim["entry_type"], lim["target_type"]), ("POST-ONLY LIMIT", "LIMIT"))
        self.assertAlmostEqual(lim["entry"], 0.000236)
        self.assertAlmostEqual(lim["stop"], 0.00059 + 0.0002)
        mkt = strategy.exec_legs(S, "scalp", entry_mode=0)
        self.assertEqual((mkt["entry_type"], mkt["target_type"]), ("MARKET", "MARKET"))
        self.assertAlmostEqual(mkt["entry"], mkt["stop"])
        self.assertEqual(strategy.exec_legs(S, "swing")["entry_type"], "MARKET")

    def test_per_leg_maths_long_and_short(self):
        legs = strategy.exec_legs(S, "scalp")
        for d, e, st, tg in (("LONG", 78000.0, 77610.0, 78585.0), ("SHORT", 78000.0, 78390.0, 77415.0)):
            cm = strategy.cost_model(d, e, st, tg, S, legs=legs)
            risk = 390 + e * legs["entry"] + st * legs["stop"] + e * 3 / 10000
            reward = 585 - e * legs["entry"] - tg * legs["target"] - e * 3 / 10000
            self.assertAlmostEqual(cm["risk_per_btc"], risk, places=6)
            self.assertAlmostEqual(cm["reward_per_btc"], reward, places=6)
            self.assertAlmostEqual(cm["net_r"], reward / risk, places=9)
            self.assertTrue(1.05 < cm["net_r"] < 1.08, cm["net_r"])

    def test_swing_flat_model_unchanged(self):
        a = strategy.cost_model("LONG", 100.0, 98.0, 104.0, S)
        c = S["fee_per_side"] + S["slippage_bps"] / 10000
        self.assertAlmostEqual(a["risk_per_btc"], 2 + 198 * c + 100 * 3 / 10000)

    def test_limit_level_rounding_and_validity(self):
        for d in ("LONG", "SHORT"):
            ctx = fixture.StrategyPolicyTests.context(types.SimpleNamespace(st=strategy, core=core, settings=S), "scalp", d)
            ctx["frames"]["5m"]["close"] = 100000.3
            lv = strategy.evaluate("scalp", ctx, S)["levels"]
            self.assertEqual(lv["entry"], 100000.0 if d == "LONG" else 100000.5)
            self.assertEqual(lv["entry_type"], "POST-ONLY LIMIT")
            self.assertEqual(lv["valid_until"], ctx["frames"]["5m"]["bar_close"] + 2 * 300)


def bar(t, o, h, l, c):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": 1}


class LimitFillTests(unittest.TestCase):
    T = 1_800_000_000 - 1_800_000_000 % 300

    def run_fill(self, bars, d="LONG", entry=100.0, stop=98.0, target=103.0):
        return journal.limit_fill_outcome(d, entry, stop, target, 0.5, bars, self.T, 2)

    def test_touch_without_trade_through_is_not_a_fill(self):
        o = self.run_fill([bar(self.T + 300, 100.5, 101, 100.0, 100.6), bar(self.T + 600, 100.6, 101, 99.6, 100.8)])
        self.assertEqual((o["fill_status"], o["outcome"], o["frozen"]), ("MISSED", "NOT FILLED", True))

    def test_trade_through_by_one_tick_fills_then_target(self):
        o = self.run_fill([bar(self.T + 300, 100.2, 100.3, 99.5, 100.1), bar(self.T + 600, 100.1, 103.1, 100.0, 103.0)])
        self.assertEqual((o["fill_status"], o["outcome"]), ("FILLED", "TARGET FIRST"))

    def test_target_on_fill_bar_ignored_stop_on_fill_bar_counts(self):
        o = self.run_fill([bar(self.T + 300, 100.2, 103.5, 99.5, 103.2), bar(self.T + 600, 103.2, 102.9, 99.0, 100.0)])
        self.assertEqual((o["fill_status"], o["outcome"]), ("FILLED", "PENDING"))
        o = self.run_fill([bar(self.T + 300, 100.2, 100.3, 97.9, 98.5)])
        self.assertEqual((o["fill_status"], o["outcome"], o["frozen"]), ("FILLED", "STOP FIRST", True))

    def test_waiting_until_bars_arrive(self):
        o = self.run_fill([bar(self.T + 300, 100.5, 101, 100.2, 100.6)])
        self.assertEqual((o["fill_status"], o["frozen"]), ("WAITING FILL", False))

    def test_short_mirror(self):
        o = self.run_fill([bar(self.T + 300, 99.8, 100.5, 99.7, 99.9), bar(self.T + 600, 99.9, 100.0, 96.9, 97.1)],
                          d="SHORT", entry=100.0, stop=102.0, target=97.0)
        self.assertEqual((o["fill_status"], o["outcome"]), ("FILLED", "TARGET FIRST"))


class BacktestScenarioTests(unittest.TestCase):
    def test_market_and_limit_both_reported(self):
        t0 = 1_800_000_000 - 1_800_000_000 % 14400
        bars, p = [], 80000.0
        for i in range(3000):  # slow trend with noise so some setups appear
            o = p
            p = p * (1 + 0.0004 * math.sin(i / 40) + 0.0006 * math.sin(i / 3))
            bars.append({"t": t0 + i * 300, "o": o, "h": max(o, p) * 1.0004, "l": min(o, p) * 0.9996, "c": p, "v": 100 + 50 * (i % 7)})
        with patch.dict(sys.modules, {"core": core}):  # validation.datetime_ts imports core lazily
            out = validation.run_backtest(bars, S, 0.5)["scalp"]
        self.assertEqual(out["mode"], "limit")
        for k in ("market", "limit"):
            for f in ("signals", "missed_fills", "hit_rate", "expectancy_net_r", "total_net_r", "max_drawdown_r"):
                self.assertIn(f, out[k])
        self.assertEqual(out["market"]["missed_fills"], 0)
        self.assertEqual(out["signals"], out["limit"]["signals"])


class FeeMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def migrate(self, fee):
        settings = types.SimpleNamespace(update_one=AsyncMock(return_value=types.SimpleNamespace(modified_count=1)))
        with patch.object(core, "db", types.SimpleNamespace(settings=settings)):
            return await core._migrate_exec_v2({"_id": "global", "version": 4, "values": {"fee_per_side": fee}})

    async def test_old_default_fee_gets_gst(self):
        out = await self.migrate(0.0005)
        self.assertEqual((out["values"]["fee_per_side"], out["version"]), (0.00059, 5))

    async def test_custom_fee_kept(self):
        out = await self.migrate(0.0004)
        self.assertEqual((out["values"]["fee_per_side"], out["version"]), (0.0004, 4))


if __name__ == "__main__":
    unittest.main()
