# prediction-mm

Polymarket US **liquidity-reward market maker**. A small, focused worker that
rests post-only quotes at the touch on reward-eligible markets to farm the
maker rebate + liquidity-reward pools.

> Migrated from `kalshi-mm` (2026-06-20). The Kalshi engine and its closed
> theses stay in that repo's history. See `CLAUDE.md` for the thesis,
> invariants, and worklog.

## Layout
```
poly_runner.py          worker: market selection + reconcile quote loop + breaker
core/polyclient.py      Polymarket US REST + ED25519 auth, shadow-gated orders
core/polymaker.py       pure quoting strategy (join touch, inventory skew, windows)
lib/fairvalue.py        dormant salvage (spot-anchored fair value), tested
scripts/poly_scan.py    read-only reward-market book scan
tests/                  unittest suite (shadow no-leak gate, maker, breaker, fairvalue)
render.yaml             Render blueprint (polymarket-mm worker, reference)
```

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env            # fill in POLYMARKET_API_KEY / POLYMARKET_SECRET
```

## Run
```bash
python -m unittest discover -s tests -v     # tests
python scripts/poly_scan.py                  # read-only reward-market scan
BOT_MODE=shadow python poly_runner.py        # shadow (no orders reach the exchange)
```

**Safety:** `BOT_MODE=shadow` is the default and records orders without sending
them. Only the operator flips `BOT_MODE=live`, and only after the live reward
economics validate. See `FOLLOWONS.md` before any live quoting.
