"""
Pure reward-yield math for the Polymarket US liquidity-reward maker (Stage 1).

Answers, per reward-eligible market and WITHOUT risking a dollar:
  (1) modeled reward YIELD — what fraction of the pool our retail budget could
      capture per hour if we rest at the touch, given the competing resting score
      we can see in the book; and
  (2) realized VOLATILITY — how much the market moves while we'd be resting, the
      direct proxy for adverse-selection ("funding") cost.

Ranking markets by reward/hour PER UNIT of volatility surfaces the low-adverse-
selection pools the whole farm thesis needs (rest where the subsidy is fat and the
price barely moves). Every function here is PURE (no network) and unit-tested; the
runnable, network-bound sweep lives in `scripts/reward_yield.py` (it must run on the
US worker — this sandbox is geo-blocked from the venue).

Reward model (per the venue docs / CLAUDE.md):
  Score = discountFactor^(ticks_from_best) * size, snapshotted ~1/s, pool split
  pro-rata across all makers' scores. Resting AT the touch = tick 0 = full weight.
"""
import statistics

# Period-length fallbacks (hours) when a program carries no explicit start/end span.
_PERIOD_HOURS = {"live": 2.0, "day_of": 6.0, "daily_event": 24.0,
                 "daily": 24.0, "early": 24.0}
_DEFAULT_PERIOD_HOURS = 24.0
_DEFAULT_DISCOUNT = 0.3
_DEFAULT_TICK = 0.01


def infer_tick(prices, default=_DEFAULT_TICK):
    """Smallest positive gap between adjacent book prices = the price tick. Futures
    tick in 0.001, game markets in 0.01; the book itself reveals which. Falls back
    to `default` when there aren't two distinct prices to measure."""
    gaps = sorted({round(abs(a - b), 6) for a, b in zip(prices, prices[1:])
                   if abs(a - b) > 1e-9})
    return gaps[0] if gaps else default


def side_score(levels, best_price, disc, tick, max_ticks=8):
    """discount-weighted resting score for ONE side of the book.

    `levels` is [(price, size), ...] best-first. Each level contributes
    size * disc^(ticks_from_best); deep levels decay geometrically, so the estimate
    is dominated by the top ~1-2 ticks and stays robust even if `tick` is mis-inferred.
    Levels beyond `max_ticks` from best are ignored (negligible score)."""
    if not levels or tick <= 0:
        return 0.0
    total = 0.0
    for px, sz in levels:
        ticks = round(abs(best_price - float(px)) / tick)
        if ticks > max_ticks:
            continue
        total += float(sz) * (disc ** ticks)
    return total


def competing_score(bids, offers, disc=_DEFAULT_DISCOUNT, tick=None, max_ticks=8):
    """Total competing maker score in the pool (both sides) — the pro-rata
    denominator we'd compete against. Returns 0.0 for a one-sided/empty book
    (reward programs require a two-sided quote, so a one-sided book earns nothing)."""
    if not bids or not offers:
        return 0.0
    if tick is None:
        tick = infer_tick([float(p) for p, _ in bids]
                          + [float(p) for p, _ in offers])
    return (side_score(bids, float(bids[0][0]), disc, tick, max_ticks)
            + side_score(offers, float(offers[0][0]), disc, tick, max_ticks))


def period_hours(period=None, prog_start=0.0, prog_end=0.0,
                 game_start=0.0, settle=0.0):
    """Hours over which the pool is paid, to normalise per-period pools to /hour.
    An explicit program start/end span wins; else the live game span; else a
    per-period-type fallback; else 24h."""
    if prog_start and prog_end and prog_end > prog_start:
        return (prog_end - prog_start) / 3600.0
    if period == "live" and game_start and settle and settle > game_start:
        return (settle - game_start) / 3600.0
    return _PERIOD_HOURS.get(period, _DEFAULT_PERIOD_HOURS)


def modeled_reward(budget, mid, competing, pool, hours):
    """Optimistic-but-honest modeled reward from resting `budget` at the touch.

    We rest ~budget/mid contracts total (both sides at tick 0 -> full weight), so
    our score ~= our contract count. Pro-rata share = ours / (competing + ours);
    reward/period = share * pool; /hour normalises across period lengths. This is a
    CEILING on the reward leg: it ignores discount decay if we're knocked off the
    touch and (crucially) ignores adverse selection, which the vol term proxies.
    Returns a dict; all zeros when inputs are degenerate."""
    if budget <= 0 or mid <= 0 or pool <= 0 or hours <= 0:
        return {"my_contracts": 0.0, "share": 0.0, "reward_per_period": 0.0,
                "reward_per_hour": 0.0, "yield_per_hr": 0.0}
    my_contracts = budget / mid
    denom = competing + my_contracts
    share = (my_contracts / denom) if denom > 0 else 0.0
    per_period = share * pool
    per_hour = per_period / hours
    return {"my_contracts": my_contracts, "share": share,
            "reward_per_period": per_period, "reward_per_hour": per_hour,
            "yield_per_hr": per_hour / budget}   # $ reward / $ rested / hour


def realized_vol(samples):
    """Adverse-selection proxy from a short book-mid time series.

    `samples` is [(ts_seconds, mid_price), ...] (order-independent; None mids
    dropped). Returns the average absolute mid drift per minute (mean |dmid|
    normalised by elapsed minutes), the stdev of per-sample moves, and the largest
    single move. More movement while resting = more getting picked off = higher
    funding cost. Degenerate (<2 usable points) -> all zeros."""
    pts = sorted((float(t), float(m)) for t, m in (samples or []) if m is not None)
    if len(pts) < 2:
        return {"n": len(pts), "span_min": 0.0, "vol_per_min": 0.0,
                "move_stdev": 0.0, "max_move": 0.0}
    moves = [abs(pts[i][1] - pts[i - 1][1]) for i in range(1, len(pts))]
    span_min = max((pts[-1][0] - pts[0][0]) / 60.0, 1e-9)
    return {"n": len(pts), "span_min": span_min,
            "vol_per_min": sum(moves) / span_min,
            "move_stdev": statistics.pstdev(moves) if len(moves) > 1 else 0.0,
            "max_move": max(moves)}


def rank_key(reward_per_hour, vol_per_min, eps=1e-4):
    """Market-selection sort key: reward/hour per unit of volatility. High reward
    with low movement (low adverse selection) ranks first. `eps` keeps a zero-vol
    market finite rather than infinite."""
    return reward_per_hour / (vol_per_min + eps)


def select_reward_markets(windows, vol_by_slug=None, max_markets=5, vol_cap=0.0,
                          eps=1e-4):
    """Stage 2 — selection-first quoting. Choose which in-window reward markets to
    rest quotes on.

    `windows`: [(slug, period, pool), ...] (the venue's active reward windows).
    `vol_by_slug`: {slug: vol_per_min} rolling volatility (the adverse-selection
    proxy), or None/empty when unavailable.

    WITHOUT vol data this is IDENTICAL to the legacy behavior — the top `max_markets`
    by pool — so the live path can never regress. WITH vol data it ranks by
    reward-rate-per-unit-volatility (`pool / period_hours ÷ vol`): prefer fat, fast-
    paying pools whose price barely moves (low adverse selection). When `vol_cap` > 0
    it also HARD-EXCLUDES markets whose measured vol exceeds the cap (too choppy to
    farm). Markets with no measured vol are treated as neutral (ranked on reward rate
    alone), never excluded. Returns the selected [(slug, period, pool), ...]."""
    if not windows:
        return []
    if not vol_by_slug:
        return sorted(windows, key=lambda w: -w[2])[:max_markets]
    scored = []
    for slug, period, pool in windows:
        vol = vol_by_slug.get(slug)
        if vol is None:
            vol = 0.0                                  # unmeasured -> neutral
        elif vol_cap > 0 and vol > vol_cap:
            continue                                   # too choppy: skip
        rate = pool / period_hours(period)             # $/hr the pool pays out
        # sort by rank desc, then pool desc as a stable tiebreak
        scored.append((rank_key(rate, vol, eps), pool, slug, period))
    scored.sort(key=lambda s: (-s[0], -s[1]))
    return [(s[2], s[3], s[1]) for s in scored[:max_markets]]
