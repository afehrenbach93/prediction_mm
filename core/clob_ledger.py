"""
CLOB ops accounting.

Source of truth: Supabase tables (clob_quotes, clob_fills, clob_rewards,
clob_daily_pnl). CSV under data/clob_logs/ is a convenience dump only —
ephemeral on Render and must not be relied on for the scale gate.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from core.supabase_clob import SupabaseClob

DEFAULT_DIR = Path("data/clob_logs")


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class ClobLedger:
    def __init__(self, log_dir: Path | str = DEFAULT_DIR, sb: SupabaseClob | None = None):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.sb = sb if sb is not None else SupabaseClob()
        self.csv_enabled = os.getenv("CLOB_CSV_DUMP", "1").strip().lower() not in (
            "0", "false", "no",
        )

    def _csv(self, name: str, fields: list[str], row: dict):
        if not self.csv_enabled:
            return
        path = self.log_dir / name
        write_header = not path.exists()
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if write_header:
                w.writeheader()
            w.writerow(row)

    def event(self, kind: str, **payload):
        rec = {"ts": _iso(), "kind": kind, **payload}
        if self.csv_enabled:
            with open(self.log_dir / "events.jsonl", "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        if self.sb.enabled:
            self.sb.insert("clob_rewards", {
                "source": "event",
                "note": kind,
                "payload_json": rec,
            })

    def log_quote(self, token_id: str, side: str, price: float, size: float,
                  mid: float, mode: str, shadow: bool, slug: str = ""):
        row = {
            "ts": _iso(), "slug": slug, "token_id": token_id, "side": side,
            "price": price, "size": size, "mid": mid, "mode": mode, "shadow": shadow,
        }
        self._csv("quotes.csv", list(row.keys()), row)
        if self.sb.enabled:
            self.sb.insert("clob_quotes", {
                "slug": slug, "token_id": token_id, "side": side,
                "price": price, "size": size, "mid": mid, "mode": mode, "shadow": shadow,
            })

    def log_fill(self, trade: dict, simulated: bool = False,
                 mid_at_fill: float | None = None):
        row = {
            "ts": _iso(),
            "trade_id": trade.get("id") or trade.get("trade_id") or "",
            "token_id": trade.get("asset_id") or trade.get("token_id") or "",
            "side": trade.get("side") or "",
            "price": trade.get("price") or "",
            "size": trade.get("size") or trade.get("matched_amount") or "",
            "fee": trade.get("fee_rate_bps") or trade.get("fee") or "",
            "simulated": simulated,
            "mid_at_fill": mid_at_fill if mid_at_fill is not None else "",
            "raw_json": json.dumps(trade, default=str)[:4000],
        }
        self._csv("fills.csv", list(row.keys()), row)
        if self.sb.enabled:
            try:
                px = float(row["price"]) if row["price"] != "" else None
            except (TypeError, ValueError):
                px = None
            try:
                sz = float(row["size"]) if row["size"] != "" else None
            except (TypeError, ValueError):
                sz = None
            self.sb.insert("clob_fills", {
                "trade_id": row["trade_id"],
                "token_id": row["token_id"],
                "side": row["side"],
                "price": px,
                "size": sz,
                "fee": str(row["fee"]),
                "simulated": simulated,
                "mid_at_fill": mid_at_fill,
                "raw_json": trade,
            })

    def log_rewards(self, payload, note: str = "", source: str = "estimate",
                    amount_usd: float | None = None, market_slug: str = "",
                    condition_id: str = ""):
        self._csv("rewards.csv", ["ts", "source", "note", "payload_json"], {
            "ts": _iso(), "source": source, "note": note,
            "payload_json": json.dumps(payload, default=str)[:8000],
        })
        if self.sb.enabled:
            self.sb.insert("clob_rewards", {
                "source": source,
                "note": note,
                "market_slug": market_slug or None,
                "condition_id": condition_id or None,
                "amount_usd": amount_usd,
                "payload_json": payload if isinstance(payload, (dict, list)) else {
                    "raw": str(payload)
                },
            })

    def log_daily_pnl(self, trading_pnl: float, rewards_usd: float,
                      est_gross: float, note: str = ""):
        net = rewards_usd + trading_pnl
        ratio = (net / est_gross) if est_gross > 0 else None
        day = _day()
        row = {
            "day": day, "ts": _iso(),
            "trading_pnl": trading_pnl, "rewards_usd": rewards_usd,
            "net": net, "est_gross": est_gross,
            "net_vs_gross": "" if ratio is None else round(ratio, 4),
            "note": note,
        }
        self._csv("pnl_daily.csv", list(row.keys()), row)
        if self.sb.enabled:
            self.sb.upsert("clob_daily_pnl", {
                "day": day,
                "trading_pnl": trading_pnl,
                "rewards_usd": rewards_usd,
                "net": net,
                "est_gross": est_gross,
                "net_vs_gross": ratio,
                "note": note,
            }, on_conflict="day")
        return net, ratio

    def kill_requested(self) -> bool:
        """Prefer Supabase clob_control.kill, then env CLOB_KILL."""
        if os.getenv("CLOB_KILL", "").strip().lower() in ("true", "1", "yes"):
            return True
        if self.sb.enabled:
            k = self.sb.get_kill()
            if k is not None:
                return k
        return False
