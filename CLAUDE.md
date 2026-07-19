# prediction-mm — Worklog & Operating Rules

> **Standing rule:** after every bug fix, ship, or significant decision, add a
> dated entry to the Incident Log below. Keep this file ≤ ~1 page.

## Thesis (PIVOTED 2026-07-19)
**Active edge search: Polymarket global CLOB liquidity rewards**
(`clob.polymarket.com` / quadratic score inside `max_spread`). Deep-dive scan
shows large gross pools vs thin qualifying depth on many markets — unproven net
after adverse selection, but the only live opportunity with measurable scale.

**Polymarket US (`api.polymarket.us`) is parked.** Days of US scanning/testing
produced no proven edge. Keep US shadow worker + safety fixes; do not treat US
as the income thesis.

**Validate-first:** daily CLOB yield snapshots → stability filter → docs-reconciled
scoring → micro-size on competed/catalyst-free markets only. Gross ≠ net.

## Invariants
- **No live CLOB orders until** wallet + L1/L2 creds + kill switch exist and a
  micro-pilot is explicitly approved. Current CLOB code is **read-only scan**.
- **US shadow gate still holds** for `poly_runner.py` (`BOT_MODE=shadow` default).
- **CLOB score:** `S=((v-s)/v)^2·size` with Q_min two-sided rule; capture =
  `daily_rate · my/(my+book)`. `max_spread` from API is **cents**.
- Prefer **competed** books; treat near-zero competition as advanced / high AS.

## Architecture
| File | Role |
|------|------|
| `scripts/clob_yield_scan.py` | **Active:** global CLOB reward-yield scan + daily CSV |
| `core/clobclient.py` | Read-only CLOB HTTP (sampling-markets, book) |
| `core/clobscore.py` | Quadratic LP score + capture estimate |
| `poly_runner.py` | Parked US worker (shadow); safety/selection hardened |
| `core/polyclient.py` / `polymaker.py` / `rewardscore.py` | US stack (parked) |
| `scripts/poly_scan.py` | US scan (parked; not the edge thesis) |
| `scripts/poly_cancel_all.py` | One-shot LIVE cancel leftover US orders |

## Deploy
Render `polymarket-mm` still runs US `poly_runner.py` — keep **shadow**. CLOB
scanner is local/cron until a CLOB worker is built.

## Closed theses
Kalshi: MM bleeds, no arb, convergence efficient. **Polymarket US LP:** no proven
edge after extended scan/test — do not re-litigate without new venue data.

## Incident Log

### 2026-07-19 — Pivot to global CLOB reward-yield search
US path had no proven edge. Egress is unrestricted (CLOB reachable). Built
read-only `clob_yield_scan` (sampling-markets + quadratic score + capture CSV /
daily snapshots). US worker stays shadow; CLOB quoting infra not built yet.

### 2026-07-19 — US methodology gaps (pre-pivot)
`netPosition` breaker fix, deny-list, US multi-level score, earnings ledger —
useful safety, not an edge. See prior PR work.

### 2026-06-20 — Migration from kalshi-mm
US baseline + Render cutover; live COD esports halt. Details in git history /
`FOLLOWONS.md`.
