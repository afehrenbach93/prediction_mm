"""
Pure CLOB quoting helpers (no network).

Two-sided quotes at a configurable fraction of max_spread around mid,
sized to budget, respecting min_size and inventory room.
Prices are clamped to (0, 1) exclusive and rounded to tick.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ClobQuoteParams:
    budget_usd: float = 75.0
    spread_fraction: float = 0.5
    max_inventory: float = 200.0
    min_price: float = 0.01
    max_price: float = 0.99


@dataclass(frozen=True)
class Quote:
    side: str
    price: float
    size: float


def round_tick(px: float, tick: float) -> float:
    if tick <= 0:
        return round(px, 4)
    n = round(px / tick)
    return round(n * tick, 10)


def clamp_price(px: float, tick: float, lo: float = 0.01, hi: float = 0.99) -> float:
    """Clamp to (0,1) exclusive bounds via [lo, hi], then tick-round."""
    lo = max(lo, tick if tick > 0 else 0.01)
    hi = min(hi, 1.0 - (tick if tick > 0 else 0.01))
    if hi <= lo:
        return round_tick(0.5, tick)
    return round_tick(min(hi, max(lo, px)), tick)


def quote_prices(mid: float, max_spread_cents: float, tick: float,
                 fraction: float = 0.5,
                 min_price: float = 0.01, max_price: float = 0.99) -> tuple[float, float]:
    """Bid/ask at ± fraction * max_spread from mid; clamped to (0,1) exclusive."""
    half = (max_spread_cents / 100.0) * fraction
    bid = clamp_price(mid - half, tick, min_price, max_price)
    ask = clamp_price(mid + half, tick, min_price, max_price)
    if bid >= ask:
        bid = clamp_price(mid - tick, tick, min_price, max_price)
        ask = clamp_price(mid + tick, tick, min_price, max_price)
    if bid >= ask:
        return bid, ask  # caller may discard
    return bid, ask


def size_per_side(budget_usd: float, mid: float, min_sz: float) -> float:
    if mid <= 0 or budget_usd <= 0:
        return 0.0
    raw = (budget_usd / 2.0) / mid
    return max(raw, float(min_sz))


def maker_quotes(mid: float, max_spread_cents: float, tick: float,
                 position: float, min_sz: float, p: ClobQuoteParams) -> list[Quote]:
    if mid <= 0 or max_spread_cents <= 0:
        return []
    bid_px, ask_px = quote_prices(
        mid, max_spread_cents, tick, p.spread_fraction, p.min_price, p.max_price,
    )
    if not (0.0 < bid_px < ask_px < 1.0):
        return []
    if not (p.min_price <= bid_px < ask_px <= p.max_price):
        return []
    sz = size_per_side(p.budget_usd, mid, min_sz)
    buy_room = max(0.0, p.max_inventory - position)
    sell_room = max(0.0, p.max_inventory + position)
    buy_sz = math.floor(min(sz, buy_room) * 100) / 100
    sell_sz = math.floor(min(sz, sell_room) * 100) / 100
    out: list[Quote] = []
    if buy_sz > 0 and (buy_sz >= max(min_sz, 1.0) or (min_sz <= 0 and buy_sz >= 1.0)):
        out.append(Quote("BUY", bid_px, buy_sz))
    if sell_sz > 0 and (sell_sz >= max(min_sz, 1.0) or (min_sz <= 0 and sell_sz >= 1.0)):
        out.append(Quote("SELL", ask_px, sell_sz))
    return out
