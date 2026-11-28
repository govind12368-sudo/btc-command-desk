# Option Picks: can a skip rule avoid the losing days? (28 Nov 2026)

Owner's request: a strict rule that skips losing days, proven on real data, not a random rule. Result: **no rule passed,
so Option Picks is unchanged.**

## The plan (written before any result was looked at)
- Trade: pick 5 short ATM straddle, 17:01 → 17:30 IST, 2x stop per leg, put mark < $20 skipped; fixed $2,000 + $2,000 at 25x.
  856 trades, 1 Jan 2024 → 22 Nov 2026, from the history file (Delta's real option prices), net +$14,949.
- Two reasons a day loses, two hypotheses (only these were tested):
  - **H1 · options too cheap for the market's current speed** (variance risk premium, volatility clustering):
    R = straddle premium ÷ (last 2 hours' 5-minute volatility scaled to 29 minutes × BTC price). Skip low R.
  - **H2 · a move already running into the entry** (liquidation cascades): M = last 30 minutes' move ÷ premium. Skip high M.
- Threshold set on 2024 only (skip 10%, 20% or 30% of days, best one frozen), then judged on unseen 2025 → 22 Nov 2026.
- Pass bars, all required: higher test net P&L; a gain in 2025 and in 2026; beats 97.5% of 2,000 random skips;
  still a gain with 5% worse fills + 2x fees; skips at most 30% of days.
- BTC candles: Binance BTCUSDT 5-minute, only candles closed before 17:00 IST.

## Result on unseen data (2025 → 22 Nov 2026: 538 trades, 68% won, net +$11,224)
| | H1 premium too cheap | H2 move already running |
|---|---|---|
| Days skipped | 207 (38%) | 66 (12%) |
| Losing days avoided | 83 of 170 (−$2,472 avoided) | 24 of 170 (−$812 avoided) |
| Winning days given up | 124 of 368 (+$4,177 lost) | 42 of 368 (+$1,448 lost) |
| Net change | **−$1,704** | **−$636** |
| 2025 / 2026 | −$1,242 / −$462 | −$873 / +$237 |
| Win rate of kept days | 74% (68% before) | 69% |
| With 5% worse fills + 2x fees | +$1,266 | +$418 |
| Better than random skips | yes (p < 0.001) | yes (p = 0.024) |
| **Pass** | **no** (loses money, skips > 30%) | **no** (loses money, 2025 negative) |

Both rules pick worse-than-average days, and the win rate goes up, but the skipped days still made money in total on
Delta's prices, so skipping lowers profit. Only if real fills are about 5% worse than the mark would H1 add money: that is
for the small real-account test to show, with its own pre-written test, not a reason to add it now.
Scripts and data: research/oprules (PLAN.md, build.py, evaluate.py, result.json) in the working folder.
