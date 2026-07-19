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
pool cadence/scope and live adverse selection. **Validate-first: stay in shadow
until a live reward-earnings read confirms positive net economics.** Global CLOB
(`clob.polymarket.com` quadratic / `sampling-markets`) is a different venue —
not this worker.

## Invariants (must never break)
- **`BOT_MODE=shadow` = no orders reach the exchange.** `PolyClient(live=False)`
  records intended orders to `shadow_orders` and returns a synthetic ack; a test
  asserts no network leak. Only the operator flips `BOT_MODE=live`.
- **Quote only reward-eligible markets in an active window** — selection from
  `/v1/incentives`, gated by `program_active`, deny-list, mid band, min hours to
  settle, and (default) competed books; ranked by US-score est capture.
- **Full reconcile every cycle:** `cancel_all_orders` (with each order's
  `marketSlug` in the body — empty body 400s) BEFORE re-posting.
- **Post-only at the EXACT book price** (futures tick in 0.001).
- **Budget bounded:** top `POLY_MAX_MARKETS` by est capture, size = `BUDGET/N`;
  breaker trips on inventory / exposure / unrealized loss, then cancels + stands aside.
  Position parse must include `netPosition`.

## Architecture
| File | Role |
|------|------|
| `poly_runner.py` | Worker: select + reconcile + breaker + earnings poll + ledger |
| `core/polyclient.py` | US REST + ED25519; shadow-gated orders |
| `core/polymaker.py` | Pure quoting + reward windows |
| `core/rewardscore.py` | US multi-level score + capture estimate |
| `core/ledger.py` | Quotes / fills / rewards CSV+JSONL under `data/logs/` |
| `scripts/poly_scan.py` | Yield scan + CSV + daily stability snapshots |
| `scripts/poly_cancel_all.py` | One-shot LIVE cancel of leftover orders |
| `lib/fairvalue.py` | Dormant Bachelier salvage |

## Deploy
Render worker `polymarket-mm` (`python poly_runner.py`). Repo repoint to
`prediction_mm` still blocked (GitHub App access).

## Closed theses (Kalshi — do not re-litigate)
MM bleeds; no guaranteed arb; convergence efficient; momentum needs vol.
Lesson: **validate read-only before funding; reconcile tape P&L vs balance.**

## Incident Log

### 2026-07-19 — Reward-capture gap upgrade (US-first)
Second-opinion deep-dive targeted global CLOB quadratic scoring; we stayed on
**Polymarket US** and closed methodology gaps: multi-level US score + capture
rank (`core/rewardscore.py`, `poly_scan` CSV/history), pilot filters (deny
`aec-cod-*`, mid 10–90¢, ≥72h to end, require competed), `netPosition` breaker
fix, `scripts/poly_cancel_all.py`, earnings poll + separate reward/trading
ledger. Stay shadow until earnings confirm.

### 2026-06-20 — Migration + cutover
Polymarket baseline from kalshi-mm; Render renamed `polymarket-mm` (still builds
from kalshi-mm until GitHub App access); live COD esports quotes halted to
shadow — see `FOLLOWONS.md`. Supabase cleanup N/A; archive kalshi-mm blocked.
