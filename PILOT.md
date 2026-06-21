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
