# Follow-ons — deep-dive checklist

## §7.1 Stability study — regular CLOB pulse
Domain: `https://clob.polymarket.com` (sampling-markets + books).

```bash
PYTHONPATH=. python3 scripts/clob_pulse.py --budget 500 --top 250 --min-days 5
```

Scheduled:
- GitHub Actions `.github/workflows/clob-pulse.yml` — **00:00 + 15:00 UTC**;
  artifacts + push to **`data` branch only** (never deploy branch) + Supabase
- Render crons `clob-pulse` / `clob-pulse-morning` in `render.yaml`

## §7.2 Docs reconciliation
Implemented in `core/clobscore.py` per
https://docs.polymarket.com/market-makers/liquidity-rewards
(`S=((v-s)/v)^2`, size-cutoff mid, Q_min, c=3). Re-verify if Polymarket changes `c`.

## §7.3 Eligibility + wallet (ops — not automatable here)
- [x] Confirm US/FL access to polymarket.com ToS → `ELIGIBILITY_CONFIRMED=true` (set 2026-07-22)
- [x] Dedicated Polygon pilot wallet + USDC (never main Magic key)
- [x] `CLOB_FUNDER` / `CLOB_SIGNATURE_TYPE` set for proxy hygiene
- [x] L2 keys on Render; Supabase ledger applied
- [ ] **Live egress:** Render oregon is CLOB-geoblocked (US API close-only).
      See `docs/CLOB_LIVE_RUNBOOK.md` — run live quoter from API-allowed region
      (docs hint: `eu-west-1` / Ireland); keep Render on shadow.

## §7.4 Quoting bot
`PYTHONPATH=. python3 clob_runner.py` — Render stays **shadow** until egress fixed.
Kill: `CLOB_KILL=true` or `clob_control.kill=true` in Supabase.
Live runbook: `docs/CLOB_LIVE_RUNBOOK.md`.

## §7.5 Micro-pilot
Defaults: `$75 × 3` competed, `MIN_HOURS_TO_END=168`, near-zero excluded.
Measure: rewards − fill losses (Supabase `clob_rewards` / `clob_fills`; shadow
sim fills have `simulated=true`).

## §7.6 Scale gate
```bash
PYTHONPATH=. python3 scripts/clob_scale_gate.py --min-days 14 --threshold 0.5
```
PASS required before increasing size. Near-zero tier stays advanced-only.

## Parked — Polymarket US
No proven edge. `BOT_MODE=shadow`. `netPosition` fix + deny-list remain for safety.
