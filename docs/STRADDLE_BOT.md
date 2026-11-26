# Straddle Bot

Short ATM BTC straddle on Delta Exchange India daily options. Owner's spec of 26 Nov 2026.
Page: **Strategies → Straddle Bot** (admin only). It starts in **Paper** mode; no real order is sent until an admin types LIVE.

## 1. What it does each day (IST)

| Time | Step |
|---|---|
| 16:55 | Pre-flight: server clock, today's option list, the 5 strikes nearest spot, live prices, balance, margin estimate |
| 17:00:00 | Sell the ATM call and ATM put, same lots each, as limit orders at mark. Re-price toward the bid every 3 s in 0.5% steps, never below mark × (1 − max entry slip). Cancel at the deadline (30 s) |
| after fill | Backup stop-market BUY on the exchange for each leg at 3 × P |
| 17:01–17:29 | Stop when call + put ≥ 2 × P. "parity" checks the mark at each 1-minute close (as the backtest does); "tick" checks every price update |
| 17:30 | Expiry. Settlement price read from the contract's record (`product_specs.settlement_index_price`) |

One trade a day, no re-entry. A single filled leg is bought back at once and the day is skipped. Uneven fills are evened out.

## 2. Architecture

| File | Job |
|---|---|
| `backend/straddle_core.py` | The maths only: lots, fees, stop, P&L, journal, Summary metrics, monthly table, workbook import. No network, no database |
| `backend/straddle_exec.py` | Market data (websocket + REST fallback), the paper broker (fills at the real bid/ask and depth), the Delta order adapter, clock check |
| `backend/straddle.py` | The bot: schedule, entry, stop, exit, expiry, risk halts, kill switch, settings with change log, page data |
| `backend/data/straddle_backtest.json.gz` | Day-by-day prices from the owner's workbook (878 days). Replaced by an import on the Settings tab |
| `backend/tests/test_straddle.py` | Lot sizing, fee formula, stop trigger, P&L, leg risk, guards, order bodies, and the replay test |
| `frontend/src/pages/StraddleBot.jsx` | The page: Live, Backtest vs live, Monthly, Journals, Settings |

Database: the site's MongoDB (`straddle_trades`, `straddle_settings`, `straddle_changes`, `straddle_state`, `straddle_events`, `straddle_backtest`).
The spec named PostgreSQL; the owner chose to build inside this site, which uses MongoDB.

The backtest, the paper bot and the live bot all call the same functions in `straddle_core.py`.
The replay test proves it: the workbook's $4,361.89 (mark, fixed) and $55,572.37 (mark, compounded) are reproduced to the cent.

## 3. Where the build differs from the spec, and why

| Spec | Built | Reason |
|---|---|---|
| Both legs in one batch request | Two requests sent at the same instant | Delta's `/v2/orders/batch` accepts one contract per batch and refuses IOC orders |
| Exchange margin / order-preview endpoint | Margin is estimated (notional ÷ the contract's leverage); if Delta refuses the order for margin, the size is halved and re-sent, and "SIZE REDUCED" is flagged | Delta's API has no preview endpoint |
| NTP with chrony | The bot asks a public time server at 16:55 and refuses to trade (Testnet/Live) when the clock is more than 100 ms off or cannot be checked | chrony is a server package; install it on the host (section 5) |
| Kill switch flattens "all positions" | Cancels and flattens this strategy's two option contracts only | Auto-Trade and Elite 4 Edge use the same Delta account |
| "Compounded" uses account equity | Uses starting capital + this strategy's own net P&L | Same reason: other strategies share the wallet |
| Stop-exit prices from the 1-minute close | Paper and live buy at the real ask | The spec asks for real bid/ask fills; expect worse numbers than the mark-price backtest |

## 4. Hard guards

- Kill switch (page button and `POST /api/straddle/control {"action":"kill"}`).
- Daily max loss: the open trade is flattened when its loss passes 15% of equity.
- Halt, manual re-enable: drawdown from peak above 25%, losing streak above 6, entry slippage above the limit 3 trading days in a row.
- Max lots per leg (500), liquidity cap (bid depth inside the slippage limit), margin cap.
- Skip the day: ATM pair not listed or not tradable, spread above 10% of mark, prices older than 2 s, balance unreadable, clock off, bot off, halted.
- Live needs: 30 paper trading days, the Live API key, the word LIVE typed. Live uses leverage 5 at most until "Live: start small" is set to 0.

## 5. Deployment

The bot ships with the site: push to `main`, CI runs the tests, the server updates itself.

Before Testnet or Live, on the server and on Delta:

1. **Clock.** `sudo apt install chrony && sudo systemctl enable --now chrony`, then `chronyc tracking` should show an offset under a few ms.
2. **API key.** Create it on Delta India with **Trading** permission only, **no withdrawal**, and whitelist the server's IP. Paste it on the Auto-Trade page ("Delta Exchange connection") or set `DELTA_TRADE_API_KEY` / `DELTA_TRADE_API_SECRET` (Live) and `DELTA_TESTNET_API_KEY` / `DELTA_TESTNET_API_SECRET` (Testnet) in the server's `.env`. Keys never reach the browser or git.
3. **Latency.** Delta's servers are in AWS Tokyo. The page shows the round-trip time of the bot's orders; a server in or near Tokyo is fastest.
4. **Rollout.** Paper for 30 trading days → Testnet → Live at leverage ≤ 5 → full settings.
5. **Alerts.** Email, phone push and Telegram use the channels already set on the site. Paper alerts follow the site's alert switch; Testnet and Live always alert.
6. **Backups.** The site's existing database backup covers the straddle collections.

A separate watchdog task alerts when the bot's heartbeat is missing in the minutes before 17:00 IST.

## 6. Tests

```
cd backend && MONGO_URL=mongodb://localhost:27017 DB_NAME=test_db python -m pytest -q -p no:anyio tests/test_straddle.py
```
