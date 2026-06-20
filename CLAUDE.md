# prediction-mm — Worklog & Operating Rules

Polymarket US liquidity-reward market maker. Fresh repo (2026-06-20), migrated
from `kalshi-mm` (Kalshi retired — every Kalshi income thesis closed; see
"Closed theses" below). Squashed baseline; full Kalshi history lives in the old
repo.

> **Standing rule:** after every bug fix, ship, or significant decision, add a
> dated entry to the Incident Log below. Keep this file ≤ ~1 page.

## Thesis
Polymarket US (CFTC/QCEX-regulated, `api.polymarket.us`) **pays market makers** —
a maker rebate (`0.0125·C·p·(1−p)`) plus liquidity-reward pools
(`Score = discountFactor^(ticks_from_best)·size`, snapshotted ~1/s, split
pro-rata). That LP reward is the income mechanism Kalshi never had. **Unresolved:**
pool cadence/scope and live adverse selection — rewards have been World-Cup-only
and the economics aren't proven. **Validate-first: stay in shadow until a live
reward-earnings read confirms positive net economics.**

## Invariants (must never break)
- **`BOT_MODE=shadow` = no orders reach the exchange.** `PolyClient(live=False)`
  records intended orders to `shadow_orders` and returns a synthetic ack; a test
  asserts no network leak. Only the operator flips `BOT_MODE=live`.
- **Quote only reward-eligible markets in an active window** — selection comes
  from `/v1/incentives` (`RewardMarketCache`), gated by `polymaker.program_active`.
- **Full reconcile every cycle:** `cancel_all_orders` (with each order's
  `marketSlug` in the body — empty body 400s) BEFORE re-posting, or orders pile up.
- **Post-only at the EXACT book price** (futures tick in 0.001, not 0.01 — rounding
  crosses a 1-tick book and gets post-only-rejected).
- **Budget bounded:** top `POLY_MAX_MARKETS` by pool, size = `BUDGET/N`; breaker
  trips on per-market inventory > cap, total exposure > `EXPOSURE_CAP` (1.5×budget),
  or unrealized loss < −`POLY_DAILY_LOSS`, then cancels all + stands aside.

## Architecture
| File | Role |
|------|------|
| `poly_runner.py` | Worker: select reward markets in-window, full-reconcile quote loop, breaker. `BOT_MODE` shadow/live/off. |
| `core/polyclient.py` | Polymarket US REST + ED25519 auth (`msg = f"{ts_ms}GET{path}"`, seed = `base64(SECRET)[:32]`). Shadow-gated order layer (`place_order`/`cancel_order(id, market_slug)`/`get_open_orders`/`get_positions`/`get_book`/`get_incentives`). |
| `core/polymaker.py` | Pure quoting: `maker_quotes` (join touch, inventory skew/cap), `program_active` (period-driven reward window). |
| `lib/fairvalue.py` | **Dormant** salvage — spot-anchored fair value (Bachelier). Not used by the reward maker. |
| `scripts/poly_scan.py` | Read-only reward-market book scan + pro-rata share estimate. |
| `tests/` | `test_polyclient_shadow` (no-leak gate), `test_polymaker`, `test_poly_breaker`, `test_fairvalue`. |

Runtime is stdlib-only except `cryptography` (ED25519). Keys in repo-root `.env`
(`POLYMARKET_API_KEY` + `POLYMARKET_SECRET`); never commit them.

## Deploy
Render background worker `polymarket-mm` (build `pip install -r requirements.txt`,
start `python poly_runner.py`). Start command + env are dashboard-only (Render MCP
can read logs/env but can't create workers or edit the start command).

## Closed theses (Kalshi — do not re-litigate)
MM bleeds (adverse-selected in every config); no executable guaranteed arb
(7.5k events swept, 0 robust); convergence efficient (weather settles on official
climate report not raw obs; sports converge to $1 instantly); momentum needs
volatility to even fire (0 trades). Lesson that paid off repeatedly: **validate
read-only before funding; reconcile any tape-derived P&L against account balance.**

## Incident Log

### 2026-06-20 — Repo migrated from kalshi-mm (Phase 1)
Assembled the Polymarket keeper tree as a clean baseline: `poly_runner.py`,
`core/polyclient.py`, `core/polymaker.py`, `scripts/poly_scan.py`, the poly tests,
and `lib/fairvalue.py` (+ its tests, salvaged from branch
`claude/kalshi-mm-multi-bot-x5xrd5`, import re-pointed `core`→`lib`). requirements/
.env.example/render.yaml re-cut Polymarket-only; CLAUDE.md distilled. **Dropped the
two convergence scanners** (`poly_wx_scan`, `poly_sports_scan`) per Andrew — they
were closed-thesis tools depending on dropped modules (weatherfeed/gamefeed/
convergence→alpha); only `poly_scan.py` (live reward thesis) came along. See
`FOLLOWONS.md` for the esports-orders investigation (do before any live quoting).
