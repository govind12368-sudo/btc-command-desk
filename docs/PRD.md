# Vantage BTC (formerly BTC Command Desk) — PRD

> Historical product notes from the first version. Hosting/email/cron details below describe the old platform; see README.md for the current self-hosted setup.

## Original problem statement (summary)
Build "BTC Command Desk": responsive website + installable PWA that replaces an Excel BTC trading workbook (25 primary + 25 backup sheets). Real data only (Delta Exchange India BTCUSD execution market via WebSocket + REST, OKX BTC-USDT-SWAP derivatives context, Fed H.10 broad dollar, Fed H.15 2Y, EIA Brent, Alternative.me F&G, Farside ETF flows, all-exchange funding provider, BLS + manual economic events, publisher RSS). 12 pages, exact F26–F37 checks for Scalping (5m/15m) and Swing (1h/4h), conservative rounding and cost model, saved Swing plan lifecycle, five pillars + live direction, original four/six-factor models, readiness, buy/sell observation, trade planner, automatic model-signal journal separate from actual fills, forward validation on 30-day 5m history, news radar, publication-aware macro freshness, system audit + automatic recovery, upgrade requests. Login with admin and regular user (view only). Prepared by Govind Bhendegawe.

## User choices
- All-exchange funding: CoinGlass with user's key (key saved but rejected by CoinGlass: "Invalid API key").
- Calendar: official BLS (US-only, incomplete) + manual researched events.
- Workbook: user said attached, but no file was received → coverage report is specification-based.
- Roles: admin edits everything, user views only.
- Extra ask: add supporting evidence for direction → added rule backtest (30d), multi-timeframe alignment, OKX long/short ratio, OI history, direction-change outcome stats.

## Architecture
FastAPI + MongoDB backend with background loops (scheduler 1 s, calc 2 s, slow loop 15 s / 5 min, recovery 10 s). Modules: sources, indicators, strategy, models_calc, journal, validation, engine, recovery, planner, server. React frontend (12 pages + login), PWA manifest + shell-only service worker. See /app/README.md for source registry and storage schema.

## Implemented (2026-10-28)
- All sources live except Farside (HTTP 403) and CoinGlass (key rejected) — shown as ACCESS NEEDED/UNAVAILABLE.
- 12 checks per strategy, statuses WAIT / PLAN ONLY / READY TO CAPTURE / BUY / SELL / MONITOR; auto-capture with dedup IDs.
- Swing plan capture/pause/expiry(4h)/invalidation/consume/cancel + archive.
- Five pillars, live direction with renormalized weights and partial coverage, four/six-factor kept unavailable when inputs missing.
- Journal (model vs actual), P&L, equity/drawdown/win rate/expectancy/profit factor, CSV import/export.
- Validation: 8,640 5m bars, 0 gaps, first-touch freezing, review/gap outcomes, manual snapshots, edits invalidate results; rule backtest evidence.
- News radar (6 RSS, dedup, word-boundary classification), daily review F31 with invalidation by new headlines, manual + BLS event blocking.
- Settings with optimistic concurrency + local draft retention; System Audit; recovery with cooldown/backoff/history; Upgrade Requests.
- JWT cookie auth, admin/viewer, brute-force lockout, same-origin write guard.
- Tested: iteration_1 (backend 40/41 then fixed; frontend flows pass); fixes for origin guard, lockout, invalid IDs, BLS parsing, mobile overflow.

## Iteration 4 (2026-10-28) — accounts & branding
- Public registration (role user, view-only), login by username or email, Remember me, show/hide password, light/dark login, email verification, forgot/reset (single-use 30-min hashed tokens), change password (revokes other sessions), admin User Management (deactivate/reactivate), rate limits + lockout.
- Educational disclaimer footer on every page + /disclaimer page; "100% Free – For Educational Purposes Only" badge; header credit "Designed & Developed by Govind Bhendegawe"; © 2026 footer credit; user menu.
- Alerts: SMS via Twilio REST + Telegram bot wired; daily 8 PM IST recap cron (.emergent/crons.yml → /api/cron/daily-summary); Delta read-only fill import (round-trip builder, funding from wallet transactions).
- Provider blockers: Twilio trial (error 572006 — upgrade account or verify templates), Telegram 404 (bot token invalid), Delta 401 ip_not_whitelisted (whitelist 35.225.230.28).
- Tested iteration_3: 100% pass; minor fixes applied (lockout window reset, rate-limit after validation, XFF last hop, column header).

## Iteration 2 (2026-10-28)
- Email signal alerts (managed email → owner's ALERT_EMAIL) on each new 12/12 capture; test-email button; SMS not configured (no provider).
- ETF factor now from Bitbo ETF flow table; all-exchange funding replaced by 5-venue OI-weighted proxy (user declined paid CoinGlass) → original four-factor and six-factor models now complete.
- Workbook audited sheet by sheet; coverage report with findings; read-only reference archive with backup-unique rows; 99 Five Pillar fields preserved.
- Tested iteration_2: all features pass; minor fixes applied (page validation, sorting, Excel serial dates, archive errors).

## Backlog
- P0: SMS/WhatsApp alerts (needs Twilio or similar account).
- P1: read-only Delta API fill import; Binance/Bybit funding via an allowed region/provider.
- P2: split server.py routers; shadcn date-time picker; telegram alerts.
