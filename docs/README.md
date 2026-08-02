# Documentation index

Single hub for every document in this repo. A few files must stay at the repo root
because tooling loads them from there (README on GitHub, `CLAUDE.md` / `AGENTS.md` for
AI agents); they are linked below so this page is still the one spot to find everything.

## In this folder (`docs/`)
- [`CLOB_LIVE_RUNBOOK.md`](CLOB_LIVE_RUNBOOK.md) — live egress + flip/abort checklist for the CLOB quoter.
- [`FOLLOWONS.md`](FOLLOWONS.md) — deployment follow-ons and open items.
- [`BUILD_REVIEW.md`](BUILD_REVIEW.md) — build review of the CLOB liquidity-reward stack.

## Repo-root docs (kept at root by convention/tooling)
- [`../README.md`](../README.md) — project overview + `## Operate` run commands.
- [`../CLAUDE.md`](../CLAUDE.md) — thesis, invariants, architecture, and dated worklog.
- [`../AGENTS.md`](../AGENTS.md) — Cursor Cloud dev-environment notes (deps, tests, run, safety gates).

## Generated artifacts (not hand-written docs)
- `../data/clob_scans/pulse.md` — latest CLOB pulse snapshot, written by `scripts/clob_pulse.py`.
