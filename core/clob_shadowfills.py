"""
Shadow-fill simulator: adverse-selection measurement pre-live.

Polls public trade tape (data-api.polymarket.com/trades) for pilot markets.
When a printed trade crosses a resting shadow quote (trade ≤ bid or ≥ ask),
logs a simulated fill at the shadow quote price/size.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

DATA_API = "https://data-api.polymarket.com"
UA = "prediction-mm/clob-shadowfills"


@dataclass
class ShadowQuote:
    token_id: str
    bid: float | None
    ask: float | None
    bid_size: float
    ask_size: float
    mid: float
    slug: str = ""


@dataclass
class ShadowFillState:
    seen_trade_ids: set[str] = field(default_factory=set)
    inventory: dict[str, float] = field(default_factory=dict)  # token -> net shares
    avg_entry: dict[str, float] = field(default_factory=dict)
    fills_today: int = 0
    adverse_moves: list[float] = field(default_factory=list)


def fetch_trades(token_id: str = "", condition_id: str = "",
                 limit: int = 50) -> list[dict]:
    params = {"limit": str(limit)}
    if token_id:
        params["asset"] = token_id
    if condition_id:
        params["market"] = condition_id
    q = urllib.parse.urlencode(params)
    url = f"{DATA_API}/trades?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
            return data if isinstance(data, list) else data.get("trades") or data.get("data") or []
    except Exception:
        return []


def trade_crosses_quote(trade_price: float, side_hint: str, q: ShadowQuote) -> str | None:
    """Return BUY/SELL if trade would fill our resting shadow quote, else None."""
    # Aggressive sell (trade at/below our bid) fills our bid
    if q.bid is not None and trade_price <= q.bid + 1e-12:
        return "BUY"
    # Aggressive buy (trade at/above our ask) fills our ask
    if q.ask is not None and trade_price >= q.ask - 1e-12:
        return "SELL"
    return None


def apply_fill(state: ShadowFillState, token_id: str, side: str,
               price: float, size: float, mid_after: float):
    signed = size if side.upper() == "BUY" else -size
    prev = state.inventory.get(token_id, 0.0)
    new = prev + signed
    # running avg entry (simple)
    if prev == 0 or (prev > 0 and signed > 0) or (prev < 0 and signed < 0):
        # adding to position
        total_cost = state.avg_entry.get(token_id, price) * abs(prev) + price * abs(signed)
        state.avg_entry[token_id] = total_cost / abs(new) if new != 0 else price
    state.inventory[token_id] = new
    state.fills_today += 1
    # adverse: for buys, mid drop after; for sells, mid rise after
    if side.upper() == "BUY":
        state.adverse_moves.append(price - mid_after)
    else:
        state.adverse_moves.append(mid_after - price)


def mark_to_mid(state: ShadowFillState, mids: dict[str, float]) -> float:
    pnl = 0.0
    for tid, net in state.inventory.items():
        entry = state.avg_entry.get(tid, mids.get(tid, 0.5))
        mid = mids.get(tid, entry)
        pnl += net * (mid - entry)
    return pnl


def process_tape(quotes: list[ShadowQuote], state: ShadowFillState,
                 ledger=None) -> list[dict]:
    """Check recent trades against shadow quotes; return new simulated fills."""
    new_fills = []
    for q in quotes:
        trades = fetch_trades(token_id=q.token_id, limit=30)
        for t in trades:
            tid = str(t.get("id") or t.get("transactionHash") or t.get("trade_id") or "")
            key = f"{q.token_id}:{tid}:{t.get('timestamp') or t.get('match_time') or t.get('createdAt')}"
            if key in state.seen_trade_ids:
                continue
            state.seen_trade_ids.add(key)
            try:
                px = float(t.get("price") or t.get("p") or 0)
                sz = float(t.get("size") or t.get("amount") or t.get("quantity") or 0)
            except (TypeError, ValueError):
                continue
            if px <= 0 or sz <= 0:
                continue
            fill_side = trade_crosses_quote(px, str(t.get("side") or ""), q)
            if not fill_side:
                continue
            fill_px = q.bid if fill_side == "BUY" else q.ask
            fill_sz = min(sz, q.bid_size if fill_side == "BUY" else q.ask_size)
            if fill_px is None or fill_sz <= 0:
                continue
            rec = {
                "id": tid or key,
                "token_id": q.token_id,
                "asset_id": q.token_id,
                "side": fill_side,
                "price": fill_px,
                "size": fill_sz,
                "simulated": True,
                "tape_price": px,
                "slug": q.slug,
            }
            apply_fill(state, q.token_id, fill_side, fill_px, fill_sz, q.mid)
            if ledger is not None:
                ledger.log_fill(rec, simulated=True, mid_at_fill=q.mid)
            new_fills.append(rec)
    # bound seen set
    if len(state.seen_trade_ids) > 5000:
        state.seen_trade_ids = set(list(state.seen_trade_ids)[-2000:])
    return new_fills


def summary(state: ShadowFillState, mids: dict[str, float],
            est_gross_by_token: dict[str, float] | None = None) -> dict:
    mtm = mark_to_mid(state, mids)
    avg_adv = (
        sum(state.adverse_moves) / len(state.adverse_moves)
        if state.adverse_moves else 0.0
    )
    return {
        "fills_today": state.fills_today,
        "avg_adverse_move": round(avg_adv, 6),
        "mtm_pnl": round(mtm, 4),
        "inventory": dict(state.inventory),
        "est_gross": est_gross_by_token or {},
    }
