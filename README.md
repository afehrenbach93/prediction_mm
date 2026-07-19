# prediction-mm

**Active thesis:** Polymarket **global CLOB** liquidity-reward yield search
(quadratic scoring on `clob.polymarket.com`). Polymarket US MM is parked — no
proven edge after extended testing.

See `CLAUDE.md` for thesis / invariants / worklog.

## Layout
```
scripts/clob_yield_scan.py  ACTIVE: CLOB reward-yield scan + daily CSV snapshots
core/clobclient.py          read-only CLOB HTTP (sampling-markets, book)
core/clobscore.py           quadratic LP score + capture estimate
poly_runner.py              PARKED: US shadow worker
core/polyclient.py          US REST + ED25519 (parked)
scripts/poly_scan.py        US scan (parked)
scripts/poly_cancel_all.py  one-shot LIVE cancel leftover US orders
tests/                      unittest suite
```

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env   # only needed for parked US worker / cancel helper
```

## Run (edge search)
```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
PYTHONPATH=. python3 scripts/clob_yield_scan.py --budget 500 --top 250
PYTHONPATH=. python3 scripts/clob_yield_scan.py --history
```

**Safety:** CLOB code is read-only today (no order placement). US worker stays
`BOT_MODE=shadow`. See `FOLLOWONS.md` for the path to a micro-pilot.
