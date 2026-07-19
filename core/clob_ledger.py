"""
CLOB ops accounting: rewards USDC vs trading P&L kept in separate files.

  data/clob_logs/quotes.csv
  data/clob_logs/fills.csv          # trading
  data/clob_logs/rewards.csv        # incentive receipts (USDC)
  data/clob_logs/pnl_daily.csv      # daily realized trading + rewards summary
  data/clob_logs/events.jsonl
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DIR = Path("data/clob_logs")


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class ClobLedger:
    def __init__(self, log_dir: Path | str = DEFAULT_DIR):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _csv(self, name: str, fields: list[str], row: dict):
        path = self.log_dir / name
        write_header = not path.exists()
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if write_header:
                w.writeheader()
            w.writerow(row)

    def event(self, kind: str, **payload):
        with open(self.log_dir / "events.jsonl", "a") as f:
            f.write(json.dumps({"ts": _iso(), "kind": kind, **payload}, default=str) + "\n")

    def log_quote(self, token_id: str, side: str, price: float, size: float,
                  mid: float, mode: str, shadow: bool, slug: str = ""):
        self._csv("quotes.csv", [
            "ts", "slug", "token_id", "side", "price", "size", "mid", "mode", "shadow",
        ], {
            "ts": _iso(), "slug": slug, "token_id": token_id, "side": side,
            "price": price, "size": size, "mid": mid, "mode": mode, "shadow": shadow,
        })

    def log_fill(self, trade: dict):
        """Trading P&L source — do not mix with rewards."""
        self._csv("fills.csv", [
            "ts", "trade_id", "token_id", "side", "price", "size",
            "fee", "raw_json",
        ], {
            "ts": _iso(),
            "trade_id": trade.get("id") or trade.get("trade_id") or "",
            "token_id": trade.get("asset_id") or trade.get("token_id") or "",
            "side": trade.get("side") or "",
            "price": trade.get("price") or "",
            "size": trade.get("size") or trade.get("matched_amount") or "",
            "fee": trade.get("fee_rate_bps") or trade.get("fee") or "",
            "raw_json": json.dumps(trade, default=str)[:4000],
        })

    def log_rewards(self, payload, note: str = ""):
        """Incentive USDC receipts — separate from fills.csv."""
        self._csv("rewards.csv", ["ts", "note", "payload_json"], {
            "ts": _iso(), "note": note,
            "payload_json": json.dumps(payload, default=str)[:8000],
        })
        self.event("rewards", note=note, summary=str(payload)[:400])

    def log_daily_pnl(self, trading_pnl: float, rewards_usd: float,
                      est_gross: float, note: str = ""):
        """Scale-gate input: net = rewards - |trading losses|."""
        net = rewards_usd + trading_pnl  # trading_pnl negative when losing
        ratio = (net / est_gross) if est_gross > 0 else None
        self._csv("pnl_daily.csv", [
            "day", "ts", "trading_pnl", "rewards_usd", "net", "est_gross",
            "net_vs_gross", "note",
        ], {
            "day": _day(), "ts": _iso(),
            "trading_pnl": trading_pnl, "rewards_usd": rewards_usd,
            "net": net, "est_gross": est_gross,
            "net_vs_gross": "" if ratio is None else round(ratio, 4),
            "note": note,
        })
        return net, ratio
