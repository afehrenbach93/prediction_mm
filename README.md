# prediction-mm

Polymarket US **liquidity-reward market maker**. A small, focused worker that
rests post-only quotes at the touch on reward-eligible markets to farm the
maker rebate + liquidity-reward pools.

> Migrated from `kalshi-mm` (2026-06-20). The Kalshi engine and its closed
> theses stay in that repo's history. See `CLAUDE.md` for the thesis,
> invariants, and worklog.

## Layout
```
poly_runner.py              worker: select + reconcile + breaker + earnings ledger
core/polyclient.py          Polymarket US REST + ED25519 auth, shadow-gated orders
core/polymaker.py           pure quoting (join touch, inventory skew, windows)
core/rewardscore.py         US multi-level reward score + capture estimate
core/ledger.py              quotes / fills / rewards logs under data/logs/
lib/fairvalue.py            dormant salvage (spot-anchored fair value), tested
scripts/poly_scan.py        yield scan + CSV + daily stability snapshots
scripts/poly_cancel_all.py  one-shot LIVE cancel of leftover resting orders
tests/                      unittest suite
render.yaml                 Render blueprint (polymarket-mm worker, reference)
```

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env            # fill in POLYMARKET_API_KEY / POLYMARKET_SECRET
```

## Run
```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
PYTHONPATH=. python3 scripts/poly_scan.py 500          # yield scan + snapshot
PYTHONPATH=. python3 scripts/poly_scan.py --history    # multi-day stability
BOT_MODE=shadow PYTHONPATH=. python3 poly_runner.py    # shadow (default)
PYTHONPATH=. python3 scripts/poly_cancel_all.py --dry-run  # list leftover live orders
```

**Safety:** `BOT_MODE=shadow` is the default and records orders without sending
them. Only the operator flips `BOT_MODE=live`, and only after the live reward
economics validate. See `FOLLOWONS.md` before any live quoting.
