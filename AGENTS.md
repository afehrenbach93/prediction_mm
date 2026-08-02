# AGENTS.md

Project context, thesis, invariants, and worklog live in `CLAUDE.md`. Run/operate
commands live in `README.md` `## Operate`. Read both first. This file only adds
durable, non-obvious environment notes.

## Cursor Cloud specific instructions

Small Python codebase (Python 3.12 here; CI uses 3.11). Two stacks:
- ACTIVE — global Polymarket CLOB liquidity-reward maker: `clob_runner.py`, the
  `core/clob_*` modules, `scripts/clob_*`, and the `dashboard/` web service.
- PARKED — Polymarket US maker (`poly_runner.py`, `core/poly*`); kept for reference,
  "no proven edge". Don't assume it's the primary path.

Dependencies (`pip install -r requirements.txt`, done by the startup update script):
`cryptography`, `py-clob-client-v2`, `polymarket-client`, `websocket-client`.

- Everything imports the top-level `core`/`lib` packages, so run every entry point
  from the repo root with `PYTHONPATH=.` — runners, scripts, dashboard, and tests.
  Invoking e.g. `python3 scripts/clob_yield_scan.py` without it fails with
  `ModuleNotFoundError: No module named 'core'` (Python puts `scripts/`, not the
  repo root, on the path).
- Tests: `PYTHONPATH=. python3 -m unittest discover -s tests -v` (119 tests, stdlib
  `unittest`, no network). CI runs the identical command on 3.11. There is no
  separate linter. `pytest` is only a transitive dependency, not the runner; pip also
  drops console scripts (`pytest`, `httpx`) in `~/.local/bin`, which isn't on PATH —
  invoke tools via `python3 -m ...`.
- Dashboard web service: `PYTHONPATH=. python3 dashboard/app.py`. Stdlib HTTP server,
  binds `0.0.0.0:$PORT` (default 10000); optional `DASHBOARD_TOKEN` gate. It boots and
  serves without Supabase, but only renders real data when Supabase env is set.

Safety gates — NEVER flip these to live in an agent session:
- CLOB defaults to `CLOB_MODE=shadow`; live orders require BOTH `CLOB_MODE=live` AND
  `ELIGIBILITY_CONFIRMED=true`. Legacy poly stack defaults to `BOT_MODE=shadow`.
- Kill switch, polled every loop: `CLOB_KILL=true` or Supabase `clob_control.kill`.
- Shadow runners still make real READ-ONLY network calls (build the CLOB token index,
  subscribe to a market websocket) — no credentials needed for shadow. Live trading,
  the ledger, the scale gate, reward recon, and dashboard data need Supabase
  (`SUPABASE_URL` + service-role/anon key; schema `sql/0002_clob_ledger.sql`) and a
  dedicated pilot wallet key; all degrade gracefully when those are absent.
