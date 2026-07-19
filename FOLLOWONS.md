# Follow-ons

## #0 — FIXED 2026-07-19: `netPosition` breaker blindness
Live API field is `netPosition` (raw shape: `{'netPosition':'332',...}`).
`positions_net` now parses `netPosition` first; unit test locks the logged shape.
**Still operator-owned:** cancel any leftover live COD orders via
`PYTHONPATH=. python scripts/poly_cancel_all.py` (or UI) — shadow cannot cancel
exchange orders. Open 332-contract WC position may still exist; breaker will now
see it once positions are readable.

## #1 — Esports scope (mitigated; confirm before live)
`/v1/incentives` listed COD (`aec-cod-*`) and the bot correctly quoted them.
**Mitigation shipped:** `POLY_DENY_PREFIXES=aec-cod-` (default) drops those
prefixes in `RewardMarketCache.refresh()`. Before any live flip: confirm current
`get_incentives()` set and extend the deny-list if other esports programs appear.

## #2 — Telemetry + app
Local append-only ledger now writes `data/logs/{quotes,fills,rewards}.csv` +
`events.jsonl`. Supabase / Expo app retarget still open.

## #3 — Resolve LP-reward economics (in progress)
- Scan: multi-level US score + `est_reward = pool × my/(my+book)`; daily snapshots
  under `data/reward_scans/`; `--history` stability report.
- Runner polls `/v1/incentives/earnings` every `POLY_EARNINGS_SECS` into
  `rewards.csv` (separate from trading fills).
- Stay shadow until earnings show positive gross; micro-size live only on
  competed, long-dated markets (`POLY_REQUIRE_COMPETED=1`, `POLY_MIN_HOURS_TO_END=72`).
- Near-zero competition markets are an advanced tier — excluded by default.
- Global CLOB (`sampling-markets` / quadratic) remains a separate venue decision;
  not wired here.
