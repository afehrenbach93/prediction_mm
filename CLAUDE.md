# prediction-mm — Worklog & Operating Rules

> After each ship: dated Incident Log entry. Keep ≤ ~1 page.

## Thesis
**Global CLOB liquidity rewards** (`clob.polymarket.com`). Quadratic score inside
`max_spread`; capture = `daily_rate · my/(my+book)`. Polymarket US: no proven edge — parked.

## Deep-dive sequence (source of truth)
1. Regular CLOB pulse (`scripts/clob_pulse.py` → `clob.polymarket.com`) twice daily
   via `.github/workflows/clob-pulse.yml` (00:00 + 15:00 UTC); CSVs committed
2. Docs-reconciled scoring (`core/clobscore.py`: S, Q_min, min_size mid)
3. Eligibility + wallet + L2 keys (`scripts/clob_derive_keys.py`) — ops
4. Quoting bot (`clob_runner.py`): two-sided, refresh, inventory, kill, fill log
5. Micro-pilot: $50–100 × ≤3 competed, ≥7d to end — not near-zero list
6. Scale gate: net > 50% of est gross over ≥14d (`scripts/clob_scale_gate.py`)

## Invariants
- `CLOB_MODE=shadow` → no CLOB mutations (`ClobTrader` gate + test)
- Pilot from `pilot_universe.csv` only; near-zero excluded
- Post-only; full cancel/replace; kill file `data/clob_logs/KILL`
- Rewards ledger ≠ trading fills (`data/clob_logs/`)

## Architecture
| Path | Role |
|------|------|
| `scripts/clob_yield_scan.py` | Daily yield scan + CSV |
| `scripts/clob_stability.py` | Persistent-yield → pilot_universe.csv |
| `scripts/clob_derive_keys.py` | L1→L2 credential derive |
| `scripts/clob_scale_gate.py` | Scale-up gate |
| `clob_runner.py` | Quoter worker |
| `core/clobscore.py` / `clobmaker.py` / `clobtrader.py` / `clob_ledger.py` | Score, quotes, shadow gate, accounting |
| `poly_runner.py` | Parked US worker |

## Incident Log

### 2026-07-19 — Implement deep-dive CLOB plan
Built stability filter, docs-reconciled score (adjusted mid + Q_min), L2 derive
helper, shadow-gated `clob_runner`, micro-pilot defaults, scale gate, separate
rewards/fills ledgers, Render cron+worker stubs. US parked.

### 2026-07-19 — US path closed as unproven; CLOB scan first land
Egress unrestricted; live scan ~9k markets / top-250 yields. See git history.
