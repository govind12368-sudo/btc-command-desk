"""Offline policy tests using actual strategy/indicator/core numeric code."""
import copy
import importlib.util
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
BACKEND = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 28, 9, 0, 10, tzinfo=timezone.utc)
def load_actual_modules():
    # Loading core must never create a network client or use local credentials.
    class OfflineClient:
        def __init__(self, *args, **kwargs):
            pass
        def __getitem__(self, name):
            return types.SimpleNamespace()
    motor = types.ModuleType("motor")
    motor.__path__ = []
    motor_asyncio = types.ModuleType("motor.motor_asyncio")
    motor_asyncio.AsyncIOMotorClient = OfflineClient
    def load(name, filename):
        spec = importlib.util.spec_from_file_location(name, BACKEND / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    with patch.dict(os.environ, {"MONGO_URL": "mongodb://offline.invalid:27017", "DB_NAME": "offline_strategy_test"}), \
            patch.dict(sys.modules, {"motor": motor, "motor.motor_asyncio": motor_asyncio}):
        core = load("strategy_policy_core", "core.py")
    core.now_utc = lambda: NOW
    indicators = load("strategy_policy_indicators", "indicators.py")
    with patch.dict(sys.modules, {"core": core, "indicators": indicators}):
        strategy = load("strategy_policy_under_test", "strategy.py")
    return core, strategy
class StrategyPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core, cls.st = load_actual_modules()
    def setUp(self):
        self.settings = copy.deepcopy(self.core.DEFAULT_SETTINGS)
    def context(self, strat, direction=None):
        cfg = self.st.CFG[strat]
        frames = {}
        for tf, seconds in zip((cfg["lower"], cfg["higher"]), cfg["res"]):
            close = NOW.timestamp() - 10
            m = {"valid": True, "issues": [], "bars": 220, "bar_open": close - seconds,
                 "bar_close": close, "close": 100000.0, "open": 100000.0,
                 "high": 100050.0, "low": 99950.0, "atr14": 1000.0,
                 "volume": 100, "volume_ratio": 1.5, "prior20_high": 100100.0, "prior20_low": 99900.0,
                 "ema20": 100000.0, "ema50": 100000.0, "ema20_prev": 100000.0, "vwap20": 100000.0}
            if direction == "LONG":
                m.update(ema20=99900.0, ema50=99800.0, ema20_prev=99850.0,
                         vwap20=99900.0, prior20_high=99950.0, prior20_low=99500.0)
            elif direction == "SHORT":
                m.update(ema20=100100.0, ema50=100200.0, ema20_prev=100150.0,
                         vwap20=100100.0, prior20_high=100500.0, prior20_low=100050.0)
            frames[tf] = m
        return {"frames": frames,
                "quote": {"bid": 99999.5, "ask": 100000.5, "event_time": self.core.iso(NOW), "source": "Delta fixture"},
                "product": {"symbol": "BTCUSD", "state": "live", "contract_type": "perpetual_futures",
                            "underlying": "BTC", "contract_unit_currency": "BTC", "quoting": "USD",
                            "tick_size": .5, "contract_value": .001},
                "candles_retrieved": NOW, "product_retrieved": NOW,
                "limits": {"issues": [], "detail": "daily controls within limits"}, "open_position": False,
                "scalp_history": {"LONG": 3, "SHORT": 3, "lookback_min": int(self.settings["scalp_confirm_lookback_min"])}}
    @staticmethod
    def checks(result):
        return {c["id"]: c for c in result["checks"]}
    def test_f31_excluded_from_both_complete_qualified_paths_without_review(self):
        for strat in ("scalp", "swing"):
            for direction, status in (("LONG", "BUY"), ("SHORT", "SELL")):
                with self.subTest(strategy=strat, direction=direction):
                    ctx = self.context(strat, direction)
                    # No review key exists: F31 must not remain a hidden gate.
                    result = self.st.evaluate(strat, ctx, self.settings)
                    self.assertEqual(result["status"], status)
                    self.assertEqual(result["passed"], 11)
                    self.assertEqual(result["total_checks"], 11)
                    self.assertEqual(len(result["checks"]), 11)
                    self.assertNotIn("F31", self.checks(result))
                    self.assertNotIn("F31", result["failing"])
                    self.assertNotIn("F31", self.st.MANDATORY)
                    self.assertEqual(result["excluded_checks"][0]["id"], "F31")
                    self.assertTrue(result["mandatory_pass"])
                    self.assertFalse(result["scenario_levels"])
    def test_unqualified_trend_gets_real_scenarios_without_passing_level_checks(self):
        for strat in ("scalp", "swing"):
            with self.subTest(strategy=strat):
                result = self.st.evaluate(strat, self.context(strat), self.settings)
                self.assertEqual(result["status"], "WAIT")
                self.assertIsNone(result["direction"])
                self.assertIsNone(result["levels"])
                self.assertEqual(set(result["scenario_levels"]), {"LONG", "SHORT"})
                for cid in ("F35", "F36", "F37"):
                    check = self.checks(result)[cid]
                    self.assertEqual(check["status"], "WAITING SETUP")
                    self.assertIn("LONG scenario", check["detail"])
                    self.assertIn("SHORT scenario", check["detail"])
                    self.assertNotIn("UNAVAILABLE", check["detail"])
                for side, levels in result["scenario_levels"].items():
                    self.assertTrue(self.st.sided_ok(side, levels["entry"], levels["stop"], levels["target"]))
                    self.assertEqual(levels["quote_time"], self.core.iso(NOW))
                    self.assertEqual(levels["quote_source"], "Delta fixture")
    def test_scenario_sizing_respects_btc_contracts_costs_and_all_limits(self):
        for strat in ("scalp", "swing"):
            ctx = self.context(strat)
            scenarios = self.st.evaluate(strat, ctx, self.settings)["scenario_levels"]
            for side, lv in scenarios.items():
                with self.subTest(strategy=strat, direction=side):
                    self.assertGreater(lv["qty_btc"], 0)
                    self.assertEqual(lv["contracts"], int(lv["contracts"]))
                    self.assertAlmostEqual(lv["qty_btc"], lv["contracts"] * ctx["product"]["contract_value"])
                    self.assertAlmostEqual(lv["notional_usd"], lv["qty_btc"] * lv["entry"])
                    self.assertAlmostEqual(lv["risk_usd"], lv["qty_btc"] * lv["risk_per_btc"])
                    self.assertAlmostEqual(lv["reward_usd"], lv["qty_btc"] * lv["reward_per_btc"])
                    self.assertAlmostEqual(lv["net_r"], lv["reward_per_btc"] / lv["risk_per_btc"])
                    self.assertLessEqual(lv["notional_usd"], self.settings["max_order_notional"] + 1e-8)
                    self.assertLessEqual(lv["risk_usd"], self.settings["capital"] * self.settings["risk_fraction"] + 1e-8)
                    self.assertLessEqual(lv["notional_usd"], self.settings["capital"] * self.settings["leverage_cap"] + 1e-8)
                    self.assertGreater(lv["risk_per_btc"], abs(lv["entry"] - lv["stop"]))
                    for key in ("entry", "stop", "target"):
                        self.assertAlmostEqual(lv[key] / ctx["product"]["tick_size"], round(lv[key] / ctx["product"]["tick_size"]))
    def test_absent_invalid_stale_or_future_inputs_never_fabricate_scenarios(self):
        mutations = {
            "no quote": lambda c: c.update(quote={}),
            "missing ask": lambda c: c["quote"].pop("ask"),
            "crossed book": lambda c: c["quote"].update(bid=100001),
            "non-finite quote": lambda c: c["quote"].update(ask=float("nan")),
            "missing quote time": lambda c: c["quote"].pop("event_time"),
            "stale quote": lambda c: c["quote"].update(event_time=self.core.iso(NOW - timedelta(seconds=121))),
            "future quote": lambda c: c["quote"].update(event_time=self.core.iso(NOW + timedelta(seconds=6))),
            "stale candles": lambda c: c.update(candles_retrieved=NOW - timedelta(seconds=121)),
            "no frames": lambda c: c.update(frames={}),
            "no product": lambda c: c.update(product={}),
            "missing tick": lambda c: c["product"].pop("tick_size"),
            "missing contract value": lambda c: c["product"].pop("contract_value"),
            "wrong instrument": lambda c: c["product"].update(symbol="ETHUSD"),
            "wrong contract type": lambda c: c["product"].update(contract_type="spot"),
            "stale product": lambda c: c.update(product_retrieved=NOW - timedelta(seconds=7201)),
        }
        for strat in ("scalp", "swing"):
            for label, mutate in mutations.items():
                with self.subTest(strategy=strat, bad_input=label):
                    ctx = self.context(strat)
                    mutate(ctx)
                    result = self.st.evaluate(strat, ctx, self.settings)
                    self.assertEqual(result["scenario_levels"], {})
                    self.assertIsNone(result["levels"])
                    self.assertEqual(result["status"], "WAIT")
                    for cid in ("F35", "F36", "F37"):
                        self.assertEqual(self.checks(result)[cid]["status"], "UNAVAILABLE")
                        self.assertTrue(self.checks(result)[cid]["detail"].startswith("Waiting on "))
    def test_scenarios_cannot_make_mandatory_gates_or_direction_qualified(self):
        for strat in ("scalp", "swing"):
            result = self.st.evaluate(strat, self.context(strat), self.settings)
            self.assertTrue(result["scenario_levels"])
            self.assertFalse(result["mandatory_pass"])
            self.assertIn("F28", result["failing"])
            self.assertTrue({"F35", "F36", "F37"}.issubset(result["failing"]))
            self.assertNotIn(result["status"], ("BUY", "SELL", "READY TO CAPTURE"))
    def test_future_retrieval_timestamps_are_rejected_before_scenario_calculation(self):
        for strat in ("scalp", "swing"):
            for key, failing_check in (("candles_retrieved", "F26"), ("product_retrieved", "F34")):
                with self.subTest(strategy=strat, timestamp=key):
                    ctx = self.context(strat)
                    ctx[key] = NOW + timedelta(seconds=6)
                    result = self.st.evaluate(strat, ctx, self.settings)
                    self.assertEqual(self.checks(result)[failing_check]["status"], "FAIL")
                    self.assertFalse(result["scenario_levels"])
                    self.assertIsNone(result["levels"])
                    self.assertEqual(result["status"], "WAIT")
    def test_malformed_frames_cannot_override_validation_with_valid_true(self):
        changes = {
            "missing EMA": lambda m: m.pop("ema20"),
            "non-finite EMA": lambda m: m.update(ema50=float("inf")),
            "missing VWAP": lambda m: m.update(vwap20=None),
            "missing prior range": lambda m: m.pop("prior20_high"),
            "non-finite close": lambda m: m.update(close=float("nan")),
            "zero close": lambda m: m.update(close=0),
            "zero ATR": lambda m: m.update(atr14=0),
            "negative ATR": lambda m: m.update(atr14=-1),
            "short history": lambda m: m.update(bars=50),
            "reversed high low": lambda m: m.update(low=m["high"] + 1),
            "missing candle close": lambda m: m.pop("bar_close"),
            "non-finite candle close": lambda m: m.update(bar_close=float("nan")),
            "malformed candle close": lambda m: m.update(bar_close="bad timestamp"),
        }
        for strat in ("scalp", "swing"):
            for tf in (self.st.CFG[strat]["lower"], self.st.CFG[strat]["higher"]):
                for label, mutate in changes.items():
                    with self.subTest(strategy=strat, frame=tf, invalid=label):
                        ctx = self.context(strat)
                        mutate(ctx["frames"][tf])
                        self.assertTrue(ctx["frames"][tf]["valid"])
                        result = self.st.evaluate(strat, ctx, self.settings)
                        self.assertEqual(self.checks(result)["F27"]["status"], "FAIL")
                        self.assertFalse(result["scenario_levels"])
                        self.assertIsNone(result["levels"])
                        self.assertIsNone(result["higher_trend"])
                        self.assertEqual(result["status"], "WAIT")
                        for cid in ("F35", "F36", "F37"):
                            self.assertEqual(self.checks(result)[cid]["status"], "UNAVAILABLE")

    # ---------- Scalp V2 ----------
    def test_scalp_v2_defaults(self):
        s = self.settings
        self.assertEqual((s["scalp_stop"], s["scalp_target_r"], s["scalp_min_net_r"]), (0.005, 1.5, 0.8))
        self.assertEqual((s["scalp_confirm_signals"], s["scalp_confirm_lookback_min"], s["scalp_max_ext_atr"]), (2, 120, 3.0))
        self.assertEqual(self.st.validate_settings(s), [])

    def test_scalp_needs_earlier_same_direction_setups(self):
        for direction in ("LONG", "SHORT"):
            for hist, ok in ((None, False), ({"LONG": 1, "SHORT": 1, "lookback_min": 120}, False),
                             ({"LONG": 2, "SHORT": 2, "lookback_min": 120}, True),
                             ({"LONG": 5, "SHORT": 5, "lookback_min": 60}, False)):
                with self.subTest(direction=direction, hist=hist):
                    ctx = self.context("scalp", direction)
                    ctx["scalp_history"] = hist
                    f29 = self.checks(self.st.evaluate("scalp", ctx, self.settings))["F29"]
                    self.assertEqual(f29["status"], "PASS" if ok else "FAIL")

    def test_confirmation_can_be_switched_off(self):
        self.settings["scalp_confirm_signals"] = 0
        ctx = self.context("scalp", "LONG")
        ctx["scalp_history"] = None
        self.assertEqual(self.st.evaluate("scalp", ctx, self.settings)["status"], "BUY")

    def test_scalp_blocks_chasing_beyond_max_ema20_extension(self):
        for direction, ema20 in (("LONG", 96000.0), ("SHORT", 104000.0)):  # 4 ATR from close 100000
            with self.subTest(direction=direction):
                ctx = self.context("scalp", direction)
                ctx["frames"]["5m"]["ema20"] = ema20
                ctx["frames"]["5m"]["ema50"] = 95000.0 if direction == "LONG" else 105000.0
                ctx["frames"]["5m"]["ema20_prev"] = ema20
                res = self.st.evaluate("scalp", ctx, self.settings)
                self.assertEqual(self.checks(res)["F36"]["status"], "FAIL")
                self.assertIn("beyond EMA20", self.checks(res)["F36"]["detail"])
                self.assertNotIn(res["status"], ("BUY", "SELL"))

    def test_scalp_net_r_threshold_is_per_strategy(self):
        ctx = self.context("scalp", "LONG")
        res = self.st.evaluate("scalp", ctx, self.settings)
        net_r = res["levels"]["net_r"]
        self.assertTrue(1.0 < net_r < 1.1, net_r)  # limit entry + limit target, stop-market (Execution V2)
        self.assertEqual(self.checks(res)["F35"]["status"], "PASS")
        self.settings["scalp_min_net_r"] = 1.1
        self.assertEqual(self.checks(self.st.evaluate("scalp", ctx, self.settings))["F35"]["status"], "FAIL")
        self.settings.update(scalp_min_net_r=0.8, scalp_entry_mode=0)  # market mode: all legs taker
        market = self.st.evaluate("scalp", ctx, self.settings)
        self.assertTrue(0.8 < market["levels"]["net_r"] < 1.0, market["levels"]["net_r"])
        self.assertEqual(self.checks(market)["F35"]["status"], "PASS")

    def test_scalp_direction_matches_live_f28_f29(self):
        for direction in ("LONG", "SHORT"):
            m = self.context("scalp", direction)["frames"]["5m"]
            self.assertEqual(self.st.scalp_direction(m, "MIXED", self.settings), direction)
            self.assertIsNone(self.st.scalp_direction(m, "DOWN" if direction == "LONG" else "UP", self.settings))
        self.assertIsNone(self.st.scalp_direction(self.context("scalp")["frames"]["5m"], "MIXED", self.settings))


if __name__ == "__main__":
    unittest.main()
