# AGENTS.md

Project context, thesis, invariants, and worklog live in `CLAUDE.md` — read it first.

## Cursor Cloud specific instructions

Tiny stdlib-only Python worker (Python 3.12 here; CI uses 3.11). The only third-party
dependency is `cryptography` (ED25519 signing), installed by the update script.
There is no web UI — the "app" is the background worker `poly_runner.py`.

- Run tests: `python3 -m unittest discover -s tests -v` (26 tests, `unittest` only, no network). There is no separate linter.
- Scripts import the top-level `core`/`lib` packages, so run them from the repo root with the repo root on `PYTHONPATH`, e.g. `PYTHONPATH=. python3 scripts/poly_scan.py`. Invoking `python3 scripts/poly_scan.py` directly fails with `ModuleNotFoundError: No module named 'core'` because Python puts `scripts/` (not the repo root) on the path.
- Read-only scan (safe, hits the public API): `PYTHONPATH=. python3 scripts/poly_scan.py [budget]`.
- Run the worker in shadow: `BOT_MODE=shadow python3 poly_runner.py` (from repo root). `BOT_MODE=shadow` is the default and the hard safety gate — `PolyClient(live=False)` records intended orders to `shadow_orders` and sends NONE to the exchange. Only the operator flips `BOT_MODE=live`; never do so in an agent session.
- Live reads use the public host `gateway.polymarket.us` (no auth) plus signed calls to `api.polymarket.us`. Signed reads (positions/earnings) need `POLYMARKET_API_KEY` + `POLYMARKET_SECRET`; when absent they return `{"_err": "no credentials configured"}` and the worker degrades gracefully (shadow still works). These are injected as env vars in this environment.
- Each worker cycle does a metadata refresh that fetches ~100 markets, so the first cycle can take ~5-10s before quotes appear.
