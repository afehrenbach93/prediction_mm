# Follow-ons — deep-dive checklist

## §7.1 Stability study
```bash
PYTHONPATH=. python3 scripts/clob_yield_scan.py --budget 500 --top 250
PYTHONPATH=. python3 scripts/clob_stability.py --min-days 1 --min-yield 3
# raise --min-days to 5–7 as daily CSVs accrue
```
Render cron: `clob-yield-scan-daily` in `render.yaml`.

## §7.2 Docs reconciliation
Implemented in `core/clobscore.py` per
https://docs.polymarket.com/market-makers/liquidity-rewards
(`S=((v-s)/v)^2`, size-cutoff mid, Q_min, c=3). Re-verify if Polymarket changes `c`.

## §7.3 Eligibility + wallet (ops — not automatable here)
- [ ] Confirm US/FL access to polymarket.com ToS
- [ ] Polygon wallet + USDC
- [ ] `CLOB_PRIVATE_KEY=… python3 scripts/clob_derive_keys.py` → fill `.env`
- [ ] Keep `CLOB_MODE=shadow` until micro-pilot go

## §7.4 Quoting bot
`PYTHONPATH=. python3 clob_runner.py` — shadow default.
Kill: `touch data/clob_logs/KILL`

## §7.5 Micro-pilot
Defaults: `$75 × 3` competed, `MIN_HOURS_TO_END=168`, near-zero excluded.
Measure: rewards (`rewards.csv`) − fill losses (`fills.csv`).

## §7.6 Scale gate
```bash
PYTHONPATH=. python3 scripts/clob_scale_gate.py --min-days 14 --threshold 0.5
```
PASS required before increasing size. Near-zero tier stays advanced-only.

## Parked — Polymarket US
No proven edge. `BOT_MODE=shadow`. `netPosition` fix + deny-list remain for safety.
