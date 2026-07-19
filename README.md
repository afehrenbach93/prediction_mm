# prediction-mm

Global Polymarket **CLOB liquidity-reward** stack (deep-dive plan).
Polymarket US MM is parked (no proven edge).

## Operate

```bash
pip install -r requirements.txt
cp .env.example .env
# Apply sql/0002_clob_ledger.sql in Supabase, then set SUPABASE_URL + key.

# 1) Regular CLOB pulse (scan + stability + pulse.json) — also GH Actions 00:00/15:00 UTC
PYTHONPATH=. python3 scripts/clob_pulse.py --budget 500 --top 250 --min-days 5

# 2) Derive L2 keys on a DEDICATED pilot wallet (never the main Magic key)
CLOB_PRIVATE_KEY=0x... PYTHONPATH=. python3 scripts/clob_derive_keys.py

# 3) Quoter (shadow default)
PYTHONPATH=. python3 clob_runner.py

# 4) Scale gate after pilot pnl rows exist (reads Supabase)
PYTHONPATH=. python3 scripts/clob_scale_gate.py
```

### Live gate (P0)
Live orders require **both** `CLOB_MODE=live` **and** `ELIGIBILITY_CONFIRMED=true`.
That flag asserts you verified US/FL access and Polymarket ToS for global
polymarket.com — it does not perform the legal check.

### Kill switch
Polled every loop: `CLOB_KILL=true` **or** Supabase `clob_control.kill=true`.
On kill: cancel all, stand aside, process stays up and logging.

### Wallet hygiene
Use a **dedicated wallet** funded only with pilot capital. Set `CLOB_FUNDER` /
`CLOB_SIGNATURE_TYPE` so leaked L2 credentials cannot touch more than the pilot
wallet. Never export the main Magic wallet key.

### Pulse artifacts
Pulse CSVs are **not** committed to the deploy branch (avoids Render autodeploy
restarting the quoter). GH Actions publishes to the `data` branch + artifacts;
prefer Supabase `clob_pulse_snapshots` when configured.

## Layout
```
clob_runner.py                 quoter (shadow/live + ws mids + shadow fills)
scripts/clob_yield_scan.py     reward yield scan
scripts/clob_stability.py      persistent-yield filter (provisional <5d)
scripts/clob_pulse.py          scan + stability + recon + supabase push
scripts/clob_reward_recon.py   actual vs estimated rewards
scripts/clob_derive_keys.py    L1→L2 credentials
scripts/clob_scale_gate.py     net vs gross (Supabase SoT)
core/eligibility.py            hard live gate
core/clobscore.py              quadratic score (docs-reconciled)
core/clobmaker.py              quote prices/sizes
core/clobtrader.py             shadow-gated py-clob-client-v2
core/clob_ledger.py            Supabase ledger + CSV dump
core/clob_shadowfills.py       tape-cross simulated fills
core/clob_bookws.py            market websocket mids
sql/0002_clob_ledger.sql       Supabase schema
```

See `CLAUDE.md` / `FOLLOWONS.md` / `BUILD_REVIEW.md`.
