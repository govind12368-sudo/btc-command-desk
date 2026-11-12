"""Scalp V2 data-pipeline fixes: one open model signal per strategy, validation snapshot
throttling, stale macro scored neutral, one-time settings migration. Offline only."""
import copy
import importlib.util
import sys
import types
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BACKEND / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load("pipeline_v2_strategy_fixtures", "tests/test_strategy_policy.py")
core, strategy = fixture.load_actual_modules()
indicators = load("pipeline_v2_indicators", "indicators.py")
with patch.dict(sys.modules, {"indicators": indicators}):
    direction = load("pipeline_v2_direction", "direction_state.py")
with patch.dict(sys.modules, {"core": core, "indicators": indicators, "direction_state": direction}):
    models = load("pipeline_v2_models", "models_calc.py")


class EngineV2Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = copy.deepcopy(core.DEFAULT_SETTINGS)
        self.core = types.ModuleType("core")
        for key in ["now_utc", "iso", "parse_iso", "age_sec", "ist_date", "finite", "tick_ceil", "tick_floor"]:
            setattr(self.core, key, getattr(core, key))
        self.signals = types.SimpleNamespace(count_documents=AsyncMock(return_value=0),
                                             update_one=AsyncMock(return_value=types.SimpleNamespace(upserted_id=None)))
        self.snaps = types.SimpleNamespace(find_one=AsyncMock(return_value=None), insert_one=AsyncMock())
        self.core.db = types.SimpleNamespace(signals=self.signals, validation_snapshots=self.snaps)
        self.core.get_settings = AsyncMock(return_value=(self.settings, 1))
        src = lambda: types.SimpleNamespace(data={}, last_success=fixture.NOW)
        sources = types.ModuleType("sources")
        sources.REGISTRY = {k: src() for k in ["delta_product", "delta_candles", "delta_ticker", "okx"]}
        sources.WS = {"trades": [], "status": "connected"}
        stubs = {name: types.ModuleType(name) for name in ("models_calc", "direction_state", "journal", "validation")}
        self.patch = patch.dict(sys.modules, {"core": self.core, "sources": sources, "indicators": indicators,
                                              "strategy": strategy, **stubs})
        self.patch.start()
        self.engine = load("pipeline_v2_engine", "engine.py")
        self.prod = {"tick_size": 0.5}
        self.q = {"bid": 100000.0, "ask": 100000.5, "event_time": core.iso(fixture.NOW)}

    async def asyncTearDown(self):
        self.patch.stop()

    def ev(self):
        return {"status": "BUY", "strategy": "scalp", "plan_id": None, "checks": [],
                "levels": {"direction": "LONG", "entry": 1.0, "stop": 0.5, "target": 2.0, "qty_btc": 0.1, "net_r": 0.9},
                "frames": {"5m": {"bar_open": 1793600000}}}

    async def test_new_signal_skipped_while_one_is_still_open(self):
        self.signals.count_documents.return_value = 1
        await self.engine.maybe_capture_signal(self.ev(), self.q, None, self.settings)
        self.signals.update_one.assert_not_awaited()

    async def test_signal_captured_when_none_open_or_cap_disabled(self):
        await self.engine.maybe_capture_signal(self.ev(), self.q, None, self.settings)
        self.assertEqual(self.signals.update_one.await_count, 1)
        self.signals.count_documents.return_value = 5
        self.settings["max_open_model_signals"] = 0
        await self.engine.maybe_capture_signal(self.ev(), self.q, None, self.settings)
        self.assertEqual(self.signals.update_one.await_count, 2)

    async def test_snapshot_ignores_near_zero_scores(self):
        await self.engine.log_direction("ORIGINAL_SIX_FACTOR", "LONG", 0.05, self.q, self.settings, self.prod)
        self.snaps.insert_one.assert_not_awaited()
        await self.engine.log_direction("ORIGINAL_SIX_FACTOR", "LONG", 0.30, self.q, self.settings, self.prod)
        self.assertEqual(self.snaps.insert_one.await_count, 1)

    async def test_snapshot_flip_within_same_5m_candle_is_ignored(self):
        now = int(core.now_utc().timestamp())
        self.snaps.find_one.return_value = {"direction": "SHORT", "capture_ts": now - now % 300}
        await self.engine.log_direction("ORIGINAL_SIX_FACTOR", "LONG", 0.30, self.q, self.settings, self.prod)
        self.snaps.insert_one.assert_not_awaited()
        self.snaps.find_one.return_value = {"direction": "SHORT", "capture_ts": now - now % 300 - 300}
        await self.engine.log_direction("ORIGINAL_SIX_FACTOR", "LONG", 0.30, self.q, self.settings, self.prod)
        self.assertEqual(self.snaps.insert_one.await_count, 1)


class MacroStalenessTests(unittest.TestCase):
    def macro(self, usd_age_h, y2_age_h):
        series = lambda age, **kw: {"valid": True, "issues": [], "observation_age_h": age, "latest_value": 1, "latest_date": "2026-10-28", **kw}
        return {"usd": series(usd_age_h, daily_change_pct=-0.5), "y2": series(y2_age_h, daily_change_pp=-0.10),
                "brent": series(10)}

    def pillars(self, macro):
        R = {"okx": types.SimpleNamespace(data=None, error="offline")}
        return models.five_pillars(R, core.DEFAULT_SETTINGS, macro)["macro"]

    def test_fresh_macro_keeps_full_score(self):
        p = self.pillars(self.macro(24, 24))
        self.assertTrue(p["valid"]); self.assertEqual(p["score"], 2)

    def test_stale_series_scored_neutral_but_pillar_stays_valid(self):
        p = self.pillars(self.macro(240, 24))
        self.assertTrue(p["valid"]); self.assertEqual(p["score"], 1)
        self.assertIn("scored neutral", p["detail"])
        p = self.pillars(self.macro(240, 100))
        self.assertTrue(p["valid"]); self.assertEqual(p["score"], 0)


class SettingsMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def migrate(self, values):
        settings = types.SimpleNamespace(update_one=AsyncMock(return_value=types.SimpleNamespace(modified_count=1)))
        with patch.object(core, "db", types.SimpleNamespace(settings=settings)):
            out = await core._migrate_scalp_v2({"_id": "global", "version": 7, "values": values})
        return out, settings.update_one

    async def test_untouched_v1_defaults_move_to_v2(self):
        out, upd = await self.migrate({**core.DEFAULT_SETTINGS, "scalp_stop": 0.003, "scalp_target_r": 2.33})
        self.assertEqual((out["values"]["scalp_stop"], out["values"]["scalp_target_r"]), (0.005, 1.5))
        self.assertEqual(out["version"], 8)
        self.assertEqual(upd.await_args.args[1]["$inc"], {"version": 1})

    async def test_admin_customised_values_are_kept(self):
        out, upd = await self.migrate({**core.DEFAULT_SETTINGS, "scalp_stop": 0.004, "scalp_target_r": 2.0})
        self.assertEqual((out["values"]["scalp_stop"], out["values"]["scalp_target_r"]), (0.004, 2.0))
        self.assertEqual(out["version"], 7)
        self.assertNotIn("$inc", upd.await_args.args[1])

    async def test_runs_only_once(self):
        doc = {"_id": "global", "version": 3, "values": {"scalp_stop": 0.003, "scalp_target_r": 2.33}, "migrations": {"scalp_v2": True}}
        settings = types.SimpleNamespace(update_one=AsyncMock())
        with patch.object(core, "db", types.SimpleNamespace(settings=settings)):
            out = await core._migrate_scalp_v2(doc)
        settings.update_one.assert_not_awaited()
        self.assertEqual(out["values"]["scalp_stop"], 0.003)


if __name__ == "__main__":
    unittest.main()
