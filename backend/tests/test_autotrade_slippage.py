"""Slippage protection (admin request, 25 Nov 2026): sizing allowance, big-news block, slippage record and auto-pause."""
from datetime import timedelta

from test_autotrade import env, run, signal, sig_status, FakeExchange  # noqa: F401  (env is the shared fixture)


class SlipExchange(FakeExchange):
    """Fills the entry `entry_slip` points worse and reports the exit given in `.exit`."""

    def __init__(self, entry_slip=0.0):
        super().__init__()
        self.entry_slip, self.exit = entry_slip, None

    async def enter(self, side, n, stop, target, coid, price, cfg):
        got = price + self.entry_slip if side == "buy" else price - self.entry_slip
        self.entries.append(coid)
        self.pos = {"size": n if side == "buy" else -n, "entry": got}
        return {"price": got, "contracts": n, "role": "taker"}

    async def poll_exit(self, t, price, hi, lo, cfg):
        if self.exit:
            self.pos = None
        return self.exit


def _on(env, **kw):
    env.at.DEFAULTS.update({"stop_slip_pct": 0.5, **kw})


async def _event(env, minutes, title="Consumer Price Index", impact="HIGH"):
    await env.db.manual_events.insert_one({"title": title, "impact": impact, "scheduled_utc": env.core.iso(env.clock() + timedelta(minutes=minutes))})
    env.at.forget_news()


def test_sizing_includes_stop_slippage_allowance(env):
    at = env.at
    cfg0, cfg1 = {**at.DEFAULTS, "stop_slip_pct": 0.0}, {**at.DEFAULTS, "stop_slip_pct": 0.5}
    n0, i0 = at.size(72000, 71640, cfg0, 45000, -60)
    n1, i1 = at.size(72000, 71640, cfg1, 45000, -60)
    assert i1["slip_allowance_pts"] == 360 and i0["slip_allowance_pts"] == 0
    assert n1 < n0                                                   # a smaller trade when the loss room is the limit
    assert n1 * i1["worst_per_contract"] <= 24 + 1e-9                # stop + 360-point bad fill + fees still fits the $24 room
    assert (n1 + 1) * i1["worst_per_contract"] > 24


def test_no_new_trade_around_big_news_then_trades_again(env):
    _on(env)
    FakeExchange.NotPlaced = env.at.NotPlaced

    async def go():
        eng = env.engine(SlipExchange())
        await eng.tick(71900)
        await _event(env, 20)                                        # CPI in 20 minutes: inside the 30-minute block
        await signal(env, "SCALP-SHORT-1")
        await eng.tick(72000)
        s = await sig_status(env, "SCALP-SHORT-1")
        assert s["status"] == "SKIPPED" and "big news" in s["reason"] and "Consumer Price Index" in s["reason"]
        assert not await env.db.autotrade_trades.find_one({})
        env.clock.add(minutes=51)                                    # 31 minutes after the release: allowed again
        env.at.forget_news()
        await signal(env, "SCALP-SHORT-2")
        await eng.tick(72000)
        assert (await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-2"}))["status"] == "OPEN"
    run(go())


def test_low_impact_event_does_not_block(env):
    _on(env)

    async def go():
        eng = env.engine()
        await eng.tick(71900)
        await _event(env, 10, title="Something minor", impact="MEDIUM")
        await signal(env, "SCALP-SHORT-1")
        await eng.tick(72000)
        assert (await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"}))["status"] == "OPEN"
    run(go())


def test_open_trade_is_closed_before_big_news(env):
    _on(env)

    async def go():
        eng = env.engine()
        await eng.tick(71900)
        await signal(env, "SCALP-SHORT-1")
        await eng.tick(72000)
        assert (await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"}))["status"] == "OPEN"
        await _event(env, 40, title="FOMC Meeting")                  # 40 minutes away: trade stays open
        env.clock.add(seconds=5)
        await eng.tick(72010)
        assert (await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"}))["status"] == "OPEN"
        env.clock.add(minutes=26)                                    # 14 minutes before: inside the 15-minute close window
        env.at.forget_news()
        await eng.tick(72050)
        t = await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"})
        assert t["status"] == "CLOSED" and t["exit_reason"] == "NEWS" and t["exit_price"] == 72050
        ev = await env.db.autotrade_events.find_one({"kind": "news"})
        assert "closing before FOMC Meeting" in ev["text"]
    run(go())


def test_slippage_is_recorded_and_a_big_slip_pauses(env):
    _on(env)

    async def go():
        ex = SlipExchange(entry_slip=20)
        eng = env.engine(ex)
        await eng.tick(71900)
        await signal(env, "SCALP-SHORT-1")                           # bot LONG at 72000, stop 71640
        await eng.tick(72000)
        t = await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"})
        assert (t["sent_price"], t["entry"], t["entry_slip_pts"], t["slip_allowance_pts"]) == (72000, 72020, 20, 360)
        ex.exit = {"reason": "STOP", "price": 71140.0, "role": "taker"}  # the stop filled 500 points beyond 71640
        env.clock.add(seconds=5)
        await eng.tick(71500)
        t = await env.db.autotrade_trades.find_one({"_id": "SCALP-SHORT-1"})
        assert (t["exit_expected"], t["exit_slip_pts"], t["slip_pts"]) == (71640, 500, 520)
        assert abs(t["slip_usd"] - 520 * t["contracts"] * 0.001) < 1e-9
        st = await env.at.get_state()
        assert st["paused"] and "slipped 520 points" in st["pause_reason"]
        assert any(a[1] == "Paused – slippage" for a in env.alerts)
        s = await env.at.slip_stats(st, env.at.DEFAULTS)
        assert s["trades"] == 1 and s["avg_stop_pts"] == 500 and s["worst"]["pts"] == 520
    run(go())


def test_average_slippage_brake_and_resume_resets_it(env):
    _on(env)

    async def go():
        ex = SlipExchange(entry_slip=40)
        eng = env.engine(ex)
        await eng.tick(71900)
        for i in range(4):
            await env.db.autotrade_trades.insert_one({"_id": f"OLD-{i}", "signal_id": f"OLD-{i}", "mode": "PAPER", "status": "CLOSED", "net_usd": 1.0,
                                                      "day": "2026-11-12", "exit_time": env.core.iso(env.clock() - timedelta(hours=5 - i)), "slip_pts": 40.0})
        await signal(env, "SCALP-SHORT-1")
        await eng.tick(72000)
        ex.exit = {"reason": "TARGET", "price": 72720.0, "role": "maker"}
        env.clock.add(seconds=5)
        await eng.tick(72720)
        st = await env.at.get_state()
        assert st["paused"] and "average slippage of the last 5 trades is 40.0 points" in st["pause_reason"]
        await eng.resume("admin@example.test")
        st = await env.at.get_state()
        assert not st["paused"] and st["pause_reason"] is None and st["slip_reset_at"]
        s = await env.at.slip_stats(st, env.at.DEFAULTS)
        assert s["trades"] == 5 and s["recent_n"] == 0              # the brake counts again from the resume
    run(go())


def test_status_and_csv_carry_news_and_slippage(env):
    _on(env)

    async def go():
        eng = env.engine()
        await eng.tick(71900)
        await env.db.manual_events.insert_one({"title": "Employment Situation", "impact": "HIGH",   # status() reads the real clock
                                               "scheduled_utc": env.core.iso(env.core.now_utc() + timedelta(minutes=90))})
        env.at.forget_news()
        out = await env.at.status()
        assert out["news"]["next"]["title"] == "Employment Situation" and out["news"]["block"] is None
        assert out["slippage"]["trades"] == 0 and out["settings"]["stop_slip_pct"] == 0.5
        head = (await env.at.journal_csv()).splitlines()[0]
        assert head.endswith("sent_price,entry_slip_pts,exit_expected,exit_slip_pts,slip_pts,slip_usd")
    run(go())
