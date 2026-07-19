"""
Pure CLOB quoting helpers (no network).

Two-sided quotes at a configurable fraction of max_spread around mid,
sized to budget, respecting min_size and inventory room.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ClobQuoteParams:
    budget_usd: float = 75.0          # notional per market (both sides combined)
    spread_fraction: float = 0.5      # quote distance = fraction * max_spread
    max_inventory: float = 200.0      # |net shares| hard cap
    min_price: float = 0.01
    max_price: float = 0.99


@dataclass(frozen=True)
class Quote:
    side: str          # BUY or SELL
    price: float
    size: float


def round_tick(px: float, tick: float) -> float:
    if tick <= 0:
        return round(px, 4)
    n = round(px / tick)
    return round(n * tick, 10)


def quote_prices(mid: float, max_spread_cents: float, tick: float,
                 fraction: float = 0.5) -> tuple[float, float]:
    """Bid/ask prices at ± fraction * max_spread from mid."""
    half = (max_spread_cents / 100.0) * fraction
    bid = round_tick(mid - half, tick)
    ask = round_tick(mid + half, tick)
    if bid >= ask:
        # ensure at least one tick of separation
        bid = round_tick(mid - tick, tick)
        ask = round_tick(mid + tick, tick)
    return bid, ask


def size_per_side(budget_usd: float, mid: float, min_sz: float) -> float:
    if mid <= 0 or budget_usd <= 0:
        return 0.0
    raw = (budget_usd / 2.0) / mid
    # meet rewards min_size when possible
    return max(raw, float(min_sz))


def maker_quotes(mid: float, max_spread_cents: float, tick: float,
                 position: float, min_sz: float, p: ClobQuoteParams) -> list[Quote]:
    """Desired resting post-only quotes. Empty if mid/band invalid."""
    if mid <= 0 or max_spread_cents <= 0:
        return []
    bid_px, ask_px = quote_prices(mid, max_spread_cents, tick, p.spread_fraction)
    if not (p.min_price <= bid_px < ask_px <= p.max_price):
        return []
    sz = size_per_side(p.budget_usd, mid, min_sz)
    buy_room = max(0.0, p.max_inventory - position)
    sell_room = max(0.0, p.max_inventory + position)
    buy_sz = math.floor(min(sz, buy_room) * 100) / 100
    sell_sz = math.floor(min(sz, sell_room) * 100) / 100
    out: list[Quote] = []
    if buy_sz >= max(min_sz, 1.0) or (min_sz <= 0 and buy_sz >= 1.0):
        if buy_sz > 0:
            out.append(Quote("BUY", bid_px, buy_sz))
    if sell_sz >= max(min_sz, 1.0) or (min_sz <= 0 and sell_sz >= 1.0):
        if sell_sz > 0:
            out.append(Quote("SELL", ask_px, sell_sz))
    return out
