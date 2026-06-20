"""
Spot-anchored fair value for crypto hourly threshold markets.

Why this exists (2026-06-13): the hourly maker bled for days quoting AROUND
the Kalshi book mid. The book mid LAGS BTC/ETH spot by seconds, so a passive
two-sided quote at the money is adversely selected — informed flow lifts our
stale ask (or hits our stale bid) the instant spot moves, before the book
reprices. Two maker retunes of the book-based knobs (wider spread, slower
cycle, wings excluded) did not fix it because the leak is the stale anchor,
not the spread width.

This module computes a fair value from the LEADING signal (Coinbase spot via
core/spotfeed.py) instead of the lagging book, so the maker can:
  - anchor its quotes on its own estimate rather than camp the 0.50 book mid
  - refuse to show the side that fair value says is underpriced (the side
    about to be picked off)
  - pull a side reactively when spot has just moved through the window

Convention: KXBTC / KXETH hourly strikes settle YES iff the underlying is
AT OR ABOVE the strike at expiry (verified empirically 2026-06-13 — within an
expiry, higher strikes carry strictly lower YES prices, the monotone
signature of an "above" threshold; a range bracket would be hump-shaped).
This matches the assumption already encoded in core/momentum.py.

Pure functions only — no I/O, no client. Unit tested in tests/test_fairvalue.py.

DORMANT in prediction-mm: salvaged from kalshi-mm's crypto hourly maker. The
Polymarket reward maker (core/polymaker.py) does NOT use this — kept here as a
tested building block for a future spot-anchored fair-value strategy. The
core/spotfeed.py and core/momentum.py modules referenced above were Kalshi-only
and did not come along.
"""

import math


def norm_cdf(x: float) -> float:
    """Standard normal CDF via erf — avoids a scipy dependency."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def vol_per_sqrt_sec(samples: list[tuple[float, float]]) -> float | None:
    """Realized volatility of the spot tape, in PRICE units per √second.

    `samples` is the (ts, price) history from SpotFeed (oldest→newest). We use
    the standard realized-variance-rate estimator: Σ(Δprice)² / Σ(Δt). The
    returned sigma is scaled so the stdev of the price move over T seconds is
    ≈ sigma · √T (a driftless arithmetic/Bachelier random walk, which is an
    adequate model over the <1h horizon of these markets).

    Returns None when there isn't enough history to estimate — callers must
    fall back to book-anchored behavior, never assume zero vol.
    """
    pts = [(t, p) for t, p in samples if p and p > 0]
    if len(pts) < 8:
        return None
    sq = 0.0
    tot = 0.0
    for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
        dt = t1 - t0
        if dt <= 0:
            continue
        sq += (p1 - p0) ** 2
        tot += dt
    if tot <= 0:
        return None
    rate = sq / tot                 # price² per second
    if rate <= 0:
        return None
    return math.sqrt(rate)


def fair_yes_above(spot: float, strike: float, secs_to_expiry: float,
                   vol_sqrt_sec: float | None) -> float | None:
    """Fair YES price for an "underlying ≥ strike at expiry" market.

    P(S_T ≥ K) ≈ Φ((S − K) / (σ·√T)) under a driftless Bachelier walk. Returns
    None if any input is missing/degenerate (caller falls back to the book).
    """
    if spot <= 0 or strike <= 0 or secs_to_expiry <= 0:
        return None
    if not vol_sqrt_sec or vol_sqrt_sec <= 0:
        return None
    sigma_t = vol_sqrt_sec * math.sqrt(secs_to_expiry)
    if sigma_t <= 0:
        return None
    return norm_cdf((spot - strike) / sigma_t)


def stale_side(move_pct: float | None, threshold_pct: float) -> str | None:
    """Which resting side to PULL because a just-happened spot move makes it
    stale (about to be picked off). YES = "above strike":
        spot up   → YES value rising → our ASK (sell YES) is underpriced → 'ask'
        spot down → our BID (buy YES) is overpriced               → 'bid'
    Returns 'ask' | 'bid' | None. `move_pct` is the trailing-window % move
    from SpotFeed.move(); None (insufficient history) → no pull.
    """
    if move_pct is None or threshold_pct <= 0:
        return None
    if move_pct >= threshold_pct:
        return "ask"
    if move_pct <= -threshold_pct:
        return "bid"
    return None


def quote_gate(fair: float, our_bid: float, our_ask: float,
               edge_margin: float) -> tuple[bool, bool]:
    """Fair-value edge gate. Only quote a side that has positive expected edge
    against our own fair value, net of an `edge_margin` (≈ fee + buffer):

        post BID only if  our_bid ≤ fair − edge_margin   (buying below fair)
        post ASK only if  our_ask ≥ fair + edge_margin   (selling above fair)

    This is what stops the at-the-money bleed: when spot says fair = 0.65, the
    bot will not post a 0.54 ask (selling YES far under fair into a rising
    market) — exactly the round-trips that lost money. Returns
    (allow_bid, allow_ask).
    """
    allow_bid = our_bid <= fair - edge_margin
    allow_ask = our_ask >= fair + edge_margin
    return allow_bid, allow_ask
