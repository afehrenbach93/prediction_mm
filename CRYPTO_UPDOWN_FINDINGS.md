# Crypto Up/Down (pspspsps5 method) — Findings

**Date:** 2026-07-14 · **Status:** CLOSED — do not implement · **Cost:** $0 (read-only throughout)

Andrew asked to test, then implement, the crypto Up/Down strategy promoted by
`polymarket.com/@pspspsps5`, and — if his exact method couldn't be tested — to mirror
his actual trades. We tested **every leg** of the method and mirrored his live account.
**All legs fail, and the author's own account is a net loss.**

---

## 1. His method (as stated) and how each leg was tested

| # | Leg of his method | How we tested it | Result |
|---|---|---|---|
| 1–3 | Record open spot, judge direction/vol/time, compute own Up/Down prob | Coinbase spot anchored at market open; directional side = spot vs open ref | see leg 5 |
| 5 | Snipe the favorite near expiry | Paper snipe at **T−60s** and **T−4s**, graded vs the venue's real resolution | **No edge** |
| 4 | Complete-set arb: buy both sides when Up+Down < $1 | Scan near-expiry books each cycle for `up_ask + down_ask < $1` | **No arb exists** |
| — | (fallback) mirror his real trades | Resolve his wallet, pull his public trade history + official P&L | **He loses money** |

All markets are Polymarket's **5-minute** crypto Up/Down markets (`{btc,eth,sol,xrp,doge}-updown-5m-<ts>`),
observed read-only from the US Render worker (gamma `/events`, CLOB `/book`, Coinbase spot,
`data-api`/`lb-api`). The trading surface stays offshore/read-only — a US person cannot place
these orders; this only measures whether the edge exists.

---

## 2. Directional snipe — efficiently priced, no edge

Venue-graded (each snipe graded against the winning CLOB token settling to ~1):

| Timing | n | Win rate | Avg ask paid | Paper P&L |
|---|---|---|---|---|
| T−60s | 98 | 56.1% | 0.558 | +$0.27 (≈0) |
| T−4s (his "final seconds") | 56 | 39.3% | 0.383 | +$0.54 (≈0) |

**The decisive cut is calibration** across all 154 venue-graded snipes — realized win-rate
tracks the price paid at every level:

| Ask paid | n | Realized win-rate | Edge (win − ask) |
|---|---|---|---|
| ~0.07 | 44 | 0.068 | +0.002 |
| ~0.29 | 22 | 0.273 | −0.015 |
| ~0.50 | 17 | 0.471 | −0.028 |
| ~0.69 | 28 | 0.643 | −0.051 |
| ~0.91 | 43 | 0.977 | +0.069 |

The favored side's ask ≈ its true win probability at every level → nothing to skim.
Paper P&L ≈ +1¢/bet = noise. His final-seconds timing does not rescue it — the ask
reprices with the outcome. **Efficient at executable prices**, like every prior thesis.

### False positive we caught and killed (the important part)
The first version showed **~86% win / +$20 paper** — a mirage. Settlement was **self-graded**:
it compared spot to the *same reference the snipe used*, ~60–90s apart, so it measured
Coinbase autocorrelation, not the Polymarket resolution. The tell was the venue's own ask —
**0.50 on the "favorite" every time** (if the spot-favored side really won 86%, its ask would
be ~0.85). Fixed by grading against the venue's actual resolution; the 56 artifact rows were
relabeled `crypto-updown-shadow-selfgraded` and excluded. *(Lesson, nth time: reconcile
tape-derived P&L against ground truth.)*

---

## 3. Complete-set arb (his leg #4) — does not exist

Scanning the near-expiry window each cycle (`crypto-arb scan`): `up_ask + down_ask` **never
drops below $1** — `best_sum` floors at exactly **1.00** (observed range 1.00–1.07), `arb_hits=0`.
The spread always keeps the pair ≥ $1, so there is no risk-free complete set to buy.

---

## 4. Mirror of his actual account — he is a net loser

Resolved his handle to wallet **`0xb2445087e45f114436ee0d4d5edf76347d79edcf`** (display name
"capitalismd3") by scraping his public profile page's embedded `proxyWallet`, then pulled his
public activity and Polymarket's official leaderboard numbers.

**Ground truth (Polymarket's own `lb-api`):**

| Metric | Value |
|---|---|
| All-time profit | **−$175.91** |
| All-time volume | $5,625.74 |
| Markets traded | 64 |
| Current portfolio value | $0 (flat) |
| Last trade | 2026-06-06 (inactive ~5 weeks) |
| P&L time series (`user-pnl`) | negative throughout |

**What he actually does** (from his 91 mirrored trades, May 12 – Jun 6): buy a side **cheap
(0.05–0.19)** and **sell it into strength (0.86–0.98)** near expiry, or hold to settlement —
the *opposite* of the buy-the-favorite snipe. He also trades sports. His trade-level cashflow
(sells − buys) was −$139 (updown +$81, sports/other −$220), but that **omits settlement
redemptions** (not "TRADE" events), which is why the official −$176 is the number to trust.

---

## 5. Verdict

**Do not implement.** Every leg fails:
- **Directional snipe** — efficiently priced at T−60s and T−4s (no edge).
- **Complete-set arb** — doesn't exist (Up+Down never < $1).
- **The author's own account** — down ~$176 all-time on $5.6k volume, flat and inactive.

Consistent with every prior income thesis in this repo: these markets are efficient at
executable prices.

---

## 6. Where the data lives (Supabase `model_predictions`)

| `model` | Contents |
|---|---|
| `crypto-updown-shadow` | Venue-graded snipe rows; `meta.settle_src='venue'`, T−60s = `market_ask`/`realized_yes`/`pnl`, T−4s = `meta.fast_ask`/`fast_realized`/`fast_pnl` |
| `crypto-updown-shadow-selfgraded` | The 56 quarantined self-graded artifact rows |
| `crypto-updown-arb` | Complete-set arb crossings (`up_ask+down_ask<$1`) — none recorded |
| `pspspsps5-mirror` | His 91 mirrored trades (`market_slug = slug\|tx`, amounts in `meta`) |

Code: `crypto_shadow` + `mirror_pspspsps5` in `poly_runner.py` (env `CRYPTO_SHADOW`; flip off
to stop the harness). Shipped across PRs #82–#94. Read-only, $0 — no orders, no venue account.
