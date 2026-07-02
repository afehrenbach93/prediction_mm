# Joining the SHARED bot (most people want this)

One worker runs the models and strategies for **everyone**; each user connects their
own Polymarket US account. Your kill switch disconnects **your** account from order
flow — the shared bot never stops.

1. Open the control app and sign up / sign in with your email.
2. Settings → **My trading** → Register (you start OFF, no keys linked).
3. Create API credentials in your Polymarket US account (api.polymarket.us) and send
   the key id + base64 secret to the operator **privately** (never commit them, never
   paste them in the app).
4. The operator adds them to the worker env (e.g. `POLYMARKET_API_KEY__YOURNAME` /
   `POLYMARKET_SECRET__YOURNAME` + a fresh deploy) and links those env-var *names* to
   your `poly_users` row (`key_env` / `secret_env` columns).
5. Settings → My trading → **Arm** when you want the bot trading your account;
   **Turn off** anytime. Off = no orders reach your account and your resting bot
   orders are cancelled; models/tracking are unaffected.

---

# Deploy your own separate instance (optional)

Run this bot against **your own** Polymarket US account, with your own control app.
Everything is per-deployment: your keys, your database, your kill switch. Nothing is
shared with anyone else's instance.

**Safety defaults:** fresh deployments place **zero orders**. Real money requires two
deliberate steps at the end (arming + flipping a taker/mode to live). Do the shadow
week first — that's how every strategy here earned (or lost) its verdict.

## 1. Polymarket US API keys

Create API credentials in your Polymarket US account settings (api.polymarket.us).
You get an **API key id** and a **base64 secret** (ED25519). Never commit them —
they live only in the worker's environment (step 4). Fund the account only after
you've watched the bot run read-only.

## 2. Supabase (heartbeat + control + prediction tracker)

1. Create a project at supabase.com (free tier is fine).
2. Open `supabase/0001_baseline.sql`, replace `YOUR_EMAIL_HERE` with the email
   you'll sign in to the app with, and run it in the SQL editor.
3. Enable Email auth (Authentication → Providers → Email). Sign up your own
   account (password or OTP). Only the email in the SQL policy can flip the bot.
4. Note your project URL and **anon** key (Settings → API).

## 3. What the worker does per mode

| `BOT_MODE` | What runs | Orders? |
|---|---|---|
| `track` | records model predictions, settles them, heartbeats | **no** |
| `shadow` | reward-maker paths log intended orders only | **no** |
| `live` | reward maker quotes; takers run if enabled | only if ARMED |
| `off` | idles | no |

Even in `live`, real orders require `POLY_LIVE_ARMED=true`. Unarmed live = shadow.

## 4. Render background worker

New → Background Worker → connect your fork.
Build: `pip install -r requirements.txt` · Start: `python poly_runner.py`

Environment (start with exactly this; it's the $0-risk config):

| Var | Value |
|---|---|
| `POLYMARKET_API_KEY` | your key id |
| `POLYMARKET_SECRET` | your base64 secret |
| `SUPABASE_URL` | from step 2 |
| `SUPABASE_ANON_KEY` | from step 2 |
| `BOT_MODE` | `track` |
| `POLY_LIVE_ARMED` | *(unset)* |
| `WX_TAKER` / `MLB_TAKER` | `off` |
| `POLY_BUDGET` / `POLY_MAX_MARKETS` / `POLY_MAX_INVENTORY` | `50` / `2` / `50` |
| `POLY_EXPOSURE_CAP` / `POLY_DAILY_LOSS` | `75` / `15` |

**Render gotcha:** env-var changes need a fresh **deploy** — a restart reuses the
old env snapshot. Confirm new values in the START log line.

## 5. Control app (Render static site)

New → Static Site → same fork. Root dir `app/`,
build `npm install && npx expo export -p web`, publish `dist`.
Add a rewrite rule `/*` → `/index.html` (SPA).
Env: `EXPO_PUBLIC_SUPABASE_URL` + `EXPO_PUBLIC_SUPABASE_ANON_KEY` (step 2 values).

Sign in with the account from step 2. **Overview** shows the worker heartbeat,
Live P&L card, and per-model calibration; **Settings** has the kill switch
(`off` ⇄ `track`) and the bounded Go-Live card — that's your on/off control.

## 6. Going live (when YOU decide)

1. Watch a full week in `track`. Read the calibration tab and
   `python scripts/promotion_gate.py` (run in a Render shell on the worker) —
   the gate is ≥100 venue-settled rows with executable odds, model Brier < market,
   positive sim at executable prices.
2. Arm: set `POLY_LIVE_ARMED=true` on the worker (+ fresh deploy).
3. Flip via the app's Go-Live card (bounded budget + auto-revert), or set a taker
   env (`MLB_TAKER=live`, budget vars) with a small budget.
4. Watch the first cycle in the logs: `placed_ok` (HTTP 200) is NOT a resting
   order — verify resting orders in the Polymarket UI.

## Known venue gotchas (hard-won — read before touching order code)

- **Order intents are inverted vs their names**: `SELL_SHORT` opened a LONG in a
  live test. Only two paths are live-proven: post-only `BUY_LONG` (open long) and
  post-only `BUY_SHORT` (open short). Takers (post_only=False) get killed if they
  don't cross.
- Cancel bodies require the order's `marketSlug` (empty body 400s).
- Prices tick 0.01 on game markets, 0.001 on futures.
- PM credits liquidity-reward earnings ~5+2 business days after a period ends.
- Weather markets settle on the official NWS Climatological Report, not raw
  observations — that divergence is why the weather edge failed. Read
  `CLAUDE.md`'s incident log before re-running any closed experiment.
