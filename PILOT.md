# PILOT.md — small bounded live reward-MM pilot

Goal: resolve the ONE unanswered question of the whole Polymarket thesis —
**do the liquidity rewards actually pay more than the adverse selection costs, at
our retail size?** — with the smallest amount of real money that produces a clear
signal, on a feedback loop **shorter than a week**.

Status: validate-first. The bot is built and (as of PR #1) its live breaker
actually works. The economics are UNPROVEN. This pilot is how we prove or kill
them. Do NOT scale past these numbers until the pilot reports green.

---

## Why shadow can't answer this (why a live pilot is required)
Rewards accrue only to **real resting orders** that score in the per-second
snapshot. Shadow orders earn nothing and take no adverse selection, so shadow can
validate plumbing but **cannot measure the edge**. Only real capital at the touch
produces the number we need.

## The "shorter than a week" design
Polymarket US **credits** earnings ~5+2 business days after a period ends — that
delay is exchange-side and unavoidable. So we DON'T wait for it to decide. We judge
the pilot in **24–48h** on signals we compute ourselves each cycle:

1. **Self-computed reward estimate (same-day).** We know our resting size at the
   touch; we read the book to estimate total competing score; our pro-rata share ×
   (pool / period length) = modeled reward/hour. `scripts/poly_scan.py` already
   does the share math — extend it to log a running modeled-reward number.
2. **Adverse selection (real-time).** `/v1/portfolio/positions` (read live even in
   shadow). Track net drift + unrealized mark-to-market each cycle. If resting at
   the touch keeps getting run over (net accumulates one-sided and marks against
   us), that's the edge-killer, visible same-day.
3. **Maker rebate (per fill).** 0.0125·C·p·(1−p) per contract traded — accrues on
   every fill, immediate.

Decision in 24–48h: **modeled net = modeled rewards + rebate − realized adverse
selection.** The credited-earnings read (~1 wk later) only CONFIRMS the model.

## Prerequisites (do IN ORDER before flipping live)
1. **[DONE] Merge PR #1** — the corrected `netPosition` breaker is on `main` and
   auto-deployed (still shadow). Without it the breaker is blind to real positions.
2. **Clear legacy exposure** — REQUIRED, because the breaker will otherwise trip
   instantly on the old 332-lot:
   - Cancel the orphaned COD orders: Render one-off shell on `polymarket-mm`,
     `CONFIRM_LIVE_CANCEL=yes python scripts/cancel_all_live.py` (dry-run first).
   - Close the **332-contract `tec-f-wc-2026-07-19-groupb-winner-bih`** position in
     the Polymarket UI (a script can't close a position without placing an order).
   - Confirm account shows **0 open orders, 0 positions** before going live.

## Market selection (avoid the weeks-long trap)
Quote only **short-period** reward markets (`live` / `day_of` / `daily_event`) so a
period completes inside the pilot. **Do NOT** quote weeks-to-settle futures (e.g.
WC group-winner, settles Jul-19) — they lock capital for the whole tournament and
can't give a fast read. `POLY_MAX_MARKETS` caps breadth to the top pools.

## Pilot config (Render env on `polymarket-mm`) — staged, BOT_MODE stays shadow until go
| var | pilot value | note |
|-----|-------------|------|
| `POLY_BUDGET` | `50` | ~10% of the $500 bankroll at risk |
| `POLY_SIZE` | `25` | per-side contracts (auto-scaled to BUDGET/N) |
| `POLY_MAX_MARKETS` | `2` | quote the 2 highest-pool short-period markets |
| `POLY_MAX_INVENTORY` | `50` | per-market net cap (breaker) |
| `POLY_EXPOSURE_CAP` | `75` | total filled-exposure cap (1.5×budget) |
| `POLY_DAILY_LOSS` | `15` | best-effort unrealized-loss breaker |
| `POLY_POLL_SECS` | `20` | reconcile cadence |
| `BOT_MODE` | `shadow`→`live` | the **single deliberate flip** = go-live |

Worst-case capital at risk is bounded: 2 markets × 50-contract cap × ~$1 ≈ $100
gross, breaker stands aside past the caps.

## Go / no-go
- **GO** to scale (toward the $500) if modeled net is **clearly positive** over
  24–48h AND adverse selection is bounded (no runaway one-sided accumulation), and
  the later credited-earnings read confirms within the same ballpark.
- **KILL** (back to shadow, thesis closed) if modeled net is negative or the touch
  gets consistently picked off — same pattern that closed every Kalshi thesis.

## Kill switch
`BOT_MODE=off` (or `shadow`) on Render → next cycle cancels all + stands aside. The
breaker auto-cancels + stands aside on any cap breach. `cancel_all_live.py` clears
anything orphaned.

## What I (Claude) can / can't do
- CAN: deploy/config the worker via Render (incl. the live flip), read its logs,
  extend the scan tooling, read account state once it's logged by the worker.
- CAN'T: hit `api.polymarket.us` directly from this sandbox (geo-blocked, 403), so
  the legacy-clear steps that need a live API call run on Render/UI (operator).

---

## Live-rails smoke test (precursor — validate the ORDER PATH, not economics)
Runs BEFORE the economics pilot above. Goal: prove the two rails that have silently
failed before — (1) a post-only order actually **RESTS** at the touch (the first pilot's
orders were 200-ACKed but never rested, $0 traded), and (2) the breaker correctly reads a
real position via **`netPosition`** (was blind pre-fix). This is a plumbing check, NOT a
reward-economics read.

Resting is verifiable even on a static book (no fill needed). The `netPosition` breaker
read is a bonus that only exercises IF a fill happens. As of 2026-07-14 the whole reward
surface is $100 in-play table-tennis pools (`aec-czechligapro-*`, `max_pool=$100`), so the
test would run there — keep size tiny because in-play fills are adverse-selection-prone.

Switches (operator, on the `polymarket-mm` Render worker unless noted):
- `POLY_LIVE_ARMED=true`   — real orders; unarmed runs the live path in **shadow ($0)**.
- `POLY_ALLOW=czechligapro` — MUST permit the target market or nothing quotes (default is
  `worldcup,fwc,-wc-`, which excludes table tennis). Match token vs slug+programId.
- `POLY_SIZE=2 POLY_BUDGET=10 POLY_MAX_MARKETS=1 POLY_MAX_INVENTORY=5 POLY_EXPOSURE_CAP=8`.
- `desired_mode=live` (+ small budget, `live_until=now+1h`) via `poly_control` — the app
  "Go Live" button OR a direct Supabase write (the agent CAN flip this); arming stays
  dashboard-only. Env changes need a fresh **deploy**, not a restart.

Verify (worker log / heartbeat, or Render MCP logs once connected):
- a `cycle: … placed_ok=N` with N>0 AND a read-back showing **open orders > 0** (RESTING,
  not merely 200-ACKed).
- if a fill occurs: heartbeat positions carry `netPosition`; breaker inventory reads
  non-zero and trips past `MAX_INVENTORY`, then cancels-all + stands aside (bounded).
- STOP: `desired_mode=track` (auto-cancels resting orders on leaving live) or
  `POLY_LIVE_ARMED=false`. Worst case ≈ $5–10 gross, breaker-bounded.

**RESULT — 2026-07-14 16:09Z: order-resting rail PASSED.** Flipped `poly_control`
`desired_mode=live` (budget $10, 15-min `live_until`) from the cloud agent — worker placed
post-only quotes on `aec-czechligapro` reward markets: `place OK … executions:[]` plus a
**read-back `st=200`** showing the order resting, and the next cycle logged
`resting(pre-cancel)=3` → orders REST across cycles (the pre-migration "200-ACK but never
rested" failure is gone). `netPosition` read confirmed live (`pnl_snapshot open_contracts=5`
on a pre-existing `arankc-atp` position). Reverted to `track`; worker logged
`left live -> cancelled 3 resting orders`. **Balance unchanged at $127.93** (no fills, no
loss). Not yet exercised: breaker tripping on a real fill (no fill occurred; it's unit-tested
and the `netPosition` read is confirmed). Driven via the direct Render API + Supabase (the
Render MCP plugin is connected but its `${RENDER_API_KEY}` header didn't resolve — used the
valid VM `RENDER_API_KEY` directly).

## Economics pilot — RUNNING (started 2026-07-15 19:56Z)
Fat pool arrived (`max_pool=$2400` Liga MX `atc-lmx-*`). Bounded live economics pilot armed:

| knob | value |
|------|-------|
| `POLY_ALLOW` | `lmx` (fat-pool only; briefly included aec then tightened) |
| `POLY_BUDGET` / control budget | `$50` |
| `POLY_SIZE` / `POLY_MAX_MARKETS` | `25` / `2` |
| `POLY_VOL_CAP` | `0.05` (Stage 2 hard-exclude choppy legs) |
| `POLY_MAX_INVENTORY` / `EXPOSURE_CAP` / `DAILY_LOSS` | `50` / `75` / `15` |
| `POLY_LIVE_ARMED` | `true` |
| `desired_mode` / `live_until` | `live` / **2026-07-16T19:56:18Z** (original end; re-affirmed after resume) |
| takers | `wx_taker=off`, `mlb_taker=off` |
| `POLY_DENY_SLUGS` | includes `atc-fwc-eng-arg-2026-07-15-arg` (Andrew's manual WC soccer bet; +462 — not bot) |

**Live confirmed:** quoting Liga MX @size=25, `placed_ok=4 rej=0`, resting. Heartbeat
`mode=live status=quoting`.

**Resume (2026-07-16 ~13:30Z):** breaker had tripped on the manual +462 WC position (idle
since 15th 20:19Z). Deny-listed that slug, redeployed, cleared halt. Kept `live_until` at
the original **19:56Z today** end (rest-of-day only).

**Same-day tooling (while waiting):**
- `python scripts/pilot_readout.py` — heartbeat GO/KILL fact sheet (Supabase only)
- `python scripts/whale_paper_score.py [--settle [--write]]` — lag cost + optional settle PnL
- `python scripts/reward_earnings.py` — `/v1/incentives/earnings` (run on US egress / Render)

**Watch for rest-of-day (GO/KILL):** modeled reward share vs inventory drift / unrealized loss;
breaker trips; credited earnings later (~5+2bd). **KILL:** set `desired_mode=track` (auto-
cancels resting) or restore `POLY_ALLOW` + `POLY_VOL_CAP=0`. Snapshot of pre-pilot allow was
`aec,arankc,apdc,cranc`. Balance ~$465 at resume (deposit + manual bet); pilot budget still $50.
