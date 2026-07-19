"""
Append-only ops ledger for intended quotes, fills placeholders, and reward
earnings. Separate reward USDC from trading P&L for tax/ops reporting.

Writes under data/logs/ (gitignored). Stdlib only.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG_DIR = Path("data/logs")


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OpsLedger:
    def __init__(self, log_dir: Path | str = DEFAULT_LOG_DIR):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.quotes_path = self.log_dir / "quotes.csv"
        self.fills_path = self.log_dir / "fills.csv"
        self.rewards_path = self.log_dir / "rewards.csv"
        self.events_path = self.log_dir / "events.jsonl"

    def _append_csv(self, path: Path, fields: list[str], row: dict):
        write_header = not path.exists()
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if write_header:
                w.writeheader()
            w.writerow(row)

    def log_event(self, kind: str, **payload):
        rec = {"ts": _iso_now(), "kind": kind, **payload}
        with open(self.events_path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def log_quote(self, slug: str, intent: str, price: float, qty: float,
                  mid: float | None, mode: str, shadow: bool):
        self._append_csv(self.quotes_path, [
            "ts", "slug", "intent", "price", "qty", "mid", "mode", "shadow",
        ], {
            "ts": _iso_now(), "slug": slug, "intent": intent,
            "price": price, "qty": qty, "mid": mid if mid is not None else "",
            "mode": mode, "shadow": shadow,
        })

    def log_fill_placeholder(self, slug: str, side: str, price: float,
                             qty: float, mid_at_fill: float | None,
                             note: str = ""):
        """Placeholder until a trade-history feed is wired; still useful for
        shadow intended-fill and manual import."""
        self._append_csv(self.fills_path, [
            "ts", "slug", "side", "price", "qty", "mid_at_fill",
            "markout_placeholder", "note",
        ], {
            "ts": _iso_now(), "slug": slug, "side": side,
            "price": price, "qty": qty,
            "mid_at_fill": mid_at_fill if mid_at_fill is not None else "",
            "markout_placeholder": "", "note": note,
        })

    def log_rewards(self, raw: dict | list | None, note: str = ""):
        """Record a poll of /v1/incentives/earnings. Reward USDC stays separate
        from trading P&L (fills.csv)."""
        self._append_csv(self.rewards_path, [
            "ts", "note", "payload_json",
        ], {
            "ts": _iso_now(), "note": note,
            "payload_json": json.dumps(raw, default=str)[:8000],
        })
        self.log_event("rewards_poll", note=note,
                       summary=str(raw)[:500] if raw is not None else "")
