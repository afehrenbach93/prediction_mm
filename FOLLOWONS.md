# Follow-ons

## Active thesis — Global CLOB LP rewards (pivoted 2026-07-19)
Polymarket US produced **no proven edge**. Pivot to global CLOB incentive capture.

1. **Stability study:** run `PYTHONPATH=. python3 scripts/clob_yield_scan.py` daily;
   keep markets whose competed yield persists (`--history`).
2. **Docs fidelity:** scoring in `core/clobscore.py` matches published quadratic +
   Q_min; re-check if Polymarket changes `c` or sampling cadence.
3. **Eligibility / wallet:** confirm US/FL access to global Polymarket; Polygon
   wallet + USDC; CLOB L1/L2 API keys. (Ops — not coded.)
4. **Quoting bot (not built):** two-sided quotes inside `max_spread`, refresh on
   mid move, inventory caps, hard kill switch, fill logging.
5. **Micro-pilot:** $50–100 on 2–3 *competed*, long-dated, catalyst-light markets.
   Scale only if realized net > ~50% of estimated gross.
6. **Avoid near-zero books** until event-aware quote-pulling exists.

## Parked — Polymarket US
- `#0` `netPosition` parse: **fixed** in code; cancel leftovers with
  `scripts/poly_cancel_all.py` if any remain.
- `#1` Esports deny-list (`aec-cod-`): shipped; keep US worker on `BOT_MODE=shadow`.
- `#2` Local ledger exists; Supabase/app still open if US ever resumes.
- `#3` US LP economics: **closed as unproven** — do not fund without new evidence.

## Domains / egress
Cloud agent egress is **unrestricted** (`clob.polymarket.com`,
`gamma-api.polymarket.com`, `gateway.polymarket.us` all reachable). No allowlist
block on CLOB scans in this environment.
