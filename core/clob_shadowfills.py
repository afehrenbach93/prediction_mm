"""
Shadow-fill simulator: adverse-selection measurement pre-live.

Polls public trade tape (data-api.polymarket.com/trades) for pilot markets.
When a printed trade crosses a resting shadow quote (trade ≤ bid or ≥ ask),
logs a simulated fill at the shadow quote price/size.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

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
    # First tape poll per token only warms seen_ids (avoids backfilling history).
    warmed_tokens: set[str] = field(default_factory=set)
    # Realized MTM booked when flattening (cap / UTC day rollover).
    realized_pnl_today: float = 0.0
    day: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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
    elif new == 0:
        state.avg_entry.pop(token_id, None)
    state.inventory[token_id] = new
    if new == 0:
        state.inventory.pop(token_id, None)
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


def flatten_token(state: ShadowFillState, token_id: str, mid: float) -> float:
    """Realize MTM for one token and clear its inventory. Returns realized $."""
    net = state.inventory.get(token_id, 0.0)
    if abs(net) < 1e-12:
        return 0.0
    entry = state.avg_entry.get(token_id, mid)
    realized = net * (mid - entry)
    state.realized_pnl_today += realized
    state.inventory.pop(token_id, None)
    state.avg_entry.pop(token_id, None)
    return realized


def flatten_all(state: ShadowFillState, mids: dict[str, float]) -> float:
    total = 0.0
    for tid in list(state.inventory.keys()):
        mid = mids.get(tid, state.avg_entry.get(tid, 0.5))
        total += flatten_token(state, tid, mid)
    return total


def rollover_utc_day(state: ShadowFillState, mids: dict[str, float]) -> tuple[bool, float, dict]:
    """If UTC day changed: flatten inventory, snapshot day stats, reset day counters.

    Keeps warmed_tokens / seen_trade_ids so we do not re-backfill history.
    Returns (rolled, realized_today_including_flatten, prior_day_summary).
    """
    today = utc_day()
    if state.day == today:
        return False, state.realized_pnl_today + mark_to_mid(state, mids), {}
    prior = summary(state, mids)
    prior["day"] = state.day
    prior["realized_pnl"] = round(state.realized_pnl_today + mark_to_mid(state, mids), 4)
    flatten_all(state, mids)
    realized = state.realized_pnl_today
    state.fills_today = 0
    state.adverse_moves.clear()
    state.realized_pnl_today = 0.0
    state.day = today
    return True, realized, prior


def _trade_key(token_id: str, t: dict) -> str:
    tid = str(t.get("id") or t.get("transactionHash") or t.get("trade_id") or "")
    return (
        f"{token_id}:{tid}:"
        f"{t.get('timestamp') or t.get('match_time') or t.get('createdAt')}"
    )


def _increases_inventory(inv: float, side: str) -> bool:
    if side.upper() == "BUY":
        return inv >= 0  # long or flat → buy increases |inv| if flat/long
    return inv <= 0  # short or flat → sell increases |inv|


def process_tape(quotes: list[ShadowQuote], state: ShadowFillState,
                 ledger=None, max_fills_per_cycle: int = 5,
                 max_fill_size: float = 5.0,
                 max_inventory: float = 150.0) -> list[dict]:
    """Check recent trades against shadow quotes; return new simulated fills.

    First poll per token is a warm-up: mark tape ids seen without filling.
    At inventory cap: still accept *reducing* fills; flatten token if stuck at cap
    so multi-day sampling continues.
    """
    new_fills = []
    for q in quotes:
        trades = fetch_trades(token_id=q.token_id, limit=40)
        warmed = q.token_id in state.warmed_tokens
        if not warmed:
            for t in trades:
                state.seen_trade_ids.add(_trade_key(q.token_id, t))
            state.warmed_tokens.add(q.token_id)
            continue

        inv = state.inventory.get(q.token_id, 0.0)
        # Soft flatten when stuck at/over cap so we keep collecting fills/day.
        if abs(inv) >= max_inventory - 1e-9:
            flatten_token(state, q.token_id, q.mid or 0.5)
            inv = 0.0

        for t in trades:
            if len(new_fills) >= max_fills_per_cycle:
                break
            key = _trade_key(q.token_id, t)
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
            inv = state.inventory.get(q.token_id, 0.0)
            increasing = _increases_inventory(inv, fill_side)
            if increasing:
                room = max(0.0, max_inventory - abs(inv))
            else:
                # reducing: allow up to flattening the position
                room = abs(inv) if abs(inv) > 1e-12 else max_fill_size
            fill_sz = min(
                sz,
                q.bid_size if fill_side == "BUY" else q.ask_size,
                max_fill_size,
                room if room > 0 else 0.0,
            )
            if fill_px is None or fill_sz <= 0:
                continue
            rec = {
                "id": str(t.get("id") or t.get("transactionHash") or t.get("trade_id") or key),
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
    if len(state.seen_trade_ids) > 8000:
        state.seen_trade_ids = set(list(state.seen_trade_ids)[-3000:])
    return new_fills


def summary(state: ShadowFillState, mids: dict[str, float],
            est_gross_by_token: dict[str, float] | None = None) -> dict:
    mtm = mark_to_mid(state, mids)
    avg_adv = (
        sum(state.adverse_moves) / len(state.adverse_moves)
        if state.adverse_moves else 0.0
    )
    return {
        "day": state.day,
        "fills_today": state.fills_today,
        "avg_adverse_move": round(avg_adv, 6),
        "mtm_pnl": round(mtm, 4),
        "realized_pnl_today": round(state.realized_pnl_today, 4),
        "net_pnl_today": round(state.realized_pnl_today + mtm, 4),
        "inventory": dict(state.inventory),
        "est_gross": est_gross_by_token or {},
    }
