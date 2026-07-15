# External Repo Scan — what (if anything) to reuse

**Date:** 2026-07-14 · **Ask:** Andrew shared 8 similar repos — assess whether any logic is
worth adopting. All 8 fetched/verified. **Bottom line: one direct hit (`lihanyu81/polymarket_lp_tool`),
two partial references; the rest target directional edges we've already proven efficient.**

Our situation frames the filter: every *directional* thesis here is closed ("efficient at
executable prices" — weather, sports, MLB, crypto Up/Down, arb). The only structurally +EV
mechanism left is the **exchange subsidy** (reward pool + maker rebate), whose funding cost is
**adverse selection**. So the only external logic worth taking is logic that improves
**reward-eligible market selection** or **reduces adverse selection while quoting** — i.e. it
must help `core/rewardyield.py` + `core/polymaker.py` + `live_cycle`, not add another
directional bot.

---

## Ranked verdict

| Repo | Stack | What it is | Verdict for us |
|------|-------|-----------|----------------|
| **`lihanyu81/polymarket_lp_tool`** | Python + Rust | **Passive LP for Polymarket liquidity rewards** — δ-band quoting, tick-aware repricing, anti-sniping | ⭐ **Adopt (patterns)** — same business as ours |
| `HarrierOnChain/…-Toolkits` | Rust | 10-strategy engine incl. MM w/ inventory skew, spread farming, depth guard, circuit breaker | ◐ **Reference** — quoting-loop hardening patterns |
| `ent0n29/polybot` | Java + Python | MM + **complete-set arb** + **strategy replication/similarity scoring** | ◐ **Reference** — replication-scoring idea for the mirror |
| `aarora4/Awesome-Prediction-Market-Tools` | (list) | Directory of 140+ tools/APIs | ◐ **Reference** — unified data APIs (Dome, PolyRouter), analytics (Polysights) |
| `alsk1992/CloddsBot` | TypeScript | 118-strategy AI agent, round-based crypto, copy-trade, MM w/ liquidity scoring | ✕ kitchen-sink directional; round discovery we already do |
| `MrFadiAi/Polymarket-bot` | TypeScript | Arb (YES+NO<$1), DipArb, copy-trade, direct | ✕ arb doesn't exist (verified); rest directional |
| `AruneshDev/…-Kalshi-Weather-Model` | Python | XGBoost daily-high → Kalshi weather trades | ✕ weather closed twice; killer was settlement source, not model skill |
| `yangyuan-zhen/*` (PolyWeather, polysniper, PolyMusic, PolyElection) | — | Weather/NBA/music/election Polymarket signals | ✕ directional; weather+NBA are closed theses |

---

## ⭐ The one to adopt: `lihanyu81/polymarket_lp_tool`

Same business we're in — passive LP that keeps quotes inside the reward band and defends
against being picked off. Its logic maps directly onto files we already have:

| Their concept | Our file | Note |
|---|---|---|
| **δ-band quoting** — pull reward half-width δ from the CLOB rewards program; keep quotes in `[mid−δ, mid+δ]` | `core/polymaker.py` / `program_active` | We gate on reward eligibility; δ-band would make the *price* eligibility explicit |
| **Tick-aware repricing** (0.01 vs 0.001) — classify tick, reprice per class | `polymaker` (Invariant: "futures tick 0.001") | This is literally the gotcha that broke our post-only resting; codify it |
| **Depth-aware placement** — scan band depth, pick a level; if ≤2 levels, cancel (thin book too risky) | `core/rewardyield.py` selection | Complements our reward/vol ranking with book-depth quality |
| **Anti-sniping** — midpoint-jump detection, EMA smoothing, post-fill cooldown, max-chase limits | **deferred Stage 2b (defensive quote skew)** | **This is the payoff** — direct, concrete adverse-selection defense |
| **Inventory skip** — stand aside on a token once holding a position | breaker / `live_cycle` | Quote-level version of our inventory breaker |

**Highest-value piece = anti-sniping.** Adverse selection is *the* unresolved risk in the
reward-maker thesis, and it's exactly what the deferred **Stage 2b (defensive quote skew)** is
meant to attack. Their midpoint-jump + EMA + cooldown + max-chase set is a ready-made blueprint
for Stage 2b: don't chase a moving mid, pull quotes when the mid jumps, cool down after a fill.

---

## Partial references (borrow patterns, not code)

- **`HarrierOnChain`** (Rust): its **depth guard** (validate book liquidity pre-quote) and
  **circuit breaker** (halt on consecutive large adverse trades) are cleanly factored — good
  shape for hardening our quote loop / breaker. Its "spread farming" ≈ our reward farm.
- **`ent0n29/polybot`** (Java): a **replication / similarity-scoring** pipeline — quantify how
  faithfully you can copy a wallet. A rigorous upgrade to the `pspspsps5-mirror` we built, *if*
  we ever find a wallet that's actually a proven winner (pspspsps5 was −$176). Also has a
  paper/live mode abstraction like our shadow gate.
- **`aarora4/Awesome-…`**: reference only — points at unified data APIs (Dome, PolyRouter) and
  analytics (Polysights) that could replace hand-rolled feeds if we ever need more coverage.

---

## Not useful (and why — so we don't re-litigate)

- **Weather models** (`AruneshDev` XGBoost, `yangyuan-zhen/PolyWeather`): weather closed twice.
  The binding constraint was the **settlement source** (official NWS climate report vs
  forecast/raw obs) and market efficiency at the boundary — a better daily-high model doesn't
  fix that.
- **Directional / copy / dip bots** (`CloddsBot`, `MrFadiAi`, `polysniper`): edges in markets
  we've shown are efficiently priced. The `YES+NO<$1` arb specifically **does not exist** on the
  Up/Down books (verified: `up_ask+down_ask` floors at 1.00).

---

## Recommendation

Only one build is worth it, and only on the right trigger: **port `lihanyu81`'s anti-sniping
(midpoint-jump / EMA / cooldown / max-chase) + explicit δ-band quoting into `core/polymaker.py`
as Stage 2b.** But **validate-first** — hold until the first reward credit confirms the farm's
net economics are positive (the standing gate). Optimizing quote defense before we know the
subsidy pays is premature. Everything else here is reference material, captured above so we
don't have to re-read these repos.
