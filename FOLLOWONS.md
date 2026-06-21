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
1. **Resting live orders not cancelled — STILL OPEN; needs an operator run.** The
   ~4–6 real COD orders + the **332-contract position on
   `tec-f-wc-2026-07-19-groupb-winner-bih`** are still on the account. This Claude
   environment is geo/network-blocked from `api.polymarket.us` (HTTP 403, no creds
   present), so it CANNOT cancel them from here. Tool added 2026-06-21:
   **`scripts/cancel_all_live.py`** — a one-shot, risk-reducing-only live cancel
   (places no orders; requires `CONFIRM_LIVE_CANCEL=yes`). **Operator action:** run
   it where Polymarket is reachable (e.g. a Render one-off shell on `polymarket-mm`,
   which already has the creds + US egress) — dry-run first, then confirm. It does
   NOT close the 332 position (closing needs an order); close that in the UI.
2. **Breaker netPosition bug — FIXED 2026-06-21.** `poly_runner.positions_net` now
   reads `netPosition` FIRST (confirmed live field), derives net from
   `qtyBought-qtySold` as a fallback, and broadens the entry-field candidates. Added
   regression tests against the logged raw shape (`test_live_netposition_field`,
   `test_live_position_trips_inventory_cap`) — the 332-contract position now trips
   the inventory cap. 29 tests green. (Entry/avg-cost field name is still unverified
   from a live position; the hard inventory + exposure caps don't depend on it, so
   an unparsed entry only disables the best-effort unrealized-loss check.)

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
