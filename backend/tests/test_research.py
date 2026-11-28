"""Research & Edge page (owner, 28 Nov 2026): counts add up, results come from the strategies' own data, and no rules are shown."""
import asyncio
import json

import research


def test_overview_totals_add_up_and_show_no_rules(monkeypatch):
    async def no_kept():
        return []
    monkeypatch.setattr(research, "kept", no_kept)
    out = asyncio.run(research.overview())
    assert out["totals"]["tested"] == sum(f["tested"] for f in out["funnel"]) == 47016
    assert out["totals"]["kept"] == sum(f["kept"] for f in out["funnel"]) == 8
    assert out["totals"]["years"] >= 3.9 and all(d["years"] > 0 for d in out["data"])
    text = json.dumps({k: out[k] for k in ("data", "funnel", "steps", "note")})
    for w in ("RSI", "ATR", "SMA"):
        assert w not in text, w
    for w in ("straddle", "candles in a row", "17:00", "5%", "2x"):
        assert w not in text.lower(), w


def test_row_return_is_net_over_capital_and_per_year():
    r = research._row("X", "src", "2024-01-01", "2026-01-01", 100, 55, 2000.0, 4000.0, "Paper trading")
    assert r["win_rate"] == 0.55 and r["return"] == 0.5 and r["years"] == 2.0 and abs(r["per_year"] - 0.25) < 1e-9
    r = research._row("Z", "src", "2024-01-01", "2026-01-01", 100, 55, 12000.0, 4000.0, "Paper trading", compound=True)
    assert abs(r["per_year"] - 1.0) < 1e-9                       # 4x in 2 years compounded = doubling each year
    r = research._row("Y", "src", None, None, 0, 0, None, 4000.0, "Paper trading")
    assert r["win_rate"] is None and r["return"] is None and r["per_year"] is None


def test_kept_reads_option_picks_from_its_own_rows(monkeypatch):
    import optpicks as op
    import straddle

    async def no_backtest(bot="sb"):
        raise RuntimeError("no workbook")
    monkeypatch.setattr(straddle, "backtest", no_backtest)
    rows = op.STATE.get("rows")
    op.STATE["rows"] = None
    try:
        out = asyncio.run(research.kept())
        assert not any(r["strategy"].startswith("Option Picks") for r in out)
    finally:
        op.STATE["rows"] = rows


def test_verdict_proven_only_when_every_bar_is_met():
    good = {"2024": 100.0, "2025": 50.0, "2026": 10.0}
    assert research.verdict(160.0, 0.66, 2.9, 860, good) == ("Proven", [])
    assert research.verdict(160.0, 0.41, 3.9, 498, good) == ("Profitable", ["win rate below 50%"])
    assert research.verdict(160.0, 0.6, 2.9, 860, {**good, "2025": -5.0}) == ("Profitable", ["loss in 2025"])
    assert research.verdict(288.0, 0.86, 0.1, 7, {"2026": 288.0}) == ("Profitable", ["less than 2 years tested"])
    assert research.verdict(50.0, 0.6, 2.5, 40, good) == ("Profitable", ["fewer than 100 trades"])
    assert research.verdict(-10.0, 0.6, 3.0, 300, good)[0] == "Not profitable"


def test_row_carries_verdict_and_stage():
    r = research._row("X", "src", "2024-01-01", "2026-06-01", 300, 180, 900.0, 1000.0, "Paper trading", yearly={"2024": 300.0, "2025": 400.0, "2026": 200.0})
    assert r["status"] == "Proven" and r["stage"] == "Paper trading" and r["status_why"] == []
