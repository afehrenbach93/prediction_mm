# CLOB live go-live runbook

**Status (2026-07-22):** Live flip from Render `oregon` failed with CLOB **403 geoblock**.
Worker is back on **`CLOB_MODE=shadow`**. `ELIGIBILITY_CONFIRMED=true` remains set.

Official geoblock docs: https://docs.polymarket.com/api-reference/geoblock

---

## What blocked us

| Item | Value |
|------|--------|
| Service | `polymarket-mm` (`srv-d8kmtfrtqb8s73eg6tu0`) |
| Render region | **oregon** (US) |
| Error | `403 Trading restricted in your region` on `POST /order` |
| Docs | United States is **close-only on frontend and API** — **no new orders** from US IPs |
| Polymarket hint | Primary servers `eu-west-2`; **closest non-georestricted region `eu-west-1` (Ireland)** |

Personal eligibility ≠ server IP eligibility. Operator ToS/access can be fine while **Render’s US IP** is still rejected.

**Do not** use a consumer VPN to “fake” a region in violation of Polymarket ToS. Host the **live quoter process** on infrastructure whose **egress IP** is in an API-allowed jurisdiction.

---

## Architecture split (recommended)

| Role | Where | Mode |
|------|--------|------|
| Shadow soak + ledger + pulse | Render Oregon (current) | `CLOB_MODE=shadow` |
| Live micro-pilot quoter | VPS/VM with **allowed egress** (see below) | `CLOB_MODE=live` |

Same Supabase, same keys, same kill flag. Only the live process needs non-blocked egress.

---

## Preflight: prove egress is allowed

From the **candidate live host** (not your laptop unless that is the host):

```bash
curl -sS https://polymarket.com/api/geoblock
# Expect: {"blocked":false, ...}
```

If `blocked: true`, **do not** set live. Pick another region/provider.

Also confirm order path is reachable (will still 401 without keys; must not 403 geoblock):

```bash
curl -sS -o /dev/null -w "%{http_code}\n" -X POST https://clob.polymarket.com/order
# Geoblock often returns 403 with geoblock body; anything else means IP check passed that gate
```

### Region notes (from Polymarket docs — re-check before go-live)

- **US / UM** — API close-only → **no new orders** (Render oregon/ohio/virginia fail this)
- **DE, GB, SG, FR, …** — on API close-only list → Render **frankfurt** / **singapore** likely fail too
- **IE (Ireland)** — frontend close-only; **API not restricted** in docs; aligns with `eu-west-1` hint
- Prefer a small **Ireland (or other API-allowed)** VPS (AWS `eu-west-1`, Hetzner/OVH/etc. in allowed country)

Re-run geoblock after every provider change — lists move.

---

## Live host setup

1. Provision VM in an **API-allowed** region (start with Ireland / `eu-west-1`).
2. Install Python 3.11+, clone repo, `pip install -r requirements.txt`.
3. Copy env (never commit):

```bash
CLOB_MODE=live
ELIGIBILITY_CONFIRMED=true
CLOB_KILL=false
CLOB_HOST=https://clob.polymarket.com
CLOB_CHAIN_ID=137
CLOB_PRIVATE_KEY=...
CLOB_API_KEY=...
CLOB_SECRET=...
CLOB_PASS_PHRASE=...
CLOB_FUNDER=0x...          # Magic/email deposit address
CLOB_SIGNATURE_TYPE=1      # Magic/email
CLOB_BUDGET_PER_MARKET=75
CLOB_MAX_MARKETS=3
CLOB_SPREAD_FRACTION=0.5
CLOB_MIN_HOURS_TO_END=168
CLOB_PILOT_CSV=data/clob_scans/pilot_universe.csv
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...   # or ANON if that is what inserts use
```

4. Ensure pilot CSV is present (pull from `data` branch or last pulse artifact).
5. Geoblock preflight must show `blocked: false`.
6. Start:

```bash
PYTHONPATH=. python3 clob_runner.py
```

Prefer `systemd`/tmux so the process survives SSH disconnect.

7. Keep Render worker on **shadow** so you still get sim fills + a second heartbeat. Do **not** run two **live** quoters on the same wallet.

---

## Flip checklist

- [ ] `curl https://polymarket.com/api/geoblock` → `blocked: false` on live host
- [ ] Wallet funded with **pilot-only** USDC on Polygon (dedicated wallet)
- [ ] `CLOB_FUNDER` + `CLOB_SIGNATURE_TYPE=1` correct for Magic
- [ ] Full L2 set: `CLOB_API_KEY` / `SECRET` / `PASS_PHRASE` + private key
- [ ] Supabase schema applied (`sql/0002_clob_ledger.sql`); kill row `clob_control.kill=false`
- [ ] `ELIGIBILITY_CONFIRMED=true` (operator ToS/access already asserted)
- [ ] Micro-pilot caps unchanged until scale gate passes
- [ ] Kill path tested in shadow: Supabase `clob_control.kill=true` or `CLOB_KILL=true`

### Go

```text
CLOB_MODE=live
```

### Abort (≤1 loop cycle)

```sql
-- Supabase
update clob_control set kill = true, note = 'manual abort', updated_at = now() where id = 1;
```

or set `CLOB_KILL=true` on the live host and restart if env is not polled-only (runner polls both each loop).

---

## First-hour monitoring

Watch live host logs for:

```text
START mode=LIVE (CLOB_MODE=live)
```

**Good:** quote lines **without** `[SHADOW]`; no `403` geoblock; open orders visible on Polymarket for the pilot wallet.

**Bad — abort immediately:**

- `Trading restricted in your region` / geoblock 403
- auth / signature / funder errors
- breaker thrash or unexpected inventory

Supabase checks:

```text
clob_quotes  — mode=live, shadow=false
clob_fills   — simulated=false for real fills
clob_control — kill=false unless aborting
```

---

## After a clean hour

1. Leave micro-pilot size as-is.
2. Let shadow (Render) + live (allowed-region host) run in parallel for days.
3. Scale only via `scripts/clob_scale_gate.py` (Supabase `clob_daily_pnl`, ≥14d, net/est_gross > 0.5).

---

## Render notes

- Current live path **cannot** be Oregon (US API close-only).
- Available Render regions (oregon / ohio / virginia / frankfurt / singapore) map to **US / DE / SG** — all appear on Polymarket’s API restriction lists as of the docs above. **Expect Render-hosted live orders to keep failing** until Render offers an API-allowed region or we add custom egress.
- Practical path: **live quoter on Ireland (or other allowed) VPS**; Render stays shadow.

---

## Incident — 2026-07-22 live attempt

1. Set `CLOB_MODE=live`, `ELIGIBILITY_CONFIRMED=true` on Render.
2. `START mode=LIVE` then immediate `403` geoblock on `/order`.
3. Reverted `CLOB_MODE=shadow`; worker healthy; eligibility flag left `true`.
