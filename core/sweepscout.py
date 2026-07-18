"""
Pure helpers for settlement-sweep paper detection (Polymarket US).

Thesis (master plan Dive 3): after an outcome is effectively known, near-certainty
trades at a discount to $1. Buy that discount. Asymmetry is brutal (win +1¢ /
lose −99¢), so Phase 0 is PAPER ONLY — flag candidates, never auto-buy.

Bright line (enforced later with source feeds): only sweep when the *named
resolution source* has published. This module only flags price/time shape
(ask in band + near end / already extreme mid) for the paper ledger.

Unit tests ≠ thesis validation. GO needs ≥50 settled paper sweeps at ≥99% hit
rate AFTER a source-feed gate exists. Until then: WATCH.
"""


def sweep_return(ask: float) -> float | None:
    """(1 − ask) / ask — return if it settles Yes at $1."""
    try:
        ask = float(ask)
    except (TypeError, ValueError):
        return None
    if not (0.0 < ask < 1.0):
        return None
    return round((1.0 - ask) / ask, 6)


def is_sweep_candidate(ask, bid=None, *, minutes_left=None,
                       min_ask: float = 0.97, max_ask: float = 0.995,
                       max_minutes_left: float = 48 * 60,
                       require_near_end: bool = True) -> bool:
    """Price-shape gate for paper sweep candidates.

    - ask in [min_ask, max_ask]
    - optionally require minutes_left in (0, max_minutes_left]
    - if bid given, mid should also be elevated (avoid one-tick phantoms)
    """
    try:
        ask = float(ask)
        min_ask = float(min_ask)
        max_ask = float(max_ask)
    except (TypeError, ValueError):
        return False
    if not (min_ask <= ask <= max_ask):
        return False
    if bid is not None:
        try:
            bid = float(bid)
            mid = (bid + ask) / 2.0
            if mid < min_ask - 0.02:
                return False
        except (TypeError, ValueError):
            pass
    if require_near_end:
        if minutes_left is None:
            return False
        try:
            minutes_left = float(minutes_left)
        except (TypeError, ValueError):
            return False
        if not (0 < minutes_left <= max_minutes_left):
            return False
    return True


def max_entry_from_error_rate(error_rate: float, k: float = 3.0) -> float | None:
    """max entry ≈ 1 − k × error_rate (plan §6.2)."""
    try:
        error_rate = float(error_rate)
        k = float(k)
    except (TypeError, ValueError):
        return None
    if error_rate < 0 or k <= 0:
        return None
    return round(max(0.0, min(0.999, 1.0 - k * error_rate)), 6)


def paper_pnl_buy(ask: float, size: float, won: bool) -> float | None:
    """Paper PnL for buying `size` contracts at `ask`, settling Yes→$1 / No→$0."""
    try:
        ask = float(ask)
        size = float(size)
    except (TypeError, ValueError):
        return None
    if size <= 0 or not (0.0 < ask < 1.0):
        return None
    if won:
        return round(size * (1.0 - ask), 4)
    return round(-size * ask, 4)


def summarize_asks(asks: list[float], *, min_ask: float = 0.97) -> dict:
    """How close is the book universe to the sweep band?"""
    xs = []
    for a in asks:
        try:
            xs.append(float(a))
        except (TypeError, ValueError):
            continue
    if not xs:
        return {"n": 0}
    xs.sort()
    in_band = [a for a in xs if a >= min_ask]
    return {
        "n": len(xs),
        "n_ge_min": len(in_band),
        "max_ask": round(xs[-1], 6),
        "p90": round(xs[int(0.9 * (len(xs) - 1))], 6) if len(xs) > 1 else round(xs[0], 6),
        "p50": round(xs[len(xs) // 2], 6),
    }


def paper_sweep_record(slug: str, *, ask: float, bid=None, minutes_left=None,
                       today: str = "", title: str = "",
                       ask_size=None, run_id: str = "") -> dict:
    ret = sweep_return(ask)
    tag = run_id or today
    return {
        "model": "sweep-scout",
        "sport": "sweep",
        "market_slug": f"{slug}|{tag}"[:120],
        "outcome": "buy",
        "model_prob": None,
        "market_bid": bid,
        "market_ask": ask,
        "edge": ret,
        "liquid": True,
        "settle_date": today,
        "run_date": today,
        "meta": {
            "slug": slug,
            "title": (title or "")[:160],
            "ask": ask,
            "bid": bid,
            "ask_size": ask_size,
            "minutes_left": (round(minutes_left, 1)
                             if minutes_left is not None else None),
            "sweep_return": ret,
            "run_id": tag,
            "source_confirmed": False,
            "note": "paper-only; no source-feed confirmation yet",
        },
    }


def go_kill(n_settled: int, hit_rate: float | None, paper_pnl: float | None,
            *, min_n: int = 50, min_hit: float = 0.99,
            source_gate: bool = False) -> tuple[str, str]:
    """Sweep needs extreme accuracy — default min_hit 99%.

    Even with a passing sample, refuse GO until a named-source gate exists
    (`source_gate=True`). Price-shape alone is not a strategy.
    """
    if n_settled < min_n:
        return ("WATCH",
                f"need ≥{min_n} settled sweeps (have {n_settled}) "
                f"— detector only, thesis unproven")
    if hit_rate is None or paper_pnl is None:
        return "INCONCLUSIVE", "missing hit_rate/paper_pnl"
    if hit_rate < min_hit or paper_pnl <= 0:
        return "KILL", f"hit={hit_rate} pnl={paper_pnl} — too many −99¢ errors"
    if not source_gate:
        return ("WATCH",
                f"paper looks good (pnl={paper_pnl:+.2f} hit={hit_rate:.2%} "
                f"n={n_settled}) but source-feed gate is missing — not GO")
    return ("GO",
            f"paper_pnl={paper_pnl:+.2f} hit={hit_rate:.2%} on {n_settled} "
            f"with source gate")
