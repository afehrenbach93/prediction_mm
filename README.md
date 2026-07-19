# prediction-mm

Global Polymarket **CLOB liquidity-reward** stack (deep-dive plan).
Polymarket US MM is parked (no proven edge).

## Operate

```bash
pip install -r requirements.txt
cp .env.example .env

# 1) Regular CLOB pulse (scan + stability + pulse.json) — also GH Actions 00:00/15:00 UTC
PYTHONPATH=. python3 scripts/clob_pulse.py --budget 500 --top 250

# 2) Derive L2 keys (ops; needs wallet key)
CLOB_PRIVATE_KEY=0x... PYTHONPATH=. python3 scripts/clob_derive_keys.py

# 3) Quoter (shadow default)
PYTHONPATH=. python3 clob_runner.py

# 4) Scale gate after pilot pnl rows exist
PYTHONPATH=. python3 scripts/clob_scale_gate.py
```

Kill switch: `touch data/clob_logs/KILL`

## Layout
```
clob_runner.py                 quoter (shadow/live)
scripts/clob_yield_scan.py     reward yield scan
scripts/clob_stability.py      persistent-yield filter
scripts/clob_derive_keys.py    L1→L2 credentials
scripts/clob_scale_gate.py     net vs gross scale rule
core/clobscore.py              quadratic score (docs-reconciled)
core/clobmaker.py              quote prices/sizes
core/clobtrader.py             shadow-gated py-clob-client-v2
core/clob_ledger.py            rewards vs fills accounting
```

See `CLAUDE.md` / `FOLLOWONS.md`.
