# Build review — CLOB liquidity-reward stack

**Share this file:** https://github.com/afehrenbach93/prediction_mm/blob/claude/prediction-mm-migration-p1-wja5v5/BUILD_REVIEW.md  

**Raw (plain text copy):** https://raw.githubusercontent.com/afehrenbach93/prediction_mm/claude/prediction-mm-migration-p1-wja5v5/BUILD_REVIEW.md  

**PR (merged):** https://github.com/afehrenbach93/prediction_mm/pull/111  
**As of:** 2026-07-19  

Source plan: deep-dive “Polymarket Market Making + Incentive Capture” (global CLOB reward-yield → deployment sequence).

### Handback fixes (post-review) — do not go live until P0 verified
| # | Item | Status in code |
|---|------|----------------|
| P0.1 | `ELIGIBILITY_CONFIRMED` hard gate | `core/eligibility.py` + `ClobTrader` + tests |
| P0.2 | Supabase ledger + remote kill | `sql/0002_clob_ledger.sql`, `clob_ledger`, `clob_control` |
| P0.3 | Pulse ≠ deploy restart | GH Actions → `data` branch only; `render.yaml` notes |
| P1.4 | Shadow-fill simulator | `core/clob_shadowfills.py` |
| P1.5 | Reward recon (actual vs est) | `scripts/clob_reward_recon.py` (wired in pulse) |
| P1.6 | Websocket mids | `core/clob_bookws.py` + REST fallback |
| P2 | Footgun tests, provisional pilot, wallet docs | done |

**Ops before live:** apply SQL in Supabase; set `SUPABASE_*` on Render; confirm Auto-Deploy is not on `data` branch; keep `CLOB_MODE=shadow` until eligibility confirmed.

---

## What we built (process → deliverables)

### Phase 0 — Diagnosis
- Compared the deep-dive (global CLOB / `clob.polymarket.com` / quadratic rewards) to the existing Polymarket **US** worker.
- Fact: US path had no proven edge after prior scan/test; CLOB had measurable **gross** reward opportunity.
- Decision: **pivot thesis to global CLOB**; park US MM.

### Phase 1 — CLOB yield scan
| Built | Purpose |
|-------|---------|
| `core/clobclient.py` | Read-only HTTP to `clob.polymarket.com` (`/sampling-markets`, `/book`) |
| `core/clobscore.py` | Docs-reconciled quadratic score + capture estimate |
| `scripts/clob_yield_scan.py` | Rank markets by est $/day; competed vs near-zero tables; CSV snapshots |

### Phase 2 — Deep-dive sequence implementation
| Built | Purpose |
|-------|---------|
| `scripts/clob_stability.py` | Persistent-yield filter → `pilot_universe.csv` |
| `scripts/clob_derive_keys.py` | L1 wallet key → L2 API key/secret/passphrase |
| `core/clobmaker.py` | Two-sided quote prices at fraction of `max_spread` |
| `core/clobtrader.py` | Shadow-gated `py-clob-client-v2` (no live orders unless `CLOB_MODE=live`) |
| `core/clob_ledger.py` | Separate `rewards.csv` vs `fills.csv` + daily pnl |
| `clob_runner.py` | Quoting worker: cancel/replace, inventory, kill switch, fill/reward logs |
| `scripts/clob_scale_gate.py` | Scale only if net/est_gross > 50% over ≥14 days |

### Phase 3 — Regular market pulses
| Built | Purpose |
|-------|---------|
| `scripts/clob_pulse.py` | One command: scan + stability + `pulse.json` / `pulse.md` |
| `.github/workflows/clob-pulse.yml` | Twice daily (00:00 + 15:00 UTC); commits CSVs |
| Render crons `clob-pulse` / `clob-pulse-morning` | Same pulse on Render schedule |

### Phase 4 — Merge + deploy (done)
| Action | Result |
|--------|--------|
| Merged PR #111 | Into `claude/prediction-mm-migration-p1-wja5v5` |
| Retargeted `polymarket-mm` worker | Branch = default; start = `PYTHONPATH=. python clob_runner.py` |
| Env | `CLOB_MODE=shadow` + micro-pilot defaults |
| Created Render crons | `clob-pulse` (15:00 UTC), `clob-pulse-morning` (00:00 UTC) |
| Deploy | Live; shadow quoting 3 pilot markets confirmed in logs |
| One-off pulse job | Succeeded on Render |

**Dashboards**
- Worker: https://dashboard.render.com/worker/srv-d8kmtfrtqb8s73eg6tu0  
- Pulse cron: https://dashboard.render.com/cron/crn-d9eg4fv41pts73eshjk0  

---

## Decisions recorded

| Item | Fact |
|------|------|
| Active venue | Global Polymarket CLOB — `https://clob.polymarket.com` |
| Parked venue | Polymarket US (`api.polymarket.us` / `poly_runner.py`) |
| Default trade mode | `CLOB_MODE=shadow` — no orders hit the exchange |
| Score model | Official quadratic LP rewards (docs-reconciled) |
| Edge status | Gross opportunity measurable; **net edge not proven** until micro-pilot |

---

## Deep-dive plan → code map

| Deep-dive step | Deliverable | Status |
|----------------|-------------|--------|
| §7.1 Stability / regular scans | `clob_pulse.py` + GH Actions + Render crons | Built + deployed |
| §7.2 Docs reconciliation | `core/clobscore.py` — `S=((v−s)/v)²`, size-cutoff mid, Q_min, c=3 | Built |
| §7.3 Eligibility + wallet + L2 keys | `.env.example` + `clob_derive_keys.py` | Scaffolded — **ops: put keys on Render** |
| §7.4 Quoting bot | `clob_runner.py` + `clobtrader.py` | Built + deployed (shadow) |
| §7.5 Micro-pilot defaults | $75 × 3, ≥7d to end, near-zero excluded | Built |
| §7.6 Scale gate | `clob_scale_gate.py` | Built |
| Accounting | `clob_ledger.py` — rewards ≠ fills | Built |

---

## Architecture

```
clob.polymarket.com
        │
        ├─ sampling-markets ──► clob_pulse / clob_yield_scan
        │                         ├─ data/clob_scans/YYYY-MM-DD.csv
        │                         ├─ latest.csv, pulse.json, pulse.md
        │                         └─ clob_stability → pilot_universe.csv
        │
        └─ book?token_id=… ──► clob_runner (shadow|live)
                                  ├─ ClobTrader (shadow gate)
                                  ├─ clobmaker (bid/ask)
                                  └─ clob_ledger (quotes / fills / rewards)
```

---

## File inventory

### Active CLOB path
| Path | Role |
|------|------|
| `scripts/clob_pulse.py` | Regular pulse entrypoint |
| `scripts/clob_yield_scan.py` | Yield scan + CSV |
| `scripts/clob_stability.py` | Persistent-yield → pilot universe |
| `scripts/clob_derive_keys.py` | L1 → L2 credentials |
| `scripts/clob_scale_gate.py` | Scale-up gate |
| `clob_runner.py` | Quoter worker |
| `core/clobclient.py` | Read-only CLOB HTTP |
| `core/clobscore.py` | Quadratic score + capture |
| `core/clobmaker.py` | Quote prices/sizes |
| `core/clobtrader.py` | Shadow-gated trading SDK wrapper |
| `core/clob_ledger.py` | Accounting logs |
| `.github/workflows/clob-pulse.yml` | Scheduled pulse |
| `render.yaml` | IaC reference for cron/worker |
| `data/clob_scans/*` | Snapshot CSVs + pulse artifacts |
| `requirements.txt` | Includes `py-clob-client-v2` |

### Tests
`tests/test_clobscore.py`, `test_clobmaker.py`, `test_clobtrader_shadow.py`, `test_clob_stability.py`

### Parked (US — not the thesis)
`poly_runner.py`, `core/polyclient.py`, `polymaker.py`, `rewardscore.py`, `scripts/poly_scan.py`, `poly_cancel_all.py`

---

## Scoring (docs-reconciled)

https://docs.polymarket.com/market-makers/liquidity-rewards  

```
S(v, s) = ((v - s) / v)^2 * b
```

- `v` = API `max_spread` in **cents**
- Mid ignores levels below `min_size`
- `Q_min` two-sided rule with `c = 3`
- Capture: `est_daily = daily_rate × my_score / (my_score + book_score)`
- Hypothetical quote at half max_spread (weight 0.25)

---

## Keys — where they go

| Where | What |
|-------|------|
| **Render** `polymarket-mm` env | `CLOB_PRIVATE_KEY`, `CLOB_API_KEY`, `CLOB_SECRET`, `CLOB_PASS_PHRASE`, `CLOB_FUNDER`, `CLOB_SIGNATURE_TYPE` — required before `CLOB_MODE=live` |
| Local `.env` | Only for laptop runs / `clob_derive_keys.py` |
| Repo / git | Never |

Get L1 key: export Polymarket wallet private key (Magic reveal or MetaMask).  
Derive L2: `CLOB_PRIVATE_KEY=0x… PYTHONPATH=. python3 scripts/clob_derive_keys.py`

---

## Operator runbook

```bash
# Pulse
PYTHONPATH=. python3 scripts/clob_pulse.py --budget 500 --top 250

# Derive L2 keys → paste into Render env
CLOB_PRIVATE_KEY=0x... PYTHONPATH=. python3 scripts/clob_derive_keys.py

# Quoter (local); Render already runs this in shadow
PYTHONPATH=. python3 clob_runner.py

# Scale gate after pilot pnl exists
PYTHONPATH=. python3 scripts/clob_scale_gate.py --min-days 14 --threshold 0.5
```

Kill switch: `touch data/clob_logs/KILL`

---

## Safety invariants

1. `CLOB_MODE=shadow` → no exchange mutations (unit-tested).  
2. Kill file cancels / stands aside.  
3. Pilot excludes near-zero competition books.  
4. Scale gate blocks size-up unless net > 50% of estimated gross over ≥14 days.  
5. Secrets not in git.

---

## Still ops (not code)

- [ ] Confirm US/FL access / ToS for polymarket.com  
- [ ] Fund Polygon USDC  
- [ ] Put CLOB keys on Render  
- [ ] Flip `CLOB_MODE=live` only for micro-pilot  
- [ ] Raise stability `--min-days` to 5–7 as CSV history accrues  

---

## Intentionally not built

| Item | Why |
|------|-----|
| Event-aware quote pull on near-zero/news markets | Advanced tier per plan |
| Automated eligibility/legal check | Ops |
| On-chain funding automation | Ops |
| Proven net edge | Needs live micro-pilot measurement |

---

*End of build review — share via the GitHub link at the top of this file.*
