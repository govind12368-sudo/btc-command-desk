"""Planner prices, sizing and guardrails must not depend on advisory news/reviews."""
import copy
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from test_strategy_policy import load_actual_modules
BACKEND = Path(__file__).resolve().parents[1]
class NoInformationAccess(dict):
    """Catch even an unused attempt to read information-only state."""
    def get(self, key, *args):
        if key in {"review", "news", "events", "calendar"}:
            raise AssertionError(f"Planner must not read advisory {key}")
        return super().get(key, *args)
class PlannerInformationPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core, strategy = load_actual_modules()
        indicator_spec = importlib.util.spec_from_file_location("planner_policy_indicators", BACKEND / "indicators.py")
        indicators = importlib.util.module_from_spec(indicator_spec)
        indicator_spec.loader.exec_module(indicators)
        spec = importlib.util.spec_from_file_location("planner_policy_under_test", BACKEND / "planner.py")
        cls.planner = importlib.util.module_from_spec(spec)
        # Factor flips are outside this test; empty pillars avoid this interface.
        with patch.dict(sys.modules, {"core": cls.core, "indicators": indicators,
                                     "strategy": strategy, "models_calc": types.ModuleType("models_calc")}):
            spec.loader.exec_module(cls.planner)
    def setUp(self):
        self.settings = copy.deepcopy(self.core.DEFAULT_SETTINGS)
        self.bars = [{"t": 1700006400 + i * 86400, "o": 99500 + i * 10,
                      "h": 100100 + i * 10, "l": 99000 + i * 10,
                      "c": 100000 + i * 10, "v": 100} for i in range(60)]
        self.state = {"quote": {"bid": 100589.5, "ask": 100590},
                      "limits": {"issues": []}, "pillars": {}, "live_direction": {}}
        self.product = {"tick_size": 0.5, "contract_value": 0.001}
    def calculate(self, state, direction="LONG", bars=None):
        return self.planner.plan({"direction": direction}, self.settings, state,
                                 self.bars if bars is None else bars, self.product)
    def test_review_and_calendar_states_cannot_change_plan_or_guardrails(self):
        variants = [None, {}, {"status": "BLOCKED", "ok": False, "gate_fraction": 0},
                    {"status": "REVIEW", "ok": False, "detail": "CRITICAL headline"},
                    {"status": "PASS", "ok": True, "gate_fraction": 1},
                    {"status": "UNAVAILABLE", "error": "calendar HTTP 403"}, "malformed"]
        for direction in ("LONG", "SHORT"):
            baseline = self.calculate(self.state, direction)
            self.assertNotIn("error", baseline)
            self.assertEqual(len(baseline["guardrails"]), 9)
            self.assertTrue(all(row["pass"] for row in baseline["guardrails"]))
            for advisory in variants:
                with self.subTest(direction=direction, review=advisory):
                    state = {**self.state, "review": advisory,
                             "news": {"critical": True}, "calendar": {"status": "error"},
                             "events": [{"impact": "CRITICAL", "reviewed": False}]}
                    self.assertEqual(self.calculate(state, direction), baseline)
    def test_information_only_state_is_never_read(self):
        for direction in ("LONG", "SHORT"):
            with self.subTest(direction=direction):
                self.assertEqual(self.calculate(NoInformationAccess(self.state), direction),
                                 self.calculate(self.state, direction))
    def test_market_quality_and_daily_risk_controls_still_fail(self):
        state = copy.deepcopy(self.state)
        state["limits"]["issues"] = ["daily loss limit exceeded"]
        result = self.calculate(state)
        guardrails = {row["name"]: row["pass"] for row in result["guardrails"]}
        self.assertFalse(guardrails["Daily limits/capacity clear"])
        state = copy.deepcopy(self.state)
        state["quote"]["ask"] += 100
        result = self.calculate(state)
        guardrails = {row["name"]: row["pass"] for row in result["guardrails"]}
        self.assertFalse(guardrails[f"Spread ≤ {self.settings['max_spread_bps']} bps"])
        self.assertEqual(self.calculate(self.state, bars=[])["error"],
                         "planner needs valid daily candles, live quote and contract specification")
if __name__ == "__main__":
    unittest.main()
