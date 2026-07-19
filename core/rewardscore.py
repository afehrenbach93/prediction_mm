"""
US liquidity-reward scoring helpers (pure, no network).

Venue model (Polymarket US): Score = discountFactor^(ticks_from_best) * size,
snapshotted ~1/s and split pro-rata. This is NOT the global CLOB quadratic
((max_spread - distance)/max_spread)^2 * size model.

Used by scripts/poly_scan.py (ranking) and poly_runner.py (market selection).
"""
from __future__ import annotations

from dataclasses import dataclass


DEFAULT_TICK = 0.001
NEAR_ZERO_BOOK_SCORE = 1.0  # below this → "near-zero competition" flag


@dataclass(frozen=True)
class ScoreResult:
    book_score: float
    my_score: float
    share: float
    est_reward: float
    mid: float
    spread: float
    near_zero: bool


def ticks_from_best(level_px: float, best_px: float, tick: float, side: str) -> int:
    """Integer ticks away from best on this side. Bids: best is highest;
    offers: best is lowest. Negative distance (crossed) clamps to 0."""
    if tick <= 0:
        return 0
    if side == "bid":
        dist = (best_px - level_px) / tick
    else:
        dist = (level_px - best_px) / tick
    return max(0, int(round(dist)))


def level_score(size: float, ticks: int, discount: float) -> float:
    if size <= 0:
        return 0.0
    if ticks <= 0:
        return float(size)
    return float(size) * (discount ** ticks)


def book_side_score(levels: list[tuple[float, float]], discount: float,
                    tick: float, side: str) -> float:
    """Sum discounted size over one side of the book. levels best-first."""
    if not levels:
        return 0.0
    best_px = levels[0][0]
    total = 0.0
    for px, qty in levels:
        total += level_score(qty, ticks_from_best(px, best_px, tick, side), discount)
    return total


def book_competition_score(bids: list[tuple[float, float]],
                           offers: list[tuple[float, float]],
                           discount: float,
                           tick: float = DEFAULT_TICK) -> float:
    return (book_side_score(bids, discount, tick, "bid")
            + book_side_score(offers, discount, tick, "ask"))


def my_touch_score(size_per_side: float, discount: float = 1.0) -> float:
    """Two-sided quote resting at the touch (tick 0 each side)."""
    return 2.0 * float(size_per_side)


def capture_share(my_score: float, book_score: float) -> float:
    denom = my_score + book_score
    return (my_score / denom) if denom > 0 else 0.0


def estimate_reward(pool: float, my_score: float, book_score: float) -> float:
    """Optimistic capture of `pool` under pro-rata score split.
    Cadence of `pool` (per-game vs per-day) is venue-unknown — treat as ceiling."""
    return float(pool) * capture_share(my_score, book_score)


def score_market(bids: list[tuple[float, float]],
                 offers: list[tuple[float, float]],
                 pool: float,
                 budget: float,
                 discount: float = 0.3,
                 tick: float = DEFAULT_TICK,
                 near_zero_threshold: float = NEAR_ZERO_BOOK_SCORE) -> ScoreResult | None:
    """Full US-score assessment for one market. Returns None if no two-sided book."""
    if not bids or not offers:
        return None
    bb, ba = bids[0][0], offers[0][0]
    mid = (bb + ba) / 2.0
    spread = round(ba - bb, 4)
    book = book_competition_score(bids, offers, discount, tick)
    # contracts we can rest, split both sides at mid (collateral ~ mid * contracts)
    my_contracts_total = (budget / mid) if mid > 0 else 0.0
    size_per_side = my_contracts_total / 2.0
    mine = my_touch_score(size_per_side)
    share = capture_share(mine, book)
    return ScoreResult(
        book_score=book,
        my_score=mine,
        share=share,
        est_reward=estimate_reward(pool, mine, book),
        mid=mid,
        spread=spread,
        near_zero=book < near_zero_threshold,
    )


def mid_in_band(mid: float, lo: float = 0.10, hi: float = 0.90) -> bool:
    return lo <= mid <= hi


def hours_to_settle(settle_ts: float, now_ts: float) -> float | None:
    if not settle_ts:
        return None
    return (settle_ts - now_ts) / 3600.0


def slug_denied(slug: str, deny_prefixes: list[str]) -> bool:
    s = (slug or "").lower()
    return any(s.startswith(p.lower()) for p in deny_prefixes if p)
