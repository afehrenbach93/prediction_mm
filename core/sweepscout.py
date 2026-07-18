"""
Pure helpers for settlement-sweep paper detection (Polymarket US).

Thesis (master plan Dive 3): after an outcome is effectively known, near-certainty
trades at a discount to $1. Buy that discount. Asymmetry is brutal (win +1¢ /
lose −99¢), so Phase 0 is PAPER ONLY — flag candidates, never auto-buy.

Bright line (enforced later with source feeds): only sweep when the *named
resolution source* has published. This module only flags price/time shape
(ask in band + near end / already extreme mid) for the paper ledger.
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


def paper_sweep_record(slug: str, *, ask: float, bid=None, minutes_left=None,
                       today: str = "", title: str = "") -> dict:
    ret = sweep_return(ask)
    return {
        "model": "sweep-scout",
        "sport": "sweep",
        "market_slug": f"{slug}|{today}"[:120],
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
            "minutes_left": (round(minutes_left, 1)
                             if minutes_left is not None else None),
            "sweep_return": ret,
            "note": "paper-only; no source-feed confirmation yet",
        },
    }


def go_kill(n_settled: int, hit_rate: float | None, paper_pnl: float | None,
            *, min_n: int = 50, min_hit: float = 0.99) -> tuple[str, str]:
    """Sweep needs extreme accuracy — default min_hit 99%."""
    if n_settled < min_n:
        return "WATCH", f"need ≥{min_n} settled sweeps (have {n_settled})"
    if hit_rate is None or paper_pnl is None:
        return "INCONCLUSIVE", "missing hit_rate/paper_pnl"
    if paper_pnl > 0 and hit_rate >= min_hit:
        return "GO", (f"paper_pnl={paper_pnl:+.2f} hit={hit_rate:.2%} on {n_settled} "
                      f"— still requires source-feed gate before live")
    if hit_rate < min_hit or paper_pnl <= 0:
        return "KILL", f"hit={hit_rate} pnl={paper_pnl} — too many −99¢ errors"
    return "WATCH", f"marginal hit={hit_rate:.2%} pnl={paper_pnl:+.2f}"
