"""
Phone-friendly CLOB ops dashboard (Polymarket-styled).

Bind: 0.0.0.0:$PORT (Render web service).
Env:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_ANON_KEY)
  DASHBOARD_TOKEN   — if set, required as ?token= or Authorization: Bearer
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from core.clob_ledger import unique_fills_by_trade_id

STATIC = Path(__file__).resolve().parent / "static"
PORT = int(os.getenv("PORT", "10000"))
TOKEN = (os.getenv("DASHBOARD_TOKEN") or "").strip()


def _sb() -> tuple[str, str]:
    url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or ""
    )
    return url, key


def _sb_req(method: str, path: str, params: dict | None = None,
            body: Any = None) -> tuple[int, Any]:
    url, key = _sb()
    if not url or not key:
        return 0, {"_err": "supabase not configured"}
    q = ("?" + urllib.parse.urlencode(params or {}, doseq=True)) if params else ""
    full = f"{url}/rest/v1/{path}{q}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    data = None if body is None else json.dumps(body, default=str).encode()
    req = urllib.request.Request(full, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"_err": str(e)}
    except Exception as e:
        return None, {"_err": str(e)}


def _iso_ago(hours: float) -> str:
    from datetime import timedelta
    t = datetime.now(timezone.utc) - timedelta(hours=hours)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_status() -> dict:
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")

    st_kill, kill_rows = _sb_req(
        "GET", "clob_control", {"id": "eq.1", "select": "kill,updated_at,note"},
    )
    kill = False
    kill_meta = {}
    if st_kill == 200 and isinstance(kill_rows, list) and kill_rows:
        kill = bool(kill_rows[0].get("kill"))
        kill_meta = kill_rows[0]

    st_status, status_rows = _sb_req(
        "GET", "clob_runner_status",
        {
            "select": "mode,host,collateral_usd,updated_at,note,payload_json",
            "order": "updated_at.desc",
            "limit": "10",
        },
    )
    runners = status_rows if (st_status == 200 and isinstance(status_rows, list)) else []
    runner_live = next(
        (r for r in runners if (r.get("mode") or "").lower() == "live"
         or (r.get("host") or "").lower() in ("ec2", "live")),
        None,
    )
    runner_shadow = next(
        (r for r in runners if (r.get("mode") or "").lower() == "shadow"
         or (r.get("host") or "").lower() in ("render", "shadow")),
        None,
    )
    # Prefer live heartbeat for collateral / primary mode; else newest row.
    runner = runner_live or (runners[0] if runners else {})

    st_pnl, pnl_rows = _sb_req(
        "GET", "clob_daily_pnl",
        {"select": "*", "order": "day.desc", "limit": "14"},
    )
    pnl = pnl_rows if isinstance(pnl_rows, list) else []
    today_pnl = next((p for p in pnl if str(p.get("day")) == day), None)

    since_24h = _iso_ago(24)
    st_fills, fills = _sb_req(
        "GET", "clob_fills",
        {
            "select": "ts,trade_id,token_id,side,price,size,simulated,mid_at_fill",
            "ts": f"gte.{since_24h}",
            "order": "ts.desc",
            "limit": "200",
        },
    )
    fills_raw = fills if isinstance(fills, list) else []
    fills = unique_fills_by_trade_id(fills_raw)

    since_6h = _iso_ago(6)
    st_quotes, quotes = _sb_req(
        "GET", "clob_quotes",
        {
            "select": "ts,slug,token_id,side,price,size,mid,mode,shadow",
            "ts": f"gte.{since_6h}",
            "order": "ts.desc",
            "limit": "120",
        },
    )
    quotes = quotes if isinstance(quotes, list) else []

    st_rew, rewards = _sb_req(
        "GET", "clob_rewards",
        {
            "select": "ts,source,note,market_slug,amount_usd,condition_id,dedupe_key",
            "order": "ts.desc",
            "limit": "40",
        },
    )
    rewards = rewards if isinstance(rewards, list) else []
    # If dedupe_key column missing (migration not applied), retry without it.
    if st_rew and st_rew >= 400:
        st_rew, rewards = _sb_req(
            "GET", "clob_rewards",
            {
                "select": "ts,source,note,market_slug,amount_usd,condition_id",
                "order": "ts.desc",
                "limit": "40",
            },
        )
        rewards = rewards if isinstance(rewards, list) else []

    st_pulse, pulse_rows = _sb_req(
        "GET", "clob_pulse_snapshots",
        {"select": "ts,day,payload_json", "order": "ts.desc", "limit": "1"},
    )
    pulse = None
    if st_pulse == 200 and isinstance(pulse_rows, list) and pulse_rows:
        pulse = pulse_rows[0]

    live_quotes = [
        q for q in quotes
        if not q.get("shadow") and (q.get("mode") or "").lower() == "live"
    ]
    shadow_quotes = [q for q in quotes if q.get("shadow") or (q.get("mode") or "").lower() == "shadow"]

    runner_mode = (runner.get("mode") or "").lower()
    if runner_mode in ("live", "shadow"):
        mode = runner_mode
    else:
        mode = "live" if live_quotes else ("shadow" if quotes else "unknown")

    last_quote = quotes[0]["ts"] if quotes else None
    last_live_quote = live_quotes[0]["ts"] if live_quotes else None
    last_shadow_quote = shadow_quotes[0]["ts"] if shadow_quotes else None
    last_fill = fills[0]["ts"] if fills else None
    live_fills = [f for f in fills if not f.get("simulated")]
    sim_fills = [f for f in fills if f.get("simulated")]

    actual_rewards = [r for r in rewards if (r.get("source") or "") == "actual"]
    actual_today = sum(
        float(r.get("amount_usd") or 0)
        for r in actual_rewards
        if (r.get("note") or "").endswith(day) or f":{day}" in (r.get("dedupe_key") or "")
        or (r.get("note") or "").startswith(f"actual")
    )
    # Prefer total row if present to avoid double-count per-market + total.
    total_rows = [
        r for r in actual_rewards
        if "total" in (r.get("dedupe_key") or "") or "total" in (r.get("note") or "")
    ]
    if total_rows:
        try:
            actual_today = float(total_rows[0].get("amount_usd") or 0)
        except (TypeError, ValueError):
            pass

    # Unique slugs recently quoted — split live vs shadow
    def _markets_from(qlist: list[dict]) -> list[dict]:
        markets: dict[str, dict] = {}
        for q in qlist:
            slug = q.get("slug") or q.get("token_id", "")[:16]
            if slug not in markets:
                markets[slug] = {
                    "slug": slug,
                    "last_ts": q.get("ts"),
                    "mid": q.get("mid"),
                    "mode": q.get("mode"),
                    "shadow": q.get("shadow"),
                    "sides": set(),
                    "last_price": q.get("price"),
                    "last_size": q.get("size"),
                }
            markets[slug]["sides"].add((q.get("side") or "").upper())
        out = []
        for m in markets.values():
            out.append({
                **{k: v for k, v in m.items() if k != "sides"},
                "sides": sorted(m["sides"]),
            })
        return out

    live_markets = _markets_from(live_quotes)[:20]
    shadow_markets = _markets_from(shadow_quotes)[:20]
    market_list = _markets_from(quotes)[:20]

    collateral = runner.get("collateral_usd")
    try:
        collateral = float(collateral) if collateral is not None else None
    except (TypeError, ValueError):
        collateral = None

    return {
        "ok": True,
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "supabase": bool(_sb()[0] and _sb()[1]),
        "mode": mode,
        "host": runner.get("host") or ("ec2" if mode == "live" else "render"),
        "collateral_usd": collateral,
        "runner": runner,
        "runners": {
            "live": runner_live,
            "shadow": runner_shadow,
            "all": runners,
        },
        "kill": kill,
        "kill_meta": kill_meta,
        "today": today_pnl,
        "pnl_history": pnl[:14],
        "fills_24h": fills[:80],
        "fills_raw_count": len(fills_raw),
        "fills_live_count": len(live_fills),
        "fills_sim_count": len(sim_fills),
        "fills_unique": True,
        "quotes_6h": quotes,
        "markets": market_list,
        "markets_live": live_markets,
        "markets_shadow": shadow_markets,
        "rewards": rewards,
        "rewards_actual_today": round(actual_today, 4) if actual_rewards else None,
        "pulse": pulse,
        "streams": {
            "live": {
                "quotes": len(live_quotes),
                "markets": len(live_markets),
                "last_quote_ts": last_live_quote,
                "host": "ec2",
            },
            "shadow": {
                "quotes": len(shadow_quotes),
                "markets": len(shadow_markets),
                "last_quote_ts": last_shadow_quote,
                "host": "render",
            },
        },
        "heartbeat": {
            "last_quote_ts": last_quote,
            "last_fill_ts": last_fill,
            "runner_ts": runner.get("updated_at"),
            "live_runner_ts": (runner_live or {}).get("updated_at"),
            "shadow_runner_ts": (runner_shadow or {}).get("updated_at"),
            "quotes_ok": st_quotes == 200,
            "fills_ok": st_fills == 200,
            "status_ok": st_status == 200,
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "clob-ops/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[dash] {self.address_string()} {fmt % args}", flush=True)

    def _authed(self) -> bool:
        if not TOKEN:
            return True
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer ") and auth[7:].strip() == TOKEN:
            return True
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        if (params.get("token") or [""])[0] == TOKEN:
            return True
        return False

    def _json(self, code: int, body: Any):
        raw = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _file(self, path: Path, ctype: str):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path or "/"

        if path in ("/health", "/healthz"):
            self._json(200, {"ok": True})
            return

        if path.startswith("/api/"):
            if not self._authed():
                self._json(401, {"ok": False, "error": "unauthorized"})
                return
            if path == "/api/status":
                self._json(200, build_status())
                return
            self._json(404, {"ok": False, "error": "not found"})
            return

        # Static
        if path == "/":
            path = "/index.html"
        rel = path.lstrip("/")
        fp = (STATIC / rel).resolve()
        if not str(fp).startswith(str(STATIC.resolve())) or not fp.is_file():
            self._json(404, {"ok": False, "error": "not found"})
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(fp.suffix, "application/octet-stream")
        self._file(fp, ctype)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path or "/"
        if not self._authed():
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode() or "{}")
        except Exception:
            self._json(400, {"ok": False, "error": "bad json"})
            return

        if path == "/api/kill":
            kill = bool(body.get("kill"))
            note = str(body.get("note") or (
                "kill via dashboard" if kill else "resume via dashboard"
            ))
            url, key = _sb()
            if not url or not key:
                self._json(503, {"ok": False, "error": "supabase not configured"})
                return
            full = f"{url}/rest/v1/clob_control?id=eq.1"
            req = urllib.request.Request(
                full,
                data=json.dumps({
                    "kill": kill,
                    "note": note,
                    "updated_at": datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                }).encode(),
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation",
                },
                method="PATCH",
            )
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    st = r.status
                    data = json.loads(r.read() or b"{}")
            except Exception as e:
                st, data = None, {"_err": str(e)}
            self._json(200 if st in (200, 204) else 502, {
                "ok": st in (200, 204),
                "kill": kill,
                "status": st,
                "data": data,
            })
            return

        self._json(404, {"ok": False, "error": "not found"})


def main():
    STATIC.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[dash] listening on 0.0.0.0:{PORT} token={'set' if TOKEN else 'off'}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
