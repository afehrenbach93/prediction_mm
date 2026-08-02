"""
CLOB ops accounting.

Source of truth: Supabase tables (clob_quotes, clob_fills, clob_rewards,
clob_daily_pnl, clob_runner_status). CSV under data/clob_logs/ is a convenience
dump only — ephemeral on Render and must not be relied on for the scale gate.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.supabase_clob import SupabaseClob

DEFAULT_DIR = Path("data/clob_logs")


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def unique_fills_by_trade_id(fills: list[dict]) -> list[dict]:
    """Prefer first (newest) row per trade_id; keep rows with empty ids."""
    seen: set[str] = set()
    out: list[dict] = []
    for f in fills:
        tid = str(f.get("trade_id") or "").strip()
        if tid:
            if tid in seen:
                continue
            seen.add(tid)
        out.append(f)
    return out


def trade_id_of(trade: dict) -> str:
    """Stable id for fill dedupe (live get_trades re-polls every cycle)."""
    tid = str(
        trade.get("id")
        or trade.get("trade_id")
        or trade.get("transaction_hash")
        or trade.get("transactionHash")
        or ""
    ).strip()
    if tid:
        return tid
    # Composite fallback when exchange omits id (still unique enough for a day).
    parts = [
        str(trade.get("asset_id") or trade.get("token_id") or ""),
        str(trade.get("side") or ""),
        str(trade.get("price") or ""),
        str(trade.get("size") or trade.get("matched_amount") or ""),
        str(
            trade.get("match_time")
            or trade.get("created_at")
            or trade.get("timestamp")
            or ""
        ),
    ]
    composite = "|".join(parts).strip("|")
    return composite if any(parts) else ""


class ClobLedger:
    def __init__(self, log_dir: Path | str = DEFAULT_DIR, sb: SupabaseClob | None = None):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.sb = sb if sb is not None else SupabaseClob()
        self.csv_enabled = os.getenv("CLOB_CSV_DUMP", "1").strip().lower() not in (
            "0", "false", "no",
        )
        # In-process dedupe — survives only for process lifetime; DB unique is SoT.
        self.seen_trade_ids: set[str] = set()
        self.seen_reward_keys: set[str] = set()

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
                 mid_at_fill: float | None = None) -> bool:
        """Log a fill once. Returns True if newly recorded, False if duplicate."""
        tid = trade_id_of(trade)
        if tid and tid in self.seen_trade_ids:
            return False
        if tid:
            self.seen_trade_ids.add(tid)
            if len(self.seen_trade_ids) > 8000:
                self.seen_trade_ids = set(list(self.seen_trade_ids)[-3000:])

        row = {
            "ts": _iso(),
            "trade_id": tid,
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
            try:
                raw_safe = json.loads(json.dumps(trade, default=str))
            except Exception:
                raw_safe = {"_raw": str(trade)}
            payload = {
                "trade_id": tid or None,
                "token_id": row["token_id"],
                "side": row["side"],
                "price": px,
                "size": sz,
                "fee": str(row["fee"]),
                "simulated": simulated,
                "mid_at_fill": mid_at_fill,
                "raw_json": raw_safe,
            }
            if tid:
                st, _ = self.sb.insert_ignore(
                    "clob_fills", payload, on_conflict="trade_id",
                )
                # Migration not applied yet → fall back to plain insert.
                if st and st >= 400:
                    self.sb.insert("clob_fills", payload)
            else:
                self.sb.insert("clob_fills", payload)
        return True

    def log_rewards(self, payload, note: str = "", source: str = "estimate",
                    amount_usd: float | None = None, market_slug: str = "",
                    condition_id: str = "", dedupe_key: str = "") -> bool:
        """Log a reward row. Returns False if dedupe_key already seen this process."""
        key = (dedupe_key or "").strip()
        if key and key in self.seen_reward_keys:
            return False
        if key:
            self.seen_reward_keys.add(key)
            if len(self.seen_reward_keys) > 4000:
                self.seen_reward_keys = set(list(self.seen_reward_keys)[-1500:])

        self._csv("rewards.csv", ["ts", "source", "note", "dedupe_key", "payload_json"], {
            "ts": _iso(), "source": source, "note": note, "dedupe_key": key,
            "payload_json": json.dumps(payload, default=str)[:8000],
        })
        if self.sb.enabled:
            if isinstance(payload, (dict, list)):
                try:
                    payload_safe = json.loads(json.dumps(payload, default=str))
                except Exception:
                    payload_safe = {"_raw": str(payload)}
            else:
                payload_safe = {"raw": str(payload)}
            row = {
                "source": source,
                "note": note,
                "market_slug": market_slug or None,
                "condition_id": condition_id or None,
                "amount_usd": amount_usd,
                "payload_json": payload_safe,
            }
            if key:
                row["dedupe_key"] = key
                st, _ = self.sb.insert_ignore(
                    "clob_rewards", row, on_conflict="dedupe_key",
                )
                if st and st >= 400:
                    # Column/index missing — drop key and insert plain.
                    row.pop("dedupe_key", None)
                    self.sb.insert("clob_rewards", row)
            else:
                self.sb.insert("clob_rewards", row)
        return True

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

    def update_runner_status(
        self,
        mode: str,
        host: str = "",
        collateral_usd: float | None = None,
        note: str = "",
        payload: dict | None = None,
    ):
        """Upsert per-host heartbeat (ec2 live vs render shadow stay separate)."""
        host = (
            host
            or os.getenv("CLOB_HOST_LABEL", "")
            or os.getenv("HOSTNAME", "")
            or "unknown"
        )
        row: dict[str, Any] = {
            "host": host,
            "mode": mode,
            "updated_at": _iso(),
            "note": note or None,
        }
        if collateral_usd is not None:
            row["collateral_usd"] = float(collateral_usd)
        if payload is not None:
            try:
                row["payload_json"] = json.loads(json.dumps(payload, default=str))
            except Exception:
                row["payload_json"] = {"_raw": str(payload)}
        if self.sb.enabled:
            self.sb.upsert("clob_runner_status", row, on_conflict="host")
        return row

    def kill_requested(self) -> bool:
        """Prefer Supabase clob_control.kill, then env CLOB_KILL."""
        if os.getenv("CLOB_KILL", "").strip().lower() in ("true", "1", "yes"):
            return True
        if self.sb.enabled:
            k = self.sb.get_kill()
            if k is not None:
                return k
        return False
