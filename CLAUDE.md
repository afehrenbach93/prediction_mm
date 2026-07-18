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
| `scripts/flow_paper_score.py` | Flow-scout lag + settlement paper score (informed-size thesis). |
| `scripts/arb_scan.py` | Same-venue complement/partition underround scan (paper). |
| `scripts/sweep_scout.py` | Near-certainty settlement-window paper scout (no source gate). |
| `core/arbscan.py` / `core/sweepscout.py` | Pure math for arb + sweep paper scouts. |
| `tests/` | `test_polyclient_shadow` (no-leak gate), `test_polymaker`, `test_poly_breaker`, `test_fairvalue`, arb/sweep unit tests. |

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

### 2026-07-18 — Arb/sweep hang: uncapped book crawl blocked track loop
After merging #108 + enabling env, `arb_scan` walked ~600 `get_book`s with no
budget and starved heartbeats (last_seen stuck). Disabled flags, restarted worker,
fixed via combined `edge_scouts` (max 100 books / 35s budget, rotate offset,
sweep meta only for hot asks). Re-enable only after that fix is live.

### 2026-07-18 — Master-plan triage → arb/sweep *instruments* (WATCH, not done)
External master plan mapped vs repo. Highest new promise: same-venue arb +
settlement sweep — but **scaffolding ≠ validated edge**. Shipped detectors +
paper-score CLIs + live baseline (2026-07-18Z): 503 books / 63 families →
partition p50 raw edge **−4¢** (normal overround); 1 plausible small hit + several
**SUSPECT** incomplete partitions (e.g. UFC champ +82¢ — not exhaustive). Sweep:
0 candidates in band (max ask on scanned set 0.95). Verdict both **WATCH**.
GO bars: arb ≥30 depth-backed hits + rules screen; sweep ≥50 settled @≥99%
*and* named-source gate. Unit tests only prove math helpers.

### 2026-07-18 — App Overview overhaul (yield / whale / flow ops view)
Dashboard was still World-Cup + Elo-model centric. Overview now shows worker hero
(mode, live_until, quote counts, pipeline chips), reward-pool & $/hr bar charts from
`detail.reward_yield`, whale + flow scout panels, and paper-row counts. Settings
Go Live copy → reward-maker pilot; research scouts listed as observe-only.

### 2026-07-18 — Flow scout (read-only): large tape prints as informed-flow proxy
New research thesis parallel to whale-scout: unusually large prints may proxy informed
money. Built `core/flowscout.py` + `poly_runner.flow_scout` (`FLOW_SCOUT=1`): polls
`data-api/trades`, per-slug size baseline, flags ≥mult× median. **Endgame is
duration-relative** (short ≤4h sports = whole live window; longer = last 50% clamped
[30m,6h]) — not a fixed 180m clock that would mishandle short events. Default records
all spikes + tags endgame; `ENDGAME_ONLY=1` for strict. Score:
`scripts/flow_paper_score.py`. GO ≥100 settled / PnL>0 / hit≥55%. $0 / .com observe-only.

### 2026-07-15 (later) — Whale scout (read-only): rank by official PROFIT, paper-copy trades
Parallel research thesis to the reward farm: can lagged copy of top wallets survive
latency? Built `core/whalescout.py` + `poly_runner.whale_scout` (env `WHALE_SCOUT=1`):
pulls `lb-api/profit` (never volume-rank), filters `min_profit`, round-robins activity
for top N, records `model='whale-scout'` rows with their fill + lagged CLOB `copy_ask` /
`lag_bps`. $0 / no orders / offshore .com observe-only (US person still can't place).
Heartbeat: `detail.whale_scout`. Does NOT interrupt the Liga MX economics pilot.

### 2026-07-15 — Economics pilot ARMED on Liga MX $2400 pools (24h window)
`max_pool` rose from $100 → **$2400** (`atc-lmx-*`). Bounded economics pilot started 19:56Z:
`POLY_ALLOW=lmx`, budget/size `$50`/`25`, `MAX_MARKETS=2`, `POLY_VOL_CAP=0.05`,
`desired_mode=live` until **2026-07-16T19:56Z**, takers off. Live-confirmed resting quotes on
`atc-lmx-asl-caz-2026-07-17-{asl,caz}` @25 (`placed_ok=4 rej=0`, readback 200,
`resting(pre-cancel)=2`). GO/KILL after 24–48h modeled-net vs adverse selection (see PILOT.md).
Balance heartbeat `$52.93` (was `$127.93` 07-14; open lots still 5 — not from this pilot).

### 2026-07-14 (later) — Stage 2: selection-first quoting (prefer low-adverse-selection pools)
`core.rewardyield.select_reward_markets` now drives which in-window reward markets `live_cycle`
quotes: rank by reward-rate-per-unit-volatility (`pool/period_hours ÷ rolling vol`), optional
hard-exclude above `POLY_VOL_CAP` (default 0 = off). Vol comes from the REWARD_YIELD sampler's
rolling history (`ryield_state.hist`), passed into `live_cycle(vol_by_slug=...)`. **Fallback is
byte-identical to the legacy top-by-pool when no vol data is present, so the live path can't
regress** (legacy `BOT_MODE=live` loop passes no vol → unchanged). Unmeasured markets are
neutral (never excluded). 208 tests green (+8). Economics test still ON HOLD (no fat pool;
`max_pool=$100`). Deferred Stage 2b: defensive quote skew (thin the side the book is thinning
toward). Tune `POLY_VOL_CAP` from the instrument once a representative pool appears.

### 2026-07-14 (later) — LIVE-RAILS TEST PASSED: reward-maker orders REST
Ran the bounded live-rails smoke test (PILOT.md) from the cloud agent. Flipped `poly_control`
`desired_mode=live` (budget $10, 15-min `live_until`) — worker placed post-only quotes on
`aec-czechligapro` reward markets: `executions:[]` + a **read-back `st=200` confirming the
order RESTS**, next cycle `resting(pre-cancel)=3` (rest across cycles). The pre-migration
"orders 200-ACK but never rest" failure is GONE. `netPosition` read verified live
(`open_contracts=5` on a pre-existing `arankc-atp` position). Reverted to track;
`left live -> cancelled 3 resting orders`. **Balance unchanged $127.93** (no fills/loss).
Remaining rail (breaker trips on a real fill) unexercised — no fill; unit-tested. Also cleaned
a stale control row (was `desired_mode=live` + expired `live_until` 07-11 → per-cycle "window
expired" spam; set to `track`). Tooling note: drove this via the **direct Render API +
Supabase** using the VM's valid `RENDER_API_KEY`; the Render MCP plugin connected but its
`${RENDER_API_KEY}` header didn't resolve (cloud HTTP MCP stores the header on Cursor's
backend, not VM-env-interpolated) → paste the literal key to fix the MCP.

### 2026-07-14 — Reward-yield instrument (Stage 1): reframe farm as subsidy-carry, not edge
Every edge thesis has closed "efficient at executable prices" (incl. crypto Up/Down). The
only structurally +EV mechanism left is the exchange **subsidy** (reward pool + maker
rebate); its funding cost is **adverse selection**, and both are dominated by *market
selection*, not quoting. Built the cheapest read on that (read-only, $0, PR #96):
`core/rewardyield.py` (pure, tested) ranks reward-eligible markets by modeled reward/hour
(pool + discount-weighted competing book score) **÷ realized volatility** (adverse-selection
proxy). `scripts/reward_yield.py` = manual sweep; `poly_runner.reward_yield_scan` wires the
same ranking into the track-mode heartbeat (`REWARD_YIELD=1`, slow 900s timer, brief vol
burst). **Live read (13:29Z, REWARD_YIELD=1 on the worker):** n=98 reward markets, but the
only fat-ranked pools were tiny ($100) Czech-Liga-Pro table-tennis `live` markets and the
20s vol burst read 0.0 across the top — too short to measure adverse selection, so rank
collapsed to reward/hr ordering (steers toward in-play, the WORST case). **Fix (Stage 1.5,
shipped same day):** replaced the burst with a per-cycle rolling mid sampler
(`reward_yield_sample`, ~30s cadence, ~40-min window) so vol reflects real multi-minute
movement; heartbeat now carries top-10-by-rank + fattest-5-by-pool + max_pool + a `warming`
flag until enough samples accrue. 200 tests green (+27). **Live sweep runs on the Render
worker only** (sandbox geo-blocked). Next: Stage 2 selection-first quoting; Stage 3 same-day
modeled-net readout w/ pre-registered GO/KILL. Can KILL the venue thesis for $0 if no market
clears yield-vs-adverse-selection. Small bounded live test (PILOT.md) is the read-only-can't-
answer backstop, gated on the fixed instrument surfacing a representative (not $100 in-play) pool.
**Live surface as of 14:14Z: all $100 in-play table-tennis pools (`max_pool=$100`), top vol≈0
(static/inactive books) — no fat pool to farm yet; instrument watches `max_pool`.** Decided
(operator+agent): while waiting for a fat pool, run a **live-rails smoke test** (order rests?
breaker reads `netPosition`?) — protocol in PILOT.md. Needs operator arming + **Render MCP wired
into a FRESH cloud-agent run** (cloud MCP is dashboard-configured at cursor.com/agents, not repo
`.mcp.json`; Render MCP = Bearer API key, HTTP; a running agent won't hot-load it).

### 2026-07-13 — Crypto Up/Down late-snipe PAPER harness (pspspsps5 method); LIVE data confirmed; open timing bug
Andrew asked to test then implement pspspsps5's crypto Up/Down method (record open-spot →
buy the spot-favored side → snipe the favorite in the final seconds). Built a **read-only,
$0, no-venue-account** paper harness (`crypto_shadow` in `poly_runner.py`, `CRYPTO_SHADOW=1`;
paper rows in `model_predictions` as `model='crypto-updown-shadow'` via `track.fetch_open_crypto`/
`set_snipe`/`mark_settled`). It pulls Coinbase spot + gamma `/events` + CLOB `/book` — all
reachable from the US Render worker (sandbox itself is egress-blocked, so all diagnosis is
worker-log driven). **The trading surface stays offshore/read-only — we never place a
polymarket.com order as a US person; this only measures whether the edge survives our polling
latency before anything else.**
- **Fetch fix (shipped):** `/events?closed=false&order=endDate&ascending=true` surfaced
  ancient never-closed **zombie** updown-5m markets (e.g. `eth-updown-5m-1766161800` → Dec 19
  2025, ~205d past; `prices=None`, `bestAsk=1/bestBid=0` placeholders) and buried the live ones
  past `limit=100`. Added `end_date_min={now}` → live markets surface.
- **LIVE DATA CONFIRMED (23:41Z):** `updown=48/cycle`, real 5-min markets across
  **btc/eth/sol/xrp/doge**, `endDate` = today +minutes. So the paper test IS feasible from our
  infra — this is NOT a wall.
- **Timing bug (fixed, #84):** slug ts (`...-1783986000` = 23:40) is the market **OPEN**;
  resolution is `endDate` = open+5min. Anchoring `resolve_ts` on the slug ts fired the windows
  5 min early → 0 rows. Now `resolve_ts = endDate` (fallback slug+300); rows record.
- **FALSE POSITIVE caught + killed (#85) — the important part.** First rows showed ~86% win /
  +$20 paper, avg ask 0.50. It was an **artifact**: settle graded `outcome == (spot vs the same
  ref the snipe used)`, ~60-90s apart → it measured Coinbase autocorrelation, not the PM
  resolution; losses clustered where `spot_move≈0`. The tell was the venue's own ask — **0.50 on
  the "favorite" every time**, i.e. the market prices these a coin flip near expiry (if the
  spot-favored side really won 86%, its ask would be ~0.85). Same "reconcile tape P&L vs ground
  truth" lesson as Kalshi/weather. Fix: settlement pass **decoupled from the end_date_min feed**
  (resolved markets drop out of it), grading each snipe against the **venue** outcome (winning
  CLOB token settles ~1; up-tok best-ask→0.001 = down won, confirmed), spot-vs-open only as a
  logged fallback. The 56 self-graded rows relabeled `model='crypto-updown-shadow-selfgraded'`
  so the app's honest view = venue-graded only.
- **Final-seconds sampler (built, #87):** to test his ACTUAL edge (last seconds, not T−60s),
  the harness now also snipes at **~T−4s** — after the main pass it sleeps to ~4s before the
  soonest close, re-reads spot + CLOB ask, and stamps `fast_side`/`fast_ask` onto the same row
  (`track.patch_meta`); settlement grades it against the SAME venue outcome (`fast_realized`/
  `fast_pnl`). So each market yields a clean **T−60s vs T−4s** venue-graded pair, no dup rows.
  Blocks the worker ≤~41s once per 5-min window (fine in track mode; farm is off).
- **DIRECTIONAL SNIPE — no edge (n past gate).** Venue-graded: T−60s n=98 win 56.1% @ ask
  0.558 (+$0.27≈0); T−4s n=56 win 39.3% @ ask 0.383 (+$0.54≈0). Decisive = **calibration**
  across all 154 snipes: realized win-rate tracks the ask paid at every level (0.07→0.07,
  0.29→0.27, 0.50→0.47, 0.69→0.64, 0.91→0.98; edge within ±0.05, no sign). Favored side's ask ≈
  its true win prob → nothing to skim; ≈+1¢/bet = noise. His final-seconds timing doesn't rescue
  it (the ask reprices with the outcome). Efficient at executable prices, like every prior thesis.
- **Testing the REST of his theory (Andrew: test the ENTIRE method, or mirror his account).**
  Two read-only $0 additions shipping now: (1) **complete-set arb (leg #4)** —
  `crypto-updown-arb`: scan the near-expiry window each cycle; when `up_ask+down_ask<$1`, buying
  both locks a risk-free `1−sum` at resolution. Records crossings + logs the tightest sum.
  (2) **mirror pspspsps5** — `mirror_pspspsps5`/`pspspsps5-mirror`: resolve his proxy wallet from
  the handle (discovery across candidate endpoints, wired from worker logs), then record his
  public TRADE activity + a value/PnL snapshot from `data-api.polymarket.com` — the most direct
  test (his ACTUAL results). Observe-only; a US person still can't place these offshore orders.
- **FULL-THEORY VERDICT (2026-07-14) — CLOSED. His entire method loses money; his own account is down.**
  (a) **Directional snipe:** efficiently priced (above). (b) **Complete-set arb (#4):** does NOT
  exist — `crypto-arb scan` shows `up_ask+down_ask` never < $1 (best_sum floors at 1.00). (c)
  **Mirror of his ACTUAL account** (`pspspsps5` = wallet `0xb244…edcf`, display "capitalismd3";
  resolved by scraping the profile page's `proxyWallet`): his real trades show buy-cheap
  (0.05–0.19) / sell-into-strength (0.86–0.98) or hold-to-settlement — NOT the buy-the-favorite
  snipe. **Polymarket's own leaderboard API is the ground truth: all-time profit `lb-api/profit`
  = −$175.91 on `lb-api/volume` = $5,626 across 64 markets; `user-pnl` series negative
  throughout; currently flat (value $0) + inactive since Jun 6.** The inventor is a net LOSER.
  So every leg of his theory fails: no directional edge, no arb, and the author's own realized
  results are negative. **Do not implement.** Same lesson, nth time: efficient at executable
  prices; reconcile tape vs ground truth (his official P&L, not our trade-cashflow guess of −$139
  which omitted settlement redemptions). Mirror feed (`pspspsps5-mirror`, 91 trades) + arb scan
  left recording read-only; flip `CRYPTO_SHADOW` off on the worker to stop the harness.

### 2026-07-07 — DECISION DAY (moved up from Jul 11 by Andrew): all betting theses closed; farm is the business
- **MLB gate: FAILED with an adequate sample.** On 181–187 PM-settled rows with EXECUTABLE
  book odds (gate minimum 100 met): `elo-mlb` Brier 0.2704 vs market 0.2493; `elo-mlb-ctx`
  0.2701 vs 0.2457; **`blend-mlb` 0.2567 vs 0.2531 — even the blend loses to the market**.
  Threshold sims: elo/ctx clearly negative; blend +$0.37/+$0.31 on ~25 bets = noise. Verdict:
  NO promotion, NO probe, MLB betting closed on all three models. Same lesson as Kalshi:
  these markets are efficient at our modeling level at executable prices.
- **Weather Tier-1 re-validation: NO EDGE.** 310 deep-bucket rows since the Jul-4 guards:
  sell-sim at ≥10¢ margin = −$0.65 over 76 sells (≈0 before fees); 13% of "deep" buckets
  still landed YES. Weather stays OFF — thesis closed a second time, now with guards on.
- **Reward credits: not landed yet** (pendingCredit 0, no REWARD activity types) — the venue's
  ~5+2-business-day lag can't be accelerated. The widened farm (aec+arankc+apdc+cranc, $100,
  auto-off Jul 11 23:59Z) keeps earning periods on tennis-ranking/politics futures pools;
  quotes confirmed RESTING; historical adverse-selection bleed ≈ $0. **Scale/kill decision on
  the farm happens when the first credit posts** — visible in balance/Recap.
- **Ledger:** balance $127.93 fully liquid (open lots settled back). Whole validation campaign
  cost ≈ $72 of the $200 start: MLB ≈ −$60, weather ≈ −$13, farm ≈ −$0.2. State while Andrew
  is away: farm quoting (auto-terminates Jul 11), all takers OFF, models still recording,
  Recap self-serve. Standing next actions: read first reward credit → farm verdict; keep
  betting closed unless a model beats the market on executable prices with n≥100.

### 2026-07-04 (later) — Self-serve Daily Recap tab + golf settle fixed (order field) + daily snapshots
- **Golf settlement FIXED (real fix):** worker SHAPE diag proved ESPN golf competitors carry
  NO `status` object — the finish rank is in `order` (order=1=winner). `golffeed._position`
  now reads `order`; the 155-row backlog settled on the first pass (`resolved=155`).
- **Daily Recap (app, self-serve so Andrew stops asking):** new **Recap** tab — yesterday's
  account P&L (day-over-day `poly_daily.balance` delta), balance/buying-power/open-lots,
  cumulative per-strategy settled P&L, a per-model scorecard for yesterday (settled/hit-rate/
  Brier/paper-P&L from `model_predictions`), and a last-8-days trend. Worker: `pnl_snapshot`
  now returns venue `balance`/`buying_power`; the pnl cycle upserts `poly_daily` (migration,
  applied) via `track.record_daily`. Balance = ground truth; day-over-day fills in as snapshots
  accumulate. App typechecks + builds.

### 2026-07-04 — Weather Tier-1 (settlement-flaw guards) + MLB settled-P&L + tennis/golf settle fixes
- **Weather Tier-1 (attacks the settlement-source flaw, not raw skill):** (1) **intraday
  conditioning** — `wxfeed.intraday_max_so_far` reads today's hourly obs; `bucket_probability(...,
  floor=)` truncates+renormalizes the daily-high distribution at the observed max-so-far (a
  bucket below it is impossible) and `wx_pass` shrinks sigma by √(day-fraction-left) → collapses
  boundary uncertainty by mid-afternoon; (2) **non-boundary guard** — `wxtaker.sell_candidates`
  now skips buckets with model prob > `max_prob` (0.15), i.e. only sells DEEP buckets far from the
  line (boundary buckets = where forecast-vs-official divergence caused the full-collateral losses).
  All pure + tested; strictly MORE conservative, so safe to run live.
- **MLB settled-P&L reader:** generalized `wx_settlement_pnl` → `_settlement_pnl(prefix,label)`;
  `mlb_settlement_pnl` sums `aec-mlb` resolutions (same authoritative after−before-delta logic).
  Heartbeat carries `mlb_settled_pnl`/`mlb_settled_n`; the app P&L card already reads them.
- **Tennis fix (root cause found in the data):** recorder was logging Wimbledon **TBD-vs-TBD**
  future-round placeholders (both default-1500, junk 0.5 preds that never settle). `espnfeed.
  upcoming_fixtures` now skips fixtures with a TBD competitor (`_is_tbd`).
- **Golf/tennis settle diagnostic:** when rows are due but 0 resolve, `settle_pass` logs the ESPN
  window's completed ids/winners vs the tourney_id/espn_id we want — surfaces the id mismatch from
  the worker's logs (sandbox is ESPN-blocked). 169 tests green (+8).

### 2026-07-02 (night, phase 2) — Full app control: strategy toggles, budgets, halt-clear
- **poly_control** gained `wx_taker`/`mlb_taker`/`wx_budget`/`mlb_budget`/`mlb_edge`/
  `clear_halts` (migration 0004, applied). `effective_config` (pure, tested): app value
  wins, NULL falls back to worker env — behavior identical until the app writes.
  Worker reads ctrl every cycle; `clear_halts` is one-shot per timestamp (boot baseline
  skipped) and un-trips wx/mlb/farm latches + probe counters on ALL accounts. Heartbeat
  carries effective `wx_on`/`mlb_on`/budgets/edge so the app renders REAL state.
  App Settings gained a **Strategies** card: per-strategy on/off + budget chips +
  HALTED badge + confirm-gated "Clear halts". Toggle-off stops NEW orders only (resting
  orders ride; per-user disarm still cancels). Env vars remain only as defaults + the
  master `POLY_LIVE_ARMED`.

### 2026-07-02 (night) — Self-serve onboarding (sealed-box keys); first MLB probe order
- **Hands-off onboarding (Andrew: no credential hand-offs, countless users):** users
  paste their PM keys into the app's My-trading card; `app/src/lib/seal.ts` encrypts them
  IN THE BROWSER to the deployment public key (ephemeral ECDH P-256 → HKDF-SHA256(salt=∅,
  info="poly-keyring") → AES-256-GCM; wire = eph_pub(65)‖iv(12)‖ct, byte-compatible with
  `core/keyring.py`). Ciphertext lands in `poly_users.pm_key_enc/pm_secret_enc` (migration
  0003, applied); only the worker (`POLY_KEYRING_PRIV`) unseals in `refresh_accounts`
  (cached; env-linked keys remain the operator fallback). Keypair via
  `scripts/keyring_gen.py`; this deployment's pair installed on Render (worker priv, app
  pub) — private key never printed/committed. **Also closed a hijack hole:** column-level
  `revoke update/insert (key_env, secret_env)` from anon+authenticated — a self-updating
  user could otherwise point their row at the OPERATOR's env keys.
- **First MLB probe order (live-verified):** 20:34Z `BUY_SHORT aec-mlb-cws-cle-2026-07-02
  2@0.50 edge=+0.21` → 200, RESTING (heartbeat `resting 1, 0pos`). Why so quiet earlier:
  (a) pre-game books empty until ~2h before first pitch (one-sided fix works — CWS@CLE
  stamped once the book formed); (b) 3 games recorded 16:37Z still carry YESTERDAY'S
  resolved market slug (rows predate the exact-date matcher fix; idempotent writer won't
  overwrite → those games unbettable today, self-heals tomorrow); (c) by design: probe =
  ONE 2-lot until a fill confirms direction, and in-play games are never touched.

### 2026-07-02 (later) — Multi-user execution (one brain, N accounts) + 4 model improvements
- **Multi-user execution shipped (Andrew's direction: single shared bot, per-user
  accounts).** `poly_users` table (migration 0002, applied): email-keyed rows carrying
  `key_env`/`secret_env` (worker env-var NAMES — secrets never in the DB) + `armed`.
  **A user's `armed=false` is THEIR kill switch: it only stops orders reaching THEIR
  Polymarket account** (client flips to shadow + resting bot orders on tc-temp/aec-
  prefixes cancelled); the shared models/worker never stop. Worker: `TradeAccount` +
  `refresh_accounts` (60s sync; falls back to base env single-account when the table is
  unreadable), wx/mlb/farm cycles fan out per account with per-account state/breakers.
  RLS: read public; self-insert disarmed w/o env links; self-update own row only. App
  Settings gained a **My trading** register/arm/off card; heartbeat carries `users`.
  Operator flow for a new user: they register + send keys privately → add env vars →
  link env names on their row.
- **4 model improvements (validate-first — live betting stays on `elo-mlb`; the gate
  promotes variants only if they beat it):** (1) **starting pitchers** captured from
  ESPN probables into ctx-row meta (per-pitcher model once data accumulates);
  (2) **`elo-mlb-ctx`** tracked variant = Elo + rest-days logit adjustment (k=0.05/day,
  ±3d cap), pitchers+rest in meta; (3) **`blend-mlb`** tracked model = 0.30·model +
  0.70·executable ask, recorded at odds-refresh with exec prices (BLEND_W env);
  (4) **execution learning**: `fill_drift` (kickoff mid − entry, signed) stamped onto
  the bet row's meta = the adverse-selection measure, so the gate can score edge AFTER
  execution. `mlbtaker.candidates` hard-filters to model `elo-mlb` so variant/blend
  rows can never reach the order path. `settle_pass` settles `blend-*`. 155 tests green (+6).

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
- **wx-pnl zero-latch fix (post-deploy, from the live STRUCT):** venue `realized` is
  CUMULATIVE per position (bp.realized prints 0.0000 pre-resolution) — the extractor latched
  that zero and flattened all 8 rows to $0.00. Now: settled = ap−bp delta; flat-zero
  candidates rejected; and the computed path is AUTHORITATIVE (STRUCT confirmed bp.cost =
  collateral: qty 5 × avgPx 0.63 = 3.15) using venue cost + resolved outcome. STRUCT now
  logs pr/before/after on separate lines (Render truncates ~930 chars). 147 tests green.
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
