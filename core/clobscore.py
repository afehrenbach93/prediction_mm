"""
Polymarket global CLOB liquidity-reward scoring (pure, no network).

Reconciled to docs.polymarket.com/market-makers/liquidity-rewards:

  S(v, s) = ((v - s) / v)^2 * b
  v = max incentive spread (cents); s = distance from size-cutoff-adjusted mid (cents)
  Q_one / Q_two = sum of S * size on each side (incl. complement view on YES book)
  Q_min = max(min(Q1,Q2), max(Q1,Q2)/c) when mid in [0.10, 0.90], else min(Q1,Q2)
  c = 3.0 (docs: currently 3.0 on all markets)

API `rewards.max_spread` is in **cents** (4.5 → 4.5¢).
`rewards.min_size` floors levels that form the adjusted mid and that score.

Capture estimate (deep-dive / pro-rata share of daily rate):
  est $/day = daily_rate * my_score / (my_score + book_score)
"""
from __future__ import annotations

from dataclasses import dataclass

C_SINGLE_SIDE = 3.0


@dataclass(frozen=True)
class ClobScoreResult:
    mid: float                 # size-cutoff-adjusted mid
    raw_mid: float             # best bid/ask mid (unfiltered)
    spread: float
    max_spread_price: float
    book_score: float          # Q_min of resting book
    book_score_raw: float      # Q_one + Q_two
    qualifying_notional: float
    my_score: float
    est_daily: float
    yield_pct: float
    near_zero: bool


def daily_rate(rewards: dict | None) -> float:
    if not rewards:
        return 0.0
    total = 0.0
    for r in rewards.get("rates") or []:
        try:
            total += float(r.get("rewards_daily_rate") or 0)
        except (TypeError, ValueError):
            pass
    return total


def normalize_max_spread_cents(raw: float) -> float:
    """API usually sends cents (e.g. 3.5). Some payloads use price units (0.035).
    Values in (0, 1] are treated as price units → ×100 to cents."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if v <= 0:
        return 0.0
    if v <= 1.0:
        return v * 100.0
    return v


def max_spread_cents(rewards: dict | None) -> float:
    if not rewards:
        return 0.0
    try:
        return normalize_max_spread_cents(float(rewards.get("max_spread") or 0))
    except (TypeError, ValueError):
        return 0.0


def min_size(rewards: dict | None) -> float:
    if not rewards:
        return 0.0
    try:
        return float(rewards.get("min_size") or 0)
    except (TypeError, ValueError):
        return 0.0


def order_weight(distance_cents: float, v_cents: float, b: float = 1.0) -> float:
    """S(v,s). Zero outside the band."""
    if v_cents <= 0 or distance_cents < 0 or distance_cents > v_cents:
        return 0.0
    return ((v_cents - distance_cents) / v_cents) ** 2 * b


def adjusted_midpoint(bids: list[tuple[float, float]],
                      asks: list[tuple[float, float]],
                      min_sz: float) -> tuple[float, float]:
    """Size-cutoff-adjusted mid: ignore levels with size < min_size.
    Returns (adjusted_mid, raw_mid). Falls back to raw if either side empty
    after the cutoff."""
    if not bids or not asks:
        return 0.0, 0.0
    raw = (bids[0][0] + asks[0][0]) / 2.0
    bb = next((p for p, s in bids if s >= min_sz), None)
    ba = next((p for p, s in asks if s >= min_sz), None)
    if bb is None or ba is None or ba <= bb:
        return raw, raw
    return (bb + ba) / 2.0, raw


def side_score(levels: list[tuple[float, float]], mid: float, v_cents: float,
               min_sz: float = 0.0) -> tuple[float, float]:
    """(quadratic_score, qualifying_notional) for one side vs adjusted mid."""
    if not levels or v_cents <= 0 or mid <= 0:
        return 0.0, 0.0
    band = v_cents / 100.0
    score = 0.0
    notional = 0.0
    for px, sz in levels:
        if sz < min_sz:
            continue
        dist_price = abs(px - mid)
        if dist_price > band + 1e-12:
            continue
        w = order_weight(dist_price * 100.0, v_cents)
        if w <= 0:
            continue
        score += w * sz
        notional += sz * mid
    return score, notional


def q_min(q_one: float, q_two: float, mid: float, c: float = C_SINGLE_SIDE) -> float:
    if 0.10 <= mid <= 0.90:
        return max(min(q_one, q_two), max(q_one, q_two) / c)
    return min(q_one, q_two)


def book_competition(bids: list[tuple[float, float]],
                     asks: list[tuple[float, float]],
                     v_cents: float,
                     min_sz: float = 0.0) -> tuple[float, float, float, float, float]:
    """Returns (q_min, q_raw_sum, qualifying_notional, adj_mid, raw_mid)."""
    if not bids or not asks:
        return 0.0, 0.0, 0.0, 0.0, 0.0
    adj, raw = adjusted_midpoint(bids, asks, min_sz)
    q_bid, n_bid = side_score(bids, adj, v_cents, min_sz)
    q_ask, n_ask = side_score(asks, adj, v_cents, min_sz)
    return (q_min(q_bid, q_ask, adj), q_bid + q_ask, n_bid + n_ask, adj, raw)


def my_twosided_score(budget: float, mid: float, v_cents: float,
                      fraction_of_max: float = 0.5) -> float:
    """Two-sided quote: budget notional split both sides at fraction * max_spread.
    Half max → weight 0.25."""
    if mid <= 0 or budget <= 0 or v_cents <= 0:
        return 0.0
    size_per_side = (budget / 2.0) / mid
    w = order_weight(fraction_of_max * v_cents, v_cents)
    q_side = w * size_per_side
    return q_min(q_side, q_side, mid)


def estimate_capture(rate: float, my_score: float, book_score: float) -> float:
    denom = my_score + book_score
    if denom <= 0:
        return float(rate) if my_score > 0 else 0.0
    return float(rate) * my_score / denom


def score_market(bids: list[tuple[float, float]],
                 asks: list[tuple[float, float]],
                 rewards: dict,
                 budget: float = 500.0,
                 near_zero_notional: float = 50.0,
                 spread_fraction: float = 0.5) -> ClobScoreResult | None:
    rate = daily_rate(rewards)
    v = max_spread_cents(rewards)
    msz = min_size(rewards)
    if not bids or not asks or v <= 0:
        return None
    qmin, qraw, qnotional, adj, raw = book_competition(bids, asks, v, msz)
    spread = round(asks[0][0] - bids[0][0], 4)
    mine = my_twosided_score(budget, adj, v, fraction_of_max=spread_fraction)
    est = estimate_capture(rate, mine, qmin)
    return ClobScoreResult(
        mid=adj,
        raw_mid=raw,
        spread=spread,
        max_spread_price=v / 100.0,
        book_score=qmin,
        book_score_raw=qraw,
        qualifying_notional=qnotional,
        my_score=mine,
        est_daily=est,
        yield_pct=(est / budget * 100.0) if budget else 0.0,
        near_zero=qnotional < near_zero_notional,
    )
