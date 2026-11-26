# Straddle Bot

Short ATM BTC straddle on Delta Exchange India daily options. Owner's spec of 26 Nov 2026.
Page: **Strategies → Straddle Bot** (admin only). It starts in **Paper** mode; no real order is sent until an admin types LIVE.

## 1. What it does each day (IST)

| Time | Step |
|---|---|
| 16:55 | Pre-flight: server clock, today's option list, the 9 strikes nearest spot, live prices, balance |
| 16:59:50 | Arming: order leverage set on Delta, connection warmed, balance read, check that nothing else is on today's contracts |
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
| Stale data rule "older than 2 s" | Kept, with a REST refresh first: Delta sends option marks about every 2 s | A strict 2 s rule on a 2 s feed would skip days at random |
| Kill switch flattens "all positions" | Cancels and flattens this strategy's two option contracts only | Auto-Trade and Elite 4 Edge use the same Delta account |
| "Compounded" uses account equity | Uses starting capital + this strategy's own net P&L | Same reason: other strategies share the wallet |
| Stop-exit prices from the 1-minute close | Paper and live buy at the real ask | The spec asks for real bid/ask fills; expect worse numbers than the mark-price backtest |

## 4. Hard guards

- Kill switch (page button and `POST /api/straddle/control {"action":"kill"}`).
- Daily max loss: the open trade is flattened when its loss passes 15% of equity.
- Halt, manual re-enable: drawdown from peak above 25%, losing streak above 6, entry slippage above the limit 3 trading days in a row.
- Max lots per leg (500), liquidity cap (bid depth inside the slippage limit), margin cap.
- Skip the day: ATM pair not listed or not tradable, spread above 10% of mark, prices older than 2 s, balance unreadable, clock off, bot off, halted.
- Live needs: 30 paper trading days, the Live API key, a passed Live connection test, a passed Testnet round trip, the word LIVE typed.
  Live uses leverage 5 at most until "Live: start small" is set to 0.

## 4a. Built for a real account (26 Nov 2026)

| Risk | What the bot does |
|---|---|
| The website is busy at 17:00 | The bot runs in its own thread with its own event loop and database connection. The page shows the worst loop delay of the day |
| Slow orders | One kept HTTPS connection to Delta, warmed 10 s before the entry (a new connection costs about 0.5 s, a kept one about 0.05 s) |
| An exit that does not fill | Keeps buying back until Delta shows no position. A trade is never marked closed while a leg is open; the backup stops stay until it is flat. If the expiry comes first, the rest is settled |
| Server restart mid-trade | Reads Delta first. Open and intact: restores missing backup stops and carries on. During the entry: adopts the hedged part, buys back the rest. After the expiry: settles |
| Settlement price not published | Tried every 30 s without blocking the next day; after 2 h the index's own 30-minute average is used and flagged |
| Stale or missing prices | Decisions use prices under 10 s old only; an alert goes out after 15 s without prices; the backup stops on Delta still protect |
| Another position on the same contracts (Delta's own automation, a manual trade) | Before the entry: the day is skipped. During the trade: reported, and the bot only ever closes its own lots |
| Liquidation before the backup stop | The order leverage is lowered automatically when premiums are high, so a leg's margin covers its loss at the backup stop |
| Deploy or auto-restart during the trade | The server does not update between 16:45 and 17:40 IST, and the health check does not restart the container while a trade is in hand |
| Rate limit, lost answers | A refused-for-rate-limit request is re-sent; an order whose answer is lost is looked up by its id and never sent twice |
| Wrong P&L | After each real trade the Delta wallet change is recorded next to the computed result, with a warning when they differ |

## 4b. Rehearsals (Live tab → Rehearsal)

- **Connection test** (Testnet or Live): key, clock, wallet, today's contracts, prices, positions, order leverage, and one resting order near the upper price band that is cancelled at once. It cannot fill.
- **Testnet round trip**: the bot's own pre-flight, arming, entry (1 lot each leg), backup stops, margin read, exit and clean-up on Delta's practice exchange.

Both run on the bot's thread, never between 16:45 and 17:40 IST. Each step shows what it found and how long it took.

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
cd backend && MONGO_URL=mongodb://localhost:27017 DB_NAME=test_db python -m pytest -q -p no:anyio tests/test_straddle.py tests/test_straddle_hardening.py
```
