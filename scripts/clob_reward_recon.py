"""
Reward reconciliation: actual earned/paid vs estimated (deep-dive / handback P1.5).

Pulls account rewards from CLOB/data API when live creds exist; stores rows in
Supabase clob_rewards with source=actual. Compares actual ÷ estimated per market
and alerts when ratio < 0.7.

Wired into clob_pulse.py (section empty until live).

    PYTHONPATH=. python3 scripts/clob_reward_recon.py
    PYTHONPATH=. python3 scripts/clob_reward_recon.py --alert-ratio 0.7
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from core.clob_ledger import ClobLedger
from core.supabase_clob import SupabaseClob

DATA_API = "https://data-api.polymarket.com"
CLOB_HOST = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
UA = "prediction-mm/clob-reward-recon"
ALERT_RATIO = 0.7


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(url: str) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"_raw": "non-json"}
    except Exception as e:
        return 0, {"_err": str(e)}


def fetch_actual_rewards(address: str = "") -> list[dict]:
    """Best-effort pull of earnings. Empty until live wallet configured."""
    addr = address or os.getenv("CLOB_FUNDER", "") or os.getenv("CLOB_ADDRESS", "")
    out: list[dict] = []
    if not addr:
        return out
    # data-api activity / rewards endpoints vary; try common paths
    for path in (
        f"/v1/rewards?user={urllib.parse.quote(addr)}",
        f"/rewards?user={urllib.parse.quote(addr)}",
        f"/activity?user={urllib.parse.quote(addr)}&type=REWARD",
    ):
        st, data = _get(DATA_API + path)
        if st == 200 and data:
            if isinstance(data, list):
                out.extend(data)
            elif isinstance(data, dict):
                rows = data.get("data") or data.get("rewards") or data.get("earnings") or []
                if isinstance(rows, list):
                    out.extend(rows)
            if out:
                break
    return out


def fetch_estimates_from_sb(sb: SupabaseClob, limit: int = 200) -> list[dict]:
    if not sb.enabled:
        return []
    st, data = sb.select(
        "clob_rewards",
        {"source": "eq.estimate", "order": "ts.desc", "limit": str(limit)},
    )
    if st != 200 or not isinstance(data, list):
        return []
    return data


def reconcile(actuals: list[dict], estimates: list[dict],
              alert_ratio: float = ALERT_RATIO) -> dict:
    """Aggregate actual vs estimated; flag markets with ratio < alert_ratio."""
    act_by: dict[str, float] = {}
    for a in actuals:
        key = str(a.get("market_slug") or a.get("slug") or a.get("condition_id")
                  or a.get("asset") or "account")
        try:
            amt = float(a.get("amount_usd") or a.get("amount") or a.get("earnings")
                        or a.get("reward") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        act_by[key] = act_by.get(key, 0.0) + amt

    est_by: dict[str, float] = {}
    for e in estimates:
        key = str(e.get("market_slug") or e.get("condition_id") or "account")
        try:
            amt = float(e.get("amount_usd") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if amt == 0 and isinstance(e.get("payload_json"), dict):
            try:
                amt = float(e["payload_json"].get("est_daily") or 0)
            except (TypeError, ValueError, AttributeError):
                pass
        est_by[key] = est_by.get(key, 0.0) + amt

    markets = []
    alerts = []
    keys = sorted(set(act_by) | set(est_by))
    for k in keys:
        a = act_by.get(k, 0.0)
        e = est_by.get(k, 0.0)
        ratio = (a / e) if e > 0 else None
        row = {
            "market": k,
            "actual": round(a, 4),
            "estimated": round(e, 4),
            "ratio": None if ratio is None else round(ratio, 4),
        }
        markets.append(row)
        if ratio is not None and ratio < alert_ratio:
            alerts.append(row)

    total_a = sum(act_by.values())
    total_e = sum(est_by.values())
    return {
        "ts": _iso(),
        "live": bool(actuals),
        "total_actual": round(total_a, 4),
        "total_estimated": round(total_e, 4),
        "ratio": None if total_e <= 0 else round(total_a / total_e, 4),
        "alert_ratio": alert_ratio,
        "alerts": alerts,
        "markets": markets,
        "note": "" if actuals else "empty until live — wire only",
    }


def write_section_md(recon: dict) -> str:
    lines = [
        "## Rewards: actual vs estimated",
        "",
        f"- ts: `{recon['ts']}`",
        f"- total actual: ${recon['total_actual']}",
        f"- total estimated: ${recon['total_estimated']}",
        f"- ratio: {recon['ratio'] if recon['ratio'] is not None else 'n/a'}",
    ]
    if recon.get("note"):
        lines.append(f"- note: {recon['note']}")
    if recon.get("alerts"):
        lines += ["", "### ALERT: ratio < {:.2f}".format(recon["alert_ratio"]), ""]
        for a in recon["alerts"]:
            lines.append(
                f"- `{a['market']}` actual={a['actual']} est={a['estimated']} "
                f"ratio={a['ratio']}"
            )
            print(f"[clob-recon] ALERT {a['market']} ratio={a['ratio']}", flush=True)
    else:
        lines += ["", "_No alerts (or no live actuals yet)._"]
    lines.append("")
    return "\n".join(lines)


def run(alert_ratio: float = ALERT_RATIO, address: str = "",
        out_md: Path | None = None) -> dict:
    sb = SupabaseClob()
    ledger = ClobLedger(sb=sb)
    actuals = fetch_actual_rewards(address)
    for a in actuals:
        try:
            amt = float(a.get("amount_usd") or a.get("amount") or a.get("earnings") or 0)
        except (TypeError, ValueError):
            amt = None
        ledger.log_rewards(
            a, note="reward_recon", source="actual",
            amount_usd=amt,
            market_slug=str(a.get("market_slug") or a.get("slug") or ""),
            condition_id=str(a.get("condition_id") or ""),
        )
    estimates = fetch_estimates_from_sb(sb)
    recon = reconcile(actuals, estimates, alert_ratio=alert_ratio)
    section = write_section_md(recon)
    if out_md is not None:
        out_md.parent.mkdir(parents=True, exist_ok=True)
        if out_md.exists():
            text = out_md.read_text()
            marker = "## Rewards: actual vs estimated"
            if marker in text:
                text = text.split(marker)[0].rstrip() + "\n\n" + section
            else:
                text = text.rstrip() + "\n\n" + section
            out_md.write_text(text)
        else:
            out_md.write_text("# CLOB pulse\n\n" + section)
    return recon


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alert-ratio", type=float, default=ALERT_RATIO)
    ap.add_argument("--address", default="")
    ap.add_argument("--out-md", default="data/clob_scans/pulse.md")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    recon = run(
        alert_ratio=args.alert_ratio,
        address=args.address,
        out_md=Path(args.out_md) if args.out_md else None,
    )
    print(json.dumps({
        "ts": recon["ts"],
        "live": recon["live"],
        "total_actual": recon["total_actual"],
        "total_estimated": recon["total_estimated"],
        "ratio": recon["ratio"],
        "alerts": len(recon["alerts"]),
        "note": recon.get("note") or "",
    }, indent=2))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(recon, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
