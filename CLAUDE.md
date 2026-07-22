# prediction-mm — Worklog & Operating Rules

> After each ship: dated Incident Log entry. Keep ≤ ~1 page.

## Thesis
**Global CLOB liquidity rewards** (`clob.polymarket.com`). Quadratic score inside
`max_spread`; capture = `daily_rate · my/(my+book)`. Polymarket US: no proven edge — parked.

## Deep-dive sequence (source of truth)
1. Regular CLOB pulse → `data` branch + Supabase (never deploy-branch commits)
2. Docs-reconciled scoring (`core/clobscore.py`: S, Q_min, min_size mid)
3. Eligibility + dedicated pilot wallet + L2 keys — ops
4. Quoting bot: two-sided, WS mids, shadow fills, Supabase ledger, remote kill
5. Micro-pilot: $50–100 × ≤3 competed, ≥7d to end — not near-zero list
6. Scale gate: net > 50% of est gross over ≥14d (Supabase `clob_daily_pnl`)

## Invariants
- Live requires `CLOB_MODE=live` **and** `ELIGIBILITY_CONFIRMED=true` (else shadow)
- Live egress must pass `GET https://polymarket.com/api/geoblock` → `blocked:false`
  (Render oregon is US API close-only — see `docs/CLOB_LIVE_RUNBOOK.md`)
- `CLOB_MODE=shadow` → no CLOB mutations (`ClobTrader` gate + tests)
- Pilot from `pilot_universe.csv`; provisional (<5d) logged as WARNING
- Post-only; full cancel/replace; kill via `CLOB_KILL` or Supabase `clob_control`
- Ledger SoT: Supabase (`sql/0002_clob_ledger.sql`); CSV dump optional only
- Pulse must not restart the worker (no deploy-branch artifact commits)

## Architecture
| Path | Role |
|------|------|
| `clob_runner.py` | Quoter (WS mids, shadow fills, kill poll) |
| `docs/CLOB_LIVE_RUNBOOK.md` | Live egress + flip/abort checklist |
| `core/eligibility.py` | Hard live gate |
| `core/clob_ledger.py` / `supabase_clob.py` | Persistent ledger + kill |
| `core/clob_shadowfills.py` / `clob_bookws.py` | Shadow tape fills / WS |
| `scripts/clob_pulse.py` / `clob_reward_recon.py` | Pulse + actual vs est |
| `scripts/clob_scale_gate.py` | Scale-up gate (Supabase) |
| `poly_runner.py` | Parked US worker |

## Incident Log

### 2026-07-22 — Live blocked by CLOB geoblock (Render oregon)
Flipped live after eligibility confirm; first `/order` → 403 geoblock (US API
close-only). Reverted shadow. Runbook: `docs/CLOB_LIVE_RUNBOOK.md` (live host
needs API-allowed egress, e.g. IE/`eu-west-1`; Render stays shadow).

### 2026-07-22 — Shadow multi-day sampling fix
Inventory-at-cap froze sim fills; flatten + UTC day rollover (#115).

### 2026-07-19 — Handback P0–P2 post-review fixes
Eligibility hard gate; Supabase ledger/kill; pulse→`data` branch; shadow-fill
sim; WS mids; reward recon; footgun tests; provisional pilot; wallet docs.

### 2026-07-19 — Implement deep-dive CLOB plan
Built stability filter, docs-reconciled score, L2 derive, shadow-gated runner,
micro-pilot defaults, scale gate, ledgers, Render stubs. US parked.
