# Follow-ons

## #0 — CONFIRMED LIVE 2026-06-20: bot quoted real COD esports orders; halted to shadow
During the migration cutover the worker (BOT_MODE=live, inherited — already live
pre-migration) was observed **placing real orders on Call of Duty esports reward
markets**: slugs `aec-cod-van-lat`, `aec-cod-tor-tex`, `aec-cod-riyf-min`
(2026-06-20). So follow-on #1 is **answered**: `/v1/incentives` itself currently
lists COD esports reward pools (`aec-cod-*`), and the bot correctly quotes whatever
is reward-eligible — the scope, not the selection logic, is the issue. Halted to
`BOT_MODE=shadow` per Andrew.

**TWO real items left after the halt:**
1. **Resting live orders not cancelled.** `cancel_order` is shadow-gated, so the
   ~4–6 real COD orders placed during the live window are STILL resting on the
   exchange (shadow can't cancel them, and this environment can't reach Polymarket
   to cancel). **Cancel them manually in the Polymarket UI**, or via a one-shot live
   `cancel_all_orders` run with creds. Also a pre-existing **332-contract position
   on `tec-f-wc-2026-07-19-groupb-winner-bih`** (WC futures) is open.
2. **Breaker is blind to real positions (BUG).** `poly_runner.positions_net` parses
   net from keys `net/netQuantity/quantity/size/position`, but the live API field is
   **`netPosition`** (raw shape logged: `{'netPosition':'332','qtyBought':'332',
   'qtySold':'0',...}`). So `breaker_check` read net=0 and did NOT trip on the
   332-contract position even though it exceeds `MAX_INV=300`. **Fix the field
   mapping (add `netPosition`, and `avgPrice`/entry equivalents) and verify against
   the logged raw shape BEFORE any future live run.**

## #1 — Investigate esports orders (DO BEFORE ANY LIVE QUOTING)
Andrew saw esports orders on the Polymarket account. The old Kalshi DB is clean
(zero esports across all tables back to April), so these were on **Polymarket**.
`poly_runner` writes no telemetry to Supabase, so they're invisible there.

**Important nuance found during the migration (refines the original hypothesis):**
market selection in `poly_runner.py` is NOT a broad category pull. `RewardMarketCache.
refresh()` builds its market set **exclusively from `/v1/incentives`** (`get_incentives()`),
and `polymaker.program_active` gates each to an active reward window. So the bot
only quotes markets that the incentives endpoint itself returns as reward-eligible.

That means the likely cause is one of:
1. **`/v1/incentives` listed esports reward pools** at the time (the venue rotates
   programs daily — WC in-play → WC futures was observed within hours). If esports
   reward pools existed, the bot correctly quoted them. → Check what `get_incentives()`
   returns now and whether esports slugs appear.
2. **The orders were live, not shadow.** Confirm the worker's `BOT_MODE` at the time.
   render.yaml defaults `shadow`; if it was flipped `live`, real orders rest.
3. **Manual orders** Andrew placed himself (precedent: a manual sports side-bet in
   the Kalshi era was mistaken for the bot).

**Investigation steps (ground truth first):**
1. Polymarket order/trade/position history — `api.polymarket.us` portfolio
   endpoints via `PolyClient` (auth with POLYMARKET_API_KEY/SECRET). List the actual
   esports market slugs touched + timestamps. (`get_open_orders`, `get_positions`
   exist; add a trade-history reader if needed.)
2. Render logs for `polymarket-mm` — the loop logs each quoted slug + period; grep
   for esports slugs and the `reward-market meta refreshed` / `cycle` lines.
3. `client.get_incentives()` — does it currently return any esports (LoL/CS/Valorant/
   Dota) reward markets? If yes, the selection is "working" and the question is
   whether we WANT esports reward markets in scope.

**Fix (if scope needs tightening):** add an explicit allow/deny on
`RewardMarketCache.refresh()` (e.g. category or slug-prefix filter) so only the
desired reward programs are quoted, even if `/v1/incentives` lists others.

## #2 — Telemetry + app
`poly_runner` emits no Supabase telemetry (orders/fills/heartbeat) and the old
Expo app is Kalshi-bot-centric. Add Polymarket telemetry + retarget the app.

## #3 — Resolve LP-reward economics
Pool cadence/scope + live adverse selection. Read the authenticated
`/v1/incentives/earnings` once funded (calc is delayed ~1 week after period end).
Stay in shadow until this confirms positive net economics.
