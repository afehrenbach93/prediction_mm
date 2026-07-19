# Build review — CLOB liquidity-reward stack

**PR:** https://github.com/afehrenbach93/prediction_mm/pull/111  
**Branch:** `cursor/reward-capture-gaps-eeb7`  
**Base:** `claude/prediction-mm-migration-p1-wja5v5`  
**As of:** 2026-07-19  

This document is a review packet for everything built in this PR. Source plan: the 2026-07-19 deep-dive (Polymarket global CLOB reward-yield scan → deployment sequence).

---

## 1. Decision recorded in code

| Item | Fact |
|------|------|
| Active venue | Global Polymarket CLOB — `https://clob.polymarket.com` |
| Parked venue | Polymarket US (`api.polymarket.us` / `poly_runner.py`) — no proven edge |
| Default trade mode | `CLOB_MODE=shadow` — no orders hit the exchange |
| Score model | Official quadratic LP rewards (docs-reconciled) |
| Edge status | Gross opportunity measurable; net edge **not** yet proven (needs micro-pilot) |

---

## 2. Deep-dive plan → what was built

| Deep-dive step | Deliverable | Status |
|----------------|-------------|--------|
| §7.1 Stability study / regular scans | `scripts/clob_pulse.py` + GH Actions 00:00 & 15:00 UTC + Render cron stubs | Built |
| §7.2 Docs reconciliation | `core/clobscore.py` — `S=((v−s)/v)²`, size-cutoff mid, Q_min, c=3 | Built |
| §7.3 Eligibility + wallet + L2 keys | `.env.example` CLOB_* + `scripts/clob_derive_keys.py` | Scaffolded (ops confirm access/funding) |
| §7.4 Quoting bot | `clob_runner.py` + `core/clobtrader.py` (py-clob-client-v2, shadow-gated) | Built |
| §7.5 Micro-pilot defaults | $75 × 3 markets, ≥7d to end, near-zero excluded | Built |
| §7.6 Scale gate | `scripts/clob_scale_gate.py` — net/est_gross > 0.5 over ≥14d | Built |
| Gap §6.7 Accounting | `core/clob_ledger.py` — `rewards.csv` ≠ `fills.csv` | Built |

---

## 3. Architecture

```
clob.polymarket.com
        │
        ├─ sampling-markets (paginated) ──► clob_yield_scan / clob_pulse
        │                                      │
        │                                      ├─ data/clob_scans/YYYY-MM-DD.csv
        │                                      ├─ data/clob_scans/latest.csv
        │                                      ├─ data/clob_scans/pulse.json|md
        │                                      └─ clob_stability ──► pilot_universe.csv
        │
        └─ book?token_id=… ──► score + (shadow|live) quotes
                                      │
                                      clob_runner
                                      ├─ ClobTrader (shadow gate)
                                      ├─ clobmaker (bid/ask at spread fraction)
                                      └─ clob_ledger (quotes / fills / rewards / pnl)
```

---

## 4. File inventory (this PR)

### Active CLOB path

| Path | Role |
|------|------|
| `scripts/clob_pulse.py` | **Entrypoint for regular pulse:** scan + stability + summary |
| `scripts/clob_yield_scan.py` | Pull sampling-markets, score top-N books, write CSVs |
| `scripts/clob_stability.py` | Multi-day persistent-yield filter → `pilot_universe.csv` |
| `scripts/clob_derive_keys.py` | L1 private key → L2 API key/secret/passphrase |
| `scripts/clob_scale_gate.py` | Scale-up gate from `pnl_daily.csv` |
| `clob_runner.py` | Quoting worker (shadow/live/off) |
| `core/clobclient.py` | Read-only HTTP (sampling-markets, book) |
| `core/clobscore.py` | Quadratic score + capture estimate |
| `core/clobmaker.py` | Pure quote prices/sizes |
| `core/clobtrader.py` | Shadow-gated wrapper over `py-clob-client-v2` |
| `core/clob_ledger.py` | Quotes / fills / rewards / daily pnl logs |
| `.github/workflows/clob-pulse.yml` | Scheduled pulse 00:00 + 15:00 UTC; commits CSVs |
| `render.yaml` | Cron stubs + `clob-mm` worker (shadow) |
| `data/clob_scans/*` | Snapshot CSVs + `pulse.json` / `pulse.md` / pilot universe |
| `requirements.txt` | Adds `py-clob-client-v2>=1.1.0` |

### Tests added

| Path | Covers |
|------|--------|
| `tests/test_clobscore.py` | Weight, adjusted mid, Q_min, capture |
| `tests/test_clobmaker.py` | Quote prices/sizes/inventory |
| `tests/test_clobtrader_shadow.py` | Shadow place/cancel never live |
| `tests/test_clob_stability.py` | Persistent competed filter |

### Parked / incidental (US — not the thesis)

| Path | Note |
|------|------|
| `poly_runner.py` | US worker; hardened (netPosition, deny-list, selection) but parked |
| `core/polyclient.py`, `polymaker.py`, `rewardscore.py`, `ledger.py` | US stack |
| `scripts/poly_scan.py`, `poly_cancel_all.py` | US scan / leftover cancel helper |

---

## 5. Scoring formula (docs-reconciled)

Source: https://docs.polymarket.com/market-makers/liquidity-rewards  

```
S(v, s) = ((v - s) / v)^2 * b
```

- `v` = `rewards.max_spread` from API (**cents**, e.g. 4.5 → 4.5¢)
- `s` = distance from **size-cutoff-adjusted** midpoint (levels with size < `min_size` ignored for mid)
- `Q_one` / `Q_two` = Σ S × size per side  
- `Q_min` = `max(min(Q1,Q2), max(Q1,Q2)/c)` when mid ∈ [0.10, 0.90], else `min(Q1,Q2)`  
- `c = 3.0`  
- Capture estimate: `est_daily = daily_rate × my_score / (my_score + book_score)`  
- Hypothetical quote: budget split two-sided at **half** max_spread (weight 0.25)

---

## 6. Regular market pulse (domains)

**Domain scanned:** `https://clob.polymarket.com`  
Endpoints used: `/sampling-markets`, `/book?token_id=…`

| Scheduler | Cadence | Action |
|-----------|---------|--------|
| GitHub Actions `clob-pulse` | 00:00 UTC + 15:00 UTC | Run pulse; upload artifacts; commit CSVs |
| Render `clob-pulse` / `clob-pulse-morning` | same (Blueprint) | Needs dashboard/Blueprint apply |
| Manual | anytime | `PYTHONPATH=. python3 scripts/clob_pulse.py --budget 500 --top 250` |

Outputs under `data/clob_scans/`:

- `YYYY-MM-DD.csv` — daily snapshot rows  
- `latest.csv` — last full ranked set  
- `pilot_universe.csv` — competed markets passing persistence filter  
- `pulse.json` / `pulse.md` — headline metrics for review  

**Egress fact (this cloud agent):** unrestricted; CLOB reachable. No allowlist block observed.

---

## 7. Quoter behavior (`clob_runner.py`)

| Behavior | Default |
|----------|---------|
| Mode | `CLOB_MODE=shadow` |
| Markets | Top of `pilot_universe.csv`, max 3 |
| Size | `$75` notional per market (both sides) |
| Quote distance | `0.5 × max_spread` from mid |
| Min time to end | 168 hours (7 days) |
| Near-zero books | Excluded |
| Orders | Post-only GTC; cancel/replace each cycle |
| Inventory cap | 200 shares / market |
| Exposure cap | `1.5 × budget × markets` |
| Kill switch | `touch data/clob_logs/KILL` |
| Logs | `data/clob_logs/{quotes,fills,rewards,pnl_daily}.csv` |

Live path requires: `CLOB_PRIVATE_KEY`, L2 creds (`clob_derive_keys.py`), Polygon USDC, operator flip to `CLOB_MODE=live`.

---

## 8. Operator runbook

```bash
pip install -r requirements.txt
cp .env.example .env

# Pulse (also scheduled)
PYTHONPATH=. python3 scripts/clob_pulse.py --budget 500 --top 250

# Derive L2 keys (ops — needs wallet)
CLOB_PRIVATE_KEY=0x... PYTHONPATH=. python3 scripts/clob_derive_keys.py
# paste CLOB_API_KEY / CLOB_SECRET / CLOB_PASS_PHRASE / CLOB_FUNDER into .env

# Quoter — stays shadow until you flip
PYTHONPATH=. python3 clob_runner.py

# After pilot pnl rows exist
PYTHONPATH=. python3 scripts/clob_scale_gate.py --min-days 14 --threshold 0.5

# Tests
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

### Ops checklist (not done by this agent)

- [ ] Confirm US/FL access / ToS for polymarket.com  
- [ ] Fund Polygon wallet with USDC  
- [ ] Derive and store L2 credentials  
- [ ] Merge PR so GH Actions schedule runs on default branch  
- [ ] Apply Render Blueprint / create crons if using Render  
- [ ] Flip `CLOB_MODE=live` only for micro-pilot on competed markets  
- [ ] Raise `clob_stability --min-days` to 5–7 after enough daily CSVs  

---

## 9. Sample pulse (committed)

From `data/clob_scans/pulse.json` (run 2026-07-19, top-100 score pass in later pulse; earlier top-250 also on branch history):

- Domain: `clob.polymarket.com`  
- Gross yields on competed books remain large in snapshot terms  
- Near-zero markets flagged separately and **excluded from pilot**  
- See `data/clob_scans/pulse.md` for ranked tables  

**Interpretation constraint from the plan:** these numbers are **gross reward capture only**. Adverse selection / fill losses are not subtracted until the micro-pilot ledger has real fills + reward receipts.

---

## 10. Safety / invariants

1. `CLOB_MODE=shadow` → `ClobTrader` records intended orders; no `create_and_post_order` / cancel mutations. Covered by `tests/test_clobtrader_shadow.py`.  
2. Kill file stops quoting and cancels (live) / logs cancel (shadow).  
3. Pilot universe excludes near-zero competition by default.  
4. Scale gate blocks size increases unless `mean(net/est_gross) > 0.5` over ≥14 days.  
5. Secrets never committed (`.env` gitignored; Render `sync: false`).  

---

## 11. Commits on this branch (summary)

1. US methodology gaps (pre-pivot safety: netPosition, deny-list, US scan/ledger)  
2. Pivot: CLOB yield scan + score + client  
3. Full deep-dive: stability, quoter, derive keys, scale gate, ledger, Render stubs  
4. Regular pulses: `clob_pulse.py` + GH Actions schedule + persisted CSVs  

---

## 12. What is intentionally not built

| Item | Why |
|------|-----|
| Event-aware quote pulling on near-zero / news markets | Plan marks as advanced tier |
| Automated eligibility/ToS legal check | Ops |
| On-chain wallet funding automation | Ops |
| Guaranteed net edge | Requires live micro-pilot measurement |
| Polymarket US as income thesis | Closed as unproven |

---

## 13. Review questions for Andrew

1. Merge PR so twice-daily GH Actions pulse starts accumulating the 5–7 day stability series?  
2. Confirm FL/US access + wallet before any `CLOB_MODE=live`?  
3. Apply Render crons/worker, or rely on GitHub Actions + local/shadow runner only?  
4. Micro-pilot market shortlist: take top competed from `pilot_universe.csv` after ≥5 snapshot days, or hand-pick now?  

---

*End of build review packet.*
