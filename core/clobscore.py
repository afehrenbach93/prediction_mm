"""
Polymarket global CLOB liquidity-reward scoring (pure, no network).

Official shape (docs.polymarket.com/market-makers/liquidity-rewards):
  S(v, s) = ((v - s) / v)^2 * b
  where v = max_spread (cents), s = distance from size-cutoff-adjusted mid (cents).

API `rewards.max_spread` is in **cents** (e.g. 4.5 → 4.5¢ = 0.045 price).

This is NOT the Polymarket US exponential tick-discount model.
"""
from __future__ import annotations

from dataclasses import dataclass

C_SINGLE_SIDE = 3.0  # docs: scaling factor c currently 3.0 on all markets


@dataclass(frozen=True)
class ClobScoreResult:
    mid: float
    spread: float
    max_spread_price: float
    book_score: float          # Q_min of resting book (competition)
    book_score_raw: float      # Q_one + Q_two (deep-dive style sum)
    qualifying_notional: float  # $ notional of size inside max_spread band
    my_score: float
    est_daily: float
    yield_pct: float            # est_daily / budget * 100
    near_zero: bool


def daily_rate(rewards: dict | None) -> float:
    if not rewards:
        return 0.0
    rates = rewards.get("rates") or []
    total = 0.0
    for r in rates:
        try:
            total += float(r.get("rewards_daily_rate") or 0)
        except (TypeError, ValueError):
            pass
    return total


def max_spread_cents(rewards: dict | None) -> float:
    if not rewards:
        return 0.0
    try:
        return float(rewards.get("max_spread") or 0)
    except (TypeError, ValueError):
        return 0.0


def min_size(rewards: dict | None) -> float:
    if not rewards:
        return 0.0
    try:
        return float(rewards.get("min_size") or 0)
    except (TypeError, ValueError):
        return 0.0


def order_weight(distance_cents: float, max_spread_cents: float, b: float = 1.0) -> float:
    """S(v,s) = ((v-s)/v)^2 * b. Zero outside the band."""
    v = max_spread_cents
    if v <= 0 or distance_cents < 0 or distance_cents > v:
        return 0.0
    return ((v - distance_cents) / v) ** 2 * b


def side_score(levels: list[tuple[float, float]], mid: float, max_spread_cents: float,
               min_sz: float = 0.0) -> tuple[float, float]:
    """Score one side of the YES book relative to mid.

    Returns (quadratic_score, qualifying_notional_usd) where notional ≈ size * mid.
    Levels below min_size are ignored for scoring (approx of size-cutoff mid).
    """
    if not levels or max_spread_cents <= 0 or mid <= 0:
        return 0.0, 0.0
    band = max_spread_cents / 100.0  # cents → price
    score = 0.0
    notional = 0.0
    for px, sz in levels:
        if sz < min_sz:
            continue
        dist_price = abs(px - mid)
        if dist_price > band + 1e-12:
            continue
        dist_cents = dist_price * 100.0
        w = order_weight(dist_cents, max_spread_cents)
        if w <= 0:
            continue
        score += w * sz
        notional += sz * mid
    return score, notional


def q_min(q_one: float, q_two: float, mid: float, c: float = C_SINGLE_SIDE) -> float:
    """Two-sided balancing per docs."""
    if 0.10 <= mid <= 0.90:
        # single-sided can score at reduced rate
        return max(min(q_one, q_two), max(q_one, q_two) / c)
    # tails: must be double-sided
    return min(q_one, q_two)


def book_competition(bids: list[tuple[float, float]],
                     asks: list[tuple[float, float]],
                     max_spread_cents: float,
                     min_sz: float = 0.0) -> tuple[float, float, float, float]:
    """Returns (q_min, q_raw_sum, qualifying_notional, mid)."""
    if not bids or not asks:
        return 0.0, 0.0, 0.0, 0.0
    mid = (bids[0][0] + asks[0][0]) / 2.0
    q_bid, n_bid = side_score(bids, mid, max_spread_cents, min_sz)
    q_ask, n_ask = side_score(asks, mid, max_spread_cents, min_sz)
    return q_min(q_bid, q_ask, mid), q_bid + q_ask, n_bid + n_ask, mid


def my_twosided_score(budget: float, mid: float, max_spread_cents: float,
                      fraction_of_max: float = 0.5) -> float:
    """Hypothetical two-sided quote: `budget` notional split both sides,
    each resting at `fraction_of_max` * max_spread from mid.

    At half max spread, per-unit weight = 0.25. Q_min of equal sides = side score.
    """
    if mid <= 0 or budget <= 0 or max_spread_cents <= 0:
        return 0.0
    size_per_side = (budget / 2.0) / mid
    dist_cents = fraction_of_max * max_spread_cents
    w = order_weight(dist_cents, max_spread_cents)
    q_side = w * size_per_side
    # equal two-sided → Q_min = q_side (also equals max/c path)
    return q_min(q_side, q_side, mid)


def estimate_capture(daily_rate: float, my_score: float, book_score: float) -> float:
    denom = my_score + book_score
    if denom <= 0:
        return float(daily_rate) if my_score > 0 else 0.0
    return float(daily_rate) * my_score / denom


def score_market(bids: list[tuple[float, float]],
                 asks: list[tuple[float, float]],
                 rewards: dict,
                 budget: float = 500.0,
                 near_zero_notional: float = 50.0,
                 use_qmin_book: bool = True) -> ClobScoreResult | None:
    rate = daily_rate(rewards)
    v = max_spread_cents(rewards)
    msz = min_size(rewards)
    if not bids or not asks or v <= 0:
        return None
    qmin, qraw, qnotional, mid = book_competition(bids, asks, v, msz)
    book = qmin if use_qmin_book else qraw
    spread = round(asks[0][0] - bids[0][0], 4)
    mine = my_twosided_score(budget, mid, v, fraction_of_max=0.5)
    est = estimate_capture(rate, mine, book)
    return ClobScoreResult(
        mid=mid,
        spread=spread,
        max_spread_price=v / 100.0,
        book_score=book,
        book_score_raw=qraw,
        qualifying_notional=qnotional,
        my_score=mine,
        est_daily=est,
        yield_pct=(est / budget * 100.0) if budget else 0.0,
        near_zero=qnotional < near_zero_notional,
    )
