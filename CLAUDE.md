# prediction-mm — Worklog & Operating Rules

Polymarket US liquidity-reward market maker. Fresh repo (2026-06-20), migrated
from `kalshi-mm` (Kalshi retired — every Kalshi income thesis closed; see
"Closed theses" below). Squashed baseline; full Kalshi history lives in the old
repo.

> **Standing rule:** after every bug fix, ship, or significant decision, add a
> dated entry to the Incident Log below. Keep this file ≤ ~1 page.
>
> **Status-summary discipline:** when reporting status in chat, lead with a
> one-line TL;DR, then ≤4 bullets — *Live? · Money at risk · Next · Need from
> you*. Deep detail goes in the worklog/PR, not the chat. No raw log dumps unless
> asked.

## Thesis
Polymarket US (CFTC/QCEX-regulated, `api.polymarket.us`) **pays market makers** —
a maker rebate (`0.0125·C·p·(1−p)`) plus liquidity-reward pools
(`Score = discountFactor^(ticks_from_best)·size`, snapshotted ~1/s, split
pro-rata). That LP reward is the income mechanism Kalshi never had. **Unresolved:**
pool cadence/scope and live adverse selection — rewards have been World-Cup-only
and the economics aren't proven. **Validate-first: stay in shadow until a live
reward-earnings read confirms positive net economics.**

## Invariants (must never break)
- **`BOT_MODE=shadow` = no orders reach the exchange.** `PolyClient(live=False)`
  records intended orders to `shadow_orders` and returns a synthetic ack; a test
  asserts no network leak. Only the operator flips `BOT_MODE=live`.
- **Quote only reward-eligible markets in an active window** — selection comes
  from `/v1/incentives` (`RewardMarketCache`), gated by `polymaker.program_active`.
- **Full reconcile every cycle:** `cancel_all_orders` (with each order's
  `marketSlug` in the body — empty body 400s) BEFORE re-posting, or orders pile up.
- **Post-only at the EXACT book price** (futures tick in 0.001, not 0.01 — rounding
  crosses a 1-tick book and gets post-only-rejected).
- **Budget bounded:** top `POLY_MAX_MARKETS` by pool, size = `BUDGET/N`; breaker
  trips on per-market inventory > cap, total exposure > `EXPOSURE_CAP` (1.5×budget),
  or unrealized loss < −`POLY_DAILY_LOSS`, then cancels all + stands aside.

## Architecture
| File | Role |
|------|------|
| `poly_runner.py` | Worker: select reward markets in-window, full-reconcile quote loop, breaker. `BOT_MODE` shadow/live/off. |
| `core/polyclient.py` | Polymarket US REST + ED25519 auth (`msg = f"{ts_ms}GET{path}"`, seed = `base64(SECRET)[:32]`). Shadow-gated order layer (`place_order`/`cancel_order(id, market_slug)`/`get_open_orders`/`get_positions`/`get_book`/`get_incentives`). |
| `core/polymaker.py` | Pure quoting: `maker_quotes` (join touch, inventory skew/cap), `program_active` (period-driven reward window). |
| `lib/fairvalue.py` | **Dormant** salvage — spot-anchored fair value (Bachelier). Not used by the reward maker. |
| `scripts/poly_scan.py` | Read-only reward-market book scan + pro-rata share estimate. |
| `tests/` | `test_polyclient_shadow` (no-leak gate), `test_polymaker`, `test_poly_breaker`, `test_fairvalue`. |

Runtime is stdlib-only except `cryptography` (ED25519). Keys in repo-root `.env`
(`POLYMARKET_API_KEY` + `POLYMARKET_SECRET`); never commit them.

## Deploy
Render background worker `polymarket-mm` (build `pip install -r requirements.txt`,
start `python poly_runner.py`). Start command + env are dashboard-only (Render MCP
can read logs/env but can't create workers or edit the start command).

## Closed theses (Kalshi — do not re-litigate)
MM bleeds (adverse-selected in every config); no executable guaranteed arb
(7.5k events swept, 0 robust); convergence efficient (weather settles on official
climate report not raw obs; sports converge to $1 instantly); momentum needs
volatility to even fire (0 trades). Lesson that paid off repeatedly: **validate
read-only before funding; reconcile any tape-derived P&L against account balance.**

## Incident Log

### 2026-06-29 — Tracker+farm on one worker; PM market-odds capture; golf-bet breaker wedge fixed
- **Breaker wedge (fixed live):** after merging Option C, the worker tripped every cycle on
  a **400-lot golf position** (`tec-pga-travcham-2026-06-28-w-wyncla`, Andrew's OWN manual
  bet, not the bot) — 8× the 50 inventory cap → stood the whole bot aside. Added it to
  `POLY_DENY_SLUGS` (excludes from breaker, like the old 332-WC future) + fresh deploy
  (env needs a deploy, not restart). Farm resumed: `5/11 mkts placed_ok=8 rej=0`. No bot
  bug — manual bet. Slug carries today's date; drop from deny after it settles.
- **Option C (shipped):** `track` loop now runs the read-only tracker passes (wx/soccer/
  sports/golf/settle) in BOTH live and track modes — the cricket reward farm and the
  validation-week model tracking run on ONE worker. Fixed a double `time.sleep`.
- **PM market-odds capture (shipped, diagnostic-first):** to measure model-vs-MARKET edge
  (not just calibration). `core/espnfeed` now captures team `abbreviation`s; `pmodds`
  matches a game to its PM market by team-token+date (abbrev/prefix aware, BOTH teams
  required), reads the book, attaches `market_bid/ask` + `meta.pm_slug/pm_yes_side` to the
  sports rows. `attach_market_odds` LOGS match-rate + samples (incl. the market outcome
  label) so the PM moneyline slug structure is confirmed from logs before `edge` is
  computed (stays null for now). NOTE: catalog samples so far were futures (champ/winner);
  read `odds: matched X/Y` + `odds:` sample lines next deploy to verify per-game matching.
  112 tests green.

### 2026-06-26 — App "Go Live" button (WC reward-maker) — armed-gated, bounded, auto-revert
Andrew wanted a button in the app to flip a one-day live test (World-Cup only) instead of
doing it via CLI. Built it end-to-end, safe-by-default:
- **poly_control** gained `budget` (default 50) + `live_until`. App Settings has a **Go
  Live — World Cup** card (budget $25/$50/$100, confirm dialog, live status + auto-revert
  time, Stop button). `setLive(budget, hours)` writes desired_mode=live + live_until=now+24h.
- **Worker (`track` loop is now control-driven):** honors `live` → runs the WC reward-maker
  (`live_cycle`, extracted from the legacy quoting loop) bounded by control budget; idles on
  `off`; auto-reverts to `track` when `live_until` passes; **cancels all resting orders when
  leaving live** (no orphans). Heartbeats live status to the app.
- **Two safety gates:** (1) `POLY_ALLOW` allow-filter (default `worldcup,fwc,-wc-`) → live
  only quotes WC markets; (2) **`POLY_LIVE_ARMED`** operator env — REAL orders require it;
  unarmed, the button runs the live path in **shadow ($0)**. So the button is safe to ship;
  real money needs a deliberate one-time `POLY_LIVE_ARMED=true` on the worker.
- 102 tests green (+6: allow-filter, live_cycle gating). App typechecks + web bundle builds.
- **CAVEAT (unchanged):** the live order path isn't proven — last pilot's post-only orders
  didn't rest ($0 traded). Arming may still trade $0 until the order layer is fixed; watch
  the Overview after going live.

### 2026-06-25 — All-sports buildout (Phase A) + settlement + Expo control app
Andrew: "build all sports — NBA, Tennis, Golf, NFL, NCAA Football, MLB — then the app
like before." Delivered read-only (no orders), in phases:
- **Settlement pass** (`core/settle.py`, injected fetchers, tested): weather buckets vs
  observed daily high, soccer/sport 1X2 vs ESPN finals → writes realized_yes/pnl.
  `scripts/calibration.py` = per-model Brier + reliability. Folded into track mode.
- **Phase A generic engine:** `core/espnfeed.py` (one ESPN parser, team OR athlete),
  `lib/elo.py` (2-way win/loss Elo, HFA off for neutral/tennis), `core/sportstrack.py`
  (SPORTS registry — add a sport = one line). Live: **MLB 55 fixtures→110 preds**, NBA
  seeded 400, NFL/NCAAF correctly 0 (offseason). **Tennis (atp/wta) returns 0** — ESPN
  tennis nests matches under tournament *groupings*, different shape; feed fix = Phase B.
- **Phase B (tennis) DONE:** `espnfeed.parse_scoreboard` now also descends into
  `events[].groupings[].competitions[]` (ESPN nests tennis matches per tournament) and
  keys on the competition id — ATP/WTA now parse. Tested on the grouped shape.
- **Phase C (golf) DONE:** `lib/golf.py` (per-player skill in [0,1] seeded from finish
  positions → softmax win probs), `core/golffeed.py` (ESPN leaderboard parse → field +
  winners), `golf_pass` records top-50 contenders of the current tournament, `settle_golf`
  resolves vs the winner. 94 tests green. ESPN tennis/golf JSON shapes built from spec —
  verify per-sport seed counts in worker logs (this sandbox is geo-blocked from ESPN).
- **Expo control app** (`app/`, mirrors kalshi-mm-app stack; typechecks + web bundle
  builds): auth, Overview (worker status + per-model Brier/hit-rate), Predictions,
  Calibration (reliability bars), Settings (mode/kill switch). Tables `poly_status`
  (worker heartbeat) + `poly_control` (desired_mode) — worker honors `off` (idle) +
  heartbeats each 60s. Trading modes gated/reported, not executed. Deploy = Render
  static site (app/, `npx expo export -p web`, publish app/dist, SPA rewrite).
- **App DEPLOYED:** Render static site `prediction-mm-app` (srv-d8unq5egvqtc73bc6pug)
  at https://prediction-mm-app.onrender.com (SPA rewrite set, EXPO_PUBLIC_SUPABASE_* env).
- **VERIFIED LIVE (all six sports + golf + settlement):** worker logs confirm tennis now
  parses (atp/wta 11 fixtures each — grouping fix), golf records (Travelers Champ field
  72 → top 50), MLB/NBA seed + record. **Settlement fixed:** `due=11 resolved=0 → 11`
  after switching to a ±1-day ESPN window (TZ boundary: 23:00Z games file under prev ET
  day). DB: MLB 86 settled, soccer 36, weather 150; golf/tennis settle as events finish.
- **TENNIS SEEDING FIXED (diagnostic-driven):** atp/wta seeded 0 because (a) ESPN won't
  return wide-range tennis history → fetch in weekly chunks (`espnfeed.results_over`); and
  (b) the real blocker — `recent_results` required NUMERIC scores, but tennis scores are
  sets so `_score`→None filtered every completed match out. Fix: decide results by ESPN's
  per-competitor `winner` flag (`winner_of`/`_home_won`), falling back to scores for team
  sports. Now **atp seeds 2639, wta 3514** real results → informed ratings. 96 tests green.
- **REMAINING:** App Phase 2 trading controls (budgets/go-live) unlock when an edge
  validates. Validation-week clock running with all 8 models recording + scoring on real
  signals.

### 2026-06-23 — Prediction tracker + soccer feed shipped; one-week validation clock started
Andrew green-lit the prediction tracker + a soccer results feed, "keep it scalable
(all sports later)", **one week then go live**. Built read-only (no orders, $0 risk):
- **`model_predictions` (Supabase)** — model-agnostic schema (`model`/`sport` cols +
  `meta` jsonb) so new sports need ZERO schema change. `core/track.py` = stdlib
  PostgREST writer. Unique `(model,market_slug,settle_date,run_date)` index +
  `on_conflict` target = idempotent daily snapshots (deploy overlap/restart safe;
  early version 409'd on the wrong conflict target — fixed to no-op 200).
- **`core/soccerfeed.py`** — ESPN scoreboard (no key; runs on worker, this sandbox is
  egress-blocked like wxfeed). Pure `parse_scoreboard` unit-tested; `recent_results`
  (seed Elo) + `upcoming_fixtures`. Wires the existing `lib/soccer` Elo into a recorder.
- **`BOT_MODE`** `wxedge`/`soccer`/**`track`** — `track` runs BOTH passes on one
  worker (weather ~10min, soccer ~hourly). Worker (`srv-d8kmtfrtqb8s73eg6tu0`) now on
  `BOT_MODE=track`, SUPABASE_URL/ANON_KEY set. **Live verified:** weather 60 buckets +
  soccer 66 (WC seeded ~45 results→~20 fixtures; EPL/MLS offseason=0), 0 dupes.
- Next: **settlement pass** (ESPN finals + NWS official highs → `realized_yes`/`pnl`)
  so the week ends with a calibration + net-edge verdict, not just raw predictions.
- **Weather-tuning Q (Andrew):** validate-first — DON'T tune blind now. Highest-value
  tune is empirical σ from this week's realized error (replaces hand-set 2+1.5°F/day),
  then a multi-model ensemble + intraday max-so-far conditioning. Model skill likely
  isn't the binding constraint (Kalshi weather was efficient; settlement-source nuance
  was the killer) — the tracker is what tells us.

### 2026-06-20 — Repo migrated from kalshi-mm (Phase 1)
Assembled the Polymarket keeper tree as a clean baseline: `poly_runner.py`,
`core/polyclient.py`, `core/polymaker.py`, `scripts/poly_scan.py`, the poly tests,
and `lib/fairvalue.py` (+ its tests, salvaged from branch
`claude/kalshi-mm-multi-bot-x5xrd5`, import re-pointed `core`→`lib`). requirements/
.env.example/render.yaml re-cut Polymarket-only; CLAUDE.md distilled. **Dropped the
two convergence scanners** (`poly_wx_scan`, `poly_sports_scan`) per Andrew — they
were closed-thesis tools depending on dropped modules (weatherfeed/gamefeed/
convergence→alpha); only `poly_scan.py` (live reward thesis) came along. See
`FOLLOWONS.md` for the esports-orders investigation (do before any live quoting).

### 2026-06-20 — Migration phases 3–5 (cutover)
- **Render (Phase 4, done):** the live poly worker physically *is* the old
  `kalshi-bot-hourly` service (`srv-d8kmtfrtqb8s73eg6tu0`, runs `python
  poly_runner.py`) — **renamed to `polymarket-mm`**. Deleted the 4 dead Kalshi
  workers (kalshi-mm core, kalshi-bot-sports, kalshi-bot-alpha, kalshi-runner).
  Kept: polymarket-mm, kalshi-mm-app (static), kalshi-mm (dashboard web),
  just_pick_it (unrelated).
- **Render (Phase 4, BLOCKED):** repointing polymarket-mm's repo to
  `prediction_mm` fails — Render's GitHub App lacks access to the new **private**
  repo ("invalid or unfetchable"). It still builds from `kalshi-mm@main` (byte-
  identical poly code). **Unblock:** grant Render's GitHub App access to
  `afehrenbach93/prediction_mm`, then PATCH the service repo→prediction_mm/main.
- **Supabase (Phase 3, done — nothing to delete):** project `pecafqwbfveovymyjako`
  ("Andrew's Project"). The plan's cleanup targets are already absent — no `macro`
  bot_control row, no `bot_markets`/`bot_series` tables, zero test-pollution rows
  (shadow_orders/fills/etc. for tickers A/B/TICK or bot_id='t'). `0001_baseline.sql`
  stays deferred until poly telemetry lands (follow-on #2). **Security:** 4 tables
  have RLS disabled (market_snapshots, bot_config, fix_requests, app_users).
- **Phase 5 (BLOCKED from this session):** archiving `kalshi-mm` + deleting its 8
  dead branches need GitHub repo-settings/ref-delete access not available here (git
  proxy blocks delete refspecs; no MCP delete-ref tool). Also hold the archive
  until polymarket-mm is repointed off kalshi-mm. **Do manually** (or once tooling
  allows): delete branches `claude/kalshi-mm-multi-bot-x5xrd5`,
  `claude/kalshi-mm-premortem-93GCf`, `claude/kalshi-multi-agent-build-ctzKn`,
  `claude/kalshi-multi-agent-session-g8ftz5`, `claude/migration-plan-continuation-grzs6d`,
  `claude/review-share-purchase-logic-Gq3wk`, `claude/trusting-knuth-6b8fcd`,
  `feature/mm/web-app-development-scaffold`; keep `main`.
- **Keys:** Render key already rotated (Andrew). Polymarket key rotation is
  operator-only (rotate in Polymarket UI + update polymarket-mm env).

### 2026-06-21 — Breaker netPosition bug fixed; Phase 4 confirmed done; orphan-cancel tool
"Global access keys added" = `RENDER_API_KEY` in env + `prediction_mm` now in the
GitHub scope. Verified via the Render API: **Phase 4 cutover is already done** — the
`polymarket-mm` worker (`srv-d8kmtfrtqb8s73eg6tu0`) builds from
`prediction_mm@main`, `autoDeploy=on commit`, and **`BOT_MODE=shadow`** (the
2026-06-20 halt is in effect; no orders reach the exchange). So a merge to `main`
safely redeploys a SHADOW worker.
- **Fixed the breaker `netPosition` bug** (FOLLOWONS #0.2, the must-fix-before-live
  item): `positions_net` now reads `netPosition` first (+ `qtyBought-qtySold`
  fallback); the 332-contract WC position now trips the inventory cap. +3 regression
  tests; 29 green.
- **Added `scripts/cancel_all_live.py`** — one-shot, risk-reducing-only live cancel
  for the orphaned COD orders (no order placement; `CONFIRM_LIVE_CANCEL=yes` gate,
  dry-run by default). This Claude env is geo-blocked from `api.polymarket.us`
  (403), so the **operator must run it** (Render one-off shell on `polymarket-mm`
  has creds + US egress). It does not close the 332 position — close that in the UI.
- **OPEN for Andrew:** (a) run the cancel tool + close the 332 position; (b) decide
  the esports scope policy — `/v1/incentives` currently lists COD `aec-cod-*` reward
  pools and the bot quotes whatever is reward-eligible; if esports should be excluded,
  add an allow/deny slug-prefix filter on `RewardMarketCache.refresh()`. Stay shadow
  until the live reward economics validate regardless.

### 2026-06-21 (later) — WENT LIVE on a small bounded PILOT (Andrew funded $150, "switch live")
Validated all of the below in SHADOW via Render logs before flipping. **The worker
is now `BOT_MODE=live`** on the $50 pilot. See `PILOT.md` for the full runbook.
- **`POLY_DENY_SLUGS`** added (`#2`): denied slugs are excluded from selection AND
  from the per-market inventory breaker, so we **keep** the held 332-lot WC future
  (`tec-f-wc-2026-07-19-groupb-winner-bih`, cost $26.68) without it standing the bot
  down. Verified live-account read: with deny set, the process goes to *idle* (not
  *tripped*) on the 332. Andrew chose to keep the loose COD orders too, but the
  full-reconcile loop cleared them on go-live (now 0 resting orders).
- **Pilot env (Render):** `BOT_MODE=live`, `POLY_BUDGET=50`, `POLY_SIZE=25`,
  `POLY_MAX_MARKETS=2`, `POLY_MAX_INVENTORY=50`, `POLY_EXPOSURE_CAP=75`,
  `POLY_DAILY_LOSS=15`. Confirmed in the live START line.
- **Earnings cadence (verified vs PM docs):** reward score snapshot ~1/s; PM **US
  credits earnings ~5+2 business days after a period ends** (uncompressible). So the
  pilot is judged in 24–48h on SELF-COMPUTED signals (resting reward-score share +
  real-time adverse selection from `/v1/portfolio/positions` + maker rebate), with
  the credited number as later confirmation. Macro reward pools are **$10k/day split
  pro-rata across instruments** (pools are real, not a smear).
- **RENDER GOTCHA (cost two cycles):** updating env-vars via the API does NOT
  propagate to the running process, and a `restart` reuses the deploy's OLD env
  snapshot. You must trigger a fresh **deploy** (`POST /deploys`) to pick up env
  changes — confirm via the START line values before trusting them.
- **State at handoff:** live but IDLE — "no reward window now" on the 2 current
  reward markets; it arms and quotes at pilot size when a window opens. A filtered
  log monitor watches for first-quote / order-count stability / breaker trips /
  errors. **Watch when it first quotes:** confirm stable order count (the historical
  120-order accumulation bug) and check the quoted slugs/periods — only ONE WC-future
  slug is denied, so if it quotes other weeks-long futures, add them to
  `POLY_DENY_SLUGS` (pilot wants short-period markets).

### 2026-06-22 — PILOT first-quote: post-only orders DON'T REST (no risk); worker SUSPENDED
First reward window hit (2 Dota2 esports `aec-dota2-*`, `day_of`). The bot placed
4 orders/cycle but the account showed **0 resting orders every cycle**, repeating.
Added `place_order` response logging (`#4`): live cycles show **`placed_ok=2 rej=0`
but `resting(pre-cancel)=0`** — orders are 200-ACCEPTED yet never rest. **Andrew
confirmed 0 open orders in the Polymarket UI** (ground truth — this env is geo-
blocked from the API). So: **no accumulation, $0 at risk** (also corroborated by
`rej=0` over 20+ cycles — real resting orders would reserve cash and eventually
exhaust the $150 → rejects, which never happened). Worker **SUSPENDED + `BOT_MODE=
off`** (instant hard stop). The held 332 WC position is untouched.
- **OPEN BUG (next step):** why don't our post-only (`participateDontInitiate`)
  quotes rest? join-the-touch at best_bid/best_ask should rest. Diagnose by logging
  each order's status read-back (`get_order`) right after placing — one focused
  watched live cycle — then fix `maker_quotes`/order body. Likely: post-only killed
  (would-cross/lock), bad intent for opening a side, or tick/price issue per market.
- **Lesson reinforced:** the order layer keeps shipping live-untested; `placed_ok`
  (HTTP 200) ≠ a resting order. Verify resting via the UI / a read-back, not the ack.
