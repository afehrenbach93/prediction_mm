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
>
> **Usage discipline (the operator is on a paid plan and hits a daily cap):**
> every message re-reads the whole context, so bloat is paid for on every turn.
> (1) Keep sessions single-purpose; prefer a fresh session per task over one long
> multi-hour thread. (2) NO long client-side watch loops — deploy/act once, then
> check the Supabase `poly_status` heartbeat (one small read) instead of tailing
> Render logs for minutes. (3) Read specific line ranges of large files
> (`poly_runner.py`) — never re-read the whole file. (4) No raw log dumps in chat.
> (5) Keep the two `CLAUDE.md` files small; `kalshi-mm/CLAUDE.md` is a retired stub —
> do not re-expand it.

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

### 2026-07-02 — Weather sell-taker went live; edge is NEGATIVE (settlement-source flaw); P&L tracker + usage cuts
- **Weather sell-taker built + went live** (`WX_TAKER=live`, `WX_BUDGET`, `core/wxtaker.py`,
  `wx_taker_cycle`/`wx_pass` in `poly_runner.py`). Sells over-priced temperature buckets
  (market bid ≥ model_prob + 0.10). Probe (1 order) until first confirmed short, then scale
  across fresh buckets to budget. **Independent risk accounting from the cricket farm**:
  `breaker_check` and `cancel_all_orders` both EXCLUDE `tc-temp` so the two strategies never
  cross-trip / cross-cancel. Observability via `poly_status.detail` heartbeat (cricket log
  spam buries weather lines).
- **VENUE ORDER SEMANTICS (hard-won, live-verified):** on PM US `SELL_SHORT` actually opened
  a LONG (inverted vs its name); **`BUY_SHORT` correctly opens a short (bet NO)**. Taker
  orders (post_only=False) get killed (executions=[], then 404) if they don't cross → shorts
  must REST as a maker: **post_only=True + `ORDER_INTENT_BUY_SHORT` at `bid+0.01`**. Also hit
  429 rate-limits from double-reading ~60 books/cycle → `wx_pass` and the taker now SHARE the
  book reads. Total real cost of the whole debug saga: ~$1 (probe + safety rails held).
- **VERDICT — weather edge is NEGATIVE; recommend halting.** Settled account-level P&L
  (`wx_settlement_pnl`): **5 settled, net −$6.12** — 2 full-collateral LOSSES (NYC ≥87-88,
  SF ≥72-73) vs 3 small-premium wins. This is the **settlement-source flaw** that killed
  weather on Kalshi, now live: our model uses forecasts/raw obs, PM settles on the official
  NWS Climatological Report; they diverge at the boundary buckets we sell. A morning "22/22
  won" read was STALE raw-obs settlement — `wx_settle_check` re-settled to PM's official
  outcome and flipped those buckets to losses. **Structural, not variance.** July 1 shorts
  ride to settlement regardless; recommend `WX_TAKER=off` for new shorts.
- **P&L tracker refined (this session):** `wx_settlement_pnl` now extracts the AUTHORITATIVE
  settled `realized` from the resolution (checks pr/ap realized·realizedPnl·pnl locations),
  strips the marketMetadata icon blob so the STRUCT logs in full, and marks cost-based
  fallback rows estimated; heartbeat carries `wx_settled_auth`/`wx_settled_est`. +4 tests
  (`tests/test_wx_settle_pnl.py`), 131 green.
- **Usage cuts (operator hits a daily paid-plan cap):** stubbed **`kalshi-mm/CLAUDE.md`**
  94KB→~1KB (retired project's ~1,700-line worklog was loading into EVERY session's context;
  full text in that repo's git history). Added the **Usage discipline** block above. Condensed
  the 06-20→06-22 migration/pilot incident entries.
- **Shadow-model deep dive → Option 1 shipped (promotion pipeline).** Status: MLB looks
  promising (Brier 0.241 vs market 0.301 on 66 rows; both buy AND fade sims positive — but
  market Brier > 0.25 means the morning `outcomePrices` prints are anti-informative/stale, so
  NOT executable evidence); soccer retired from consideration (market 0.119 beats model 0.168);
  tennis recorder went dark 06-27 (Wimbledon shape); golf settled 0 rows ever. Fixes shipped:
  (1) **`sport_settle_check`** — generalized `wx_settle_check`: re-settles sports rows against
  PM's authoritative resolution via `meta.pm_slug` (6h timer, capped reads); (2)
  **`odds_refresh_pass`** — near-kickoff EXECUTABLE odds: reads the matched game market's
  actual book, maps the single book to per-side bid/ask (`pmodds.executable_sides`: book
  prices outcome[0], other side = complement; self-verifying PROBE log vs outcomePrices),
  PATCHes rows once (`meta.odds_at`), keeps the morning print in `meta.snap_ask`; (3) **golf
  settlement root cause**: ESPN files tournaments under their START date but golf settles on
  END date — the ±1-day window missed every event; now `_golf_window` = end−6d..end+1d; (4)
  **tennis diag** `espnfeed.raw_shape` logs the slam's raw nesting once/day when 0 fixtures
  parse (fix follows from worker logs); (5) **`scripts/promotion_gate.py`** — the standing
  go-live rule: ≥100 PM-settled rows with executable odds AND model Brier < market AND
  threshold-sim positive at executable prices → eligible for a $50 armed-gated probe. MLB is
  first through the gate if it survives honest prices (~a week at ~15 games/day). 139 tests
  green (+8; golf-window test updated — it was asserting the bug).
- **MLB probe went live (Andrew: "merge and turn on mlb now" — Option 2 sizing, ahead of the
  gate).** `core/mlbtaker.py` + `mlb_taker_cycle`: buys the model-cheap side of matched game
  markets near kickoff at executable book prices (only rows stamped by `odds_refresh_pass`),
  `MLB_TAKER=live`/`MLB_BUDGET=50`/`MLB_EDGE=0.05`, probe-first (one 2-lot until a position
  confirms direction) then scale with $10/game cap. Rails mirror the weather taker: halt on
  wrong-direction (expected-sign per slug) / over-exposure / 3x never-rested; **stale-order
  sweep cancels our resting game orders the moment kickoff passes** (never rest in-play),
  runs even when tripped, on a fast 10-min timer. Order semantics reuse the two LIVE-PROVEN
  paths only: rest post-only `BUY_LONG` inside the spread to long the book side; rest
  post-only `BUY_SHORT` at bid+0.01 to fade it (no SELL_* intents — the inverted-intent
  trap). Independent accounting: `aec-mlb` excluded from the cricket breaker + cancel-all,
  like `tc-temp`. Heartbeat: `mlb_taker`/`mlb_tripped`. 145 tests green (+6).
- **Breaker wedge (fixed live):** after merging Option C, the worker tripped every cycle on
  a **400-lot golf position** (`tec-pga-travcham-2026-06-28-w-wyncla`, Andrew's OWN manual
  bet, not the bot) — 8× the 50 inventory cap → stood the whole bot aside. Added it to
  `POLY_DENY_SLUGS` (excludes from breaker, like the old 332-WC future) + fresh deploy
  (env needs a deploy, not restart). Farm resumed: `5/11 mkts placed_ok=8 rej=0`. No bot
  bug — manual bet. Slug carries today's date; drop from deny after it settles.
- **Option C (shipped):** `track` loop now runs the read-only tracker passes (wx/soccer/
  sports/golf/settle) in BOTH live and track modes — the cricket reward farm and the
  validation-week model tracking run on ONE worker. Fixed a double `time.sleep`.
- **PM market-odds capture (shipped + VERIFIED WORKING):** measures model-vs-MARKET edge,
  not just calibration. `core/espnfeed` captures team `abbreviation`s; `core/pmodds`
  matches a game to its PM market by team-token + date (abbrev/prefix aware via `ABBR_ALIAS`,
  e.g. ESPN `chw`→PM `cws`, `ari`→`az`; BOTH teams required). Diagnostic-first loop, all
  read-only, settled the format from worker logs:
  - per-game markets are `aec-mlb-<away>-<home>-<DATE>` (the same `aec` moneyline prefix as
    the cricket farm); date matched within ±1 day (ESPN UTC vs PM ET off-by-one).
  - **catalog must be fetched in full** — markets are created day-of and sort late; the
    40-page/4000 cap silently truncated today's games (now `max_pages=150`, ~7700 markets).
  - **price source:** each game market carries parallel `outcomes` (team names) +
    `outcomePrices` arrays (NOT binary YES/NO). Map each single-team outcome to home/away,
    read its price directly — no book read / YES-side guessing. `market_ask` = that side's
    implied prob; **`edge = model_prob − implied` per row**.
  - **Live result:** `odds: matched 37/51` MLB games, per-side prices sum ≈1.0, favorites
    sensible (e.g. NYY home 0.575 vs DET 0.43). Misses = games 2-3 days out not yet listed
    (match as game day nears). Today's rows were already recorded null this AM (idempotent
    writer won't overwrite) → odds/edge populate from the next daily snapshot. 117 tests green.

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

### 2026-06-20 → 06-22 — Migration + first pilot (condensed)
- **Migration (Phase 1):** assembled the Polymarket keeper tree (`poly_runner.py`,
  `core/polyclient.py`, `core/polymaker.py`, `scripts/poly_scan.py`, poly tests,
  `lib/fairvalue.py`). Dropped the two closed-thesis convergence scanners.
- **Render cutover (done):** the live worker `polymarket-mm` (`srv-d8kmtfrtqb8s73eg6tu0`,
  `python poly_runner.py`) is the repurposed old `kalshi-bot-hourly`; 4 dead Kalshi
  workers deleted; builds from `prediction_mm@main`, `autoDeploy=on commit`.
- **Supabase** project `pecafqwbfveovymyjako` ("Andrew's Project"). 4 tables have RLS
  disabled (market_snapshots, bot_config, fix_requests, app_users).
- **Breaker `netPosition` fix:** `positions_net` reads `netPosition` first (qtyBought−
  qtySold fallback) so held positions trip the inventory cap. `scripts/cancel_all_live.py`
  = risk-reducing-only live cancel (operator runs it; this env is geo-blocked from PM).
- **`POLY_DENY_SLUGS`:** denied slugs excluded from selection AND the inventory breaker,
  so we keep the held 332-lot WC future without standing the bot down.
- **First $150 pilot went live then SUSPENDED:** post-only quotes 200-ACCEPTED but
  **never rested** (`placed_ok=2 rej=0` yet 0 resting) → $0 at risk, worker set `off`.
  Root fix came later (see weather era: on this venue the SELL/BUY_SHORT intents are
  inverted and takers get killed; resting requires post_only=True + BUY_SHORT to short).
- **Gotchas that still bite:** (a) Render env changes need a fresh **deploy** (`POST
  /deploys`) — a `restart` reuses the OLD env snapshot; (b) `placed_ok` (HTTP 200) ≠ a
  resting order — verify resting via a read-back / the UI. PM credits reward earnings
  ~5+2 business days after a period ends (uncompressible); macro pools ~$10k/day pro-rata.
- **Left to do manually** (blocked here): archive `kalshi-mm` + delete its 8 dead
  `claude/*` + `feature/*` branches (keep `main`).
