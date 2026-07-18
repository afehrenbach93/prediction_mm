"""
Pure helpers for the READ-ONLY flow scout (informed-flow / endgame-size research).

Thesis: unusually large prints on a live tape — especially near market end — may
proxy informed money. We do NOT trade; we flag spikes, stamp a lagged executable
price, and paper-score vs settlement later.

Success (GO) after enough settled flags — see PILOT.md / FLOW_SCOUT section:
  ≥100 settled flags, paper PnL @ copy_ask > 0, hit rate ≥ ~55%, endgame subset
  not worse than the rest. KILL if coin-flip or lag kills EV.
"""


def trade_size(trade: dict) -> float:
    """Contracts in the print (fallback 0)."""
    try:
        return float((trade or {}).get("size") or 0)
    except (TypeError, ValueError):
        return 0.0


def trade_notional(trade: dict) -> float:
    """Approx USDC notional = size * price (0 if either missing)."""
    try:
        px = float((trade or {}).get("price") or 0)
    except (TypeError, ValueError):
        px = 0.0
    return round(trade_size(trade) * px, 4)


def trade_dedupe_key(trade: dict) -> str:
    tx = str((trade or {}).get("transactionHash") or "")
    return (f"{tx}-{(trade or {}).get('asset', '')}-{(trade or {}).get('side', '')}-"
            f"{(trade or {}).get('size', '')}-{(trade or {}).get('price', '')}")


def median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    mid = len(s) // 2
    if len(s) % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def push_baseline(hist: list[float], size: float, maxlen: int = 40) -> list[float]:
    """Append a size observation; return the trimmed history (mutates hist)."""
    if size > 0:
        hist.append(float(size))
    if len(hist) > maxlen:
        del hist[: len(hist) - maxlen]
    return hist


def is_spike(size: float, baseline: list[float], *, mult: float = 5.0,
             min_size: float = 100.0, min_baseline_n: int = 8) -> bool:
    """True if `size` clears an absolute floor AND ≥ mult × baseline median.

    Needs a warm baseline so the first prints on a quiet market don't all flag.
    """
    try:
        size = float(size)
        mult = float(mult)
        min_size = float(min_size)
    except (TypeError, ValueError):
        return False
    if size < min_size or mult <= 0:
        return False
    if len(baseline) < min_baseline_n:
        return False
    med = median(baseline)
    if med is None or med <= 0:
        return False
    return size >= mult * med


def minutes_to_end(end_ts, now_ts) -> float | None:
    """Minutes from now_ts → end_ts (unix seconds). None if either missing."""
    try:
        end_ts = float(end_ts)
        now_ts = float(now_ts)
    except (TypeError, ValueError):
        return None
    return (end_ts - now_ts) / 60.0


def market_duration_minutes(start_ts, end_ts) -> float | None:
    """Listed market lifetime in minutes (end - start). None if unknown/invalid."""
    try:
        start_ts = float(start_ts)
        end_ts = float(end_ts)
    except (TypeError, ValueError):
        return None
    if end_ts <= start_ts:
        return None
    return (end_ts - start_ts) / 60.0


def endgame_window_minutes(duration_min: float | None, *,
                           frac: float = 0.5,
                           floor_min: float = 30.0,
                           cap_min: float = 360.0,
                           short_max: float = 240.0) -> float | None:
    """How many minutes-before-end count as endgame for THIS market.

    Fixed absolute windows (e.g. always 180) are wrong across types:
      - 3h soccer / UFC: want most/all of the live event, not just a tail slice
        that assumes a multi-day market.
      - week-long politics: last 50% would be days — must CAP.

    Rules:
      - unknown duration → None (caller decides)
      - short markets (duration ≤ short_max, default 4h) → window = full duration
        (any spike while the market is still live is "in play / endgame")
      - longer markets → clamp(duration * frac, floor, cap)
        defaults: last 50%, at least 30m, at most 6h
    """
    if duration_min is None:
        return None
    try:
        duration_min = float(duration_min)
        frac = float(frac)
        floor_min = float(floor_min)
        cap_min = float(cap_min)
        short_max = float(short_max)
    except (TypeError, ValueError):
        return None
    if duration_min <= 0:
        return None
    if duration_min <= short_max:
        return duration_min
    # longer-dated: relative slice, bounded
    frac = min(max(frac, 0.05), 1.0)
    return max(floor_min, min(cap_min, duration_min * frac))


def in_endgame(minutes_left: float | None, window_min: float | None) -> bool:
    """True if minutes_left is inside [0, window_min].

    window_min None → False (unknown schedule; still record spike, just untagged).
    window_min <= 0 → True (explicit any-time endgame tag — rare).
    """
    if window_min is None:
        return False
    try:
        window_min = float(window_min)
    except (TypeError, ValueError):
        return False
    if window_min <= 0:
        return True
    if minutes_left is None:
        return False
    return 0 <= float(minutes_left) <= window_min


def paper_flow_record(trade: dict, *, copy_ask=None, spike_mult=None,
                      baseline_med=None, minutes_left=None, today: str = "",
                      endgame: bool = False, duration_min=None,
                      endgame_window=None) -> dict:
    """Shape one flagged print into a model_predictions row (model='flow-scout')."""
    base = str(trade.get("slug") or trade.get("conditionId") or "")[:88]
    tx = str(trade.get("transactionHash") or "")
    uniq = (tx[-10:] or str(trade.get("timestamp", "")))
    side = str(trade.get("side", "")).lower()
    try:
        their_px = float(trade.get("price")) if trade.get("price") is not None else None
    except (TypeError, ValueError):
        their_px = None
    size = trade_size(trade)
    return {
        "model": "flow-scout",
        "sport": "flow",
        "market_slug": f"{base}|{uniq}"[:120],
        "outcome": side,
        "model_prob": None,
        "market_bid": None,
        "market_ask": their_px,          # their print price
        "edge": None,
        "liquid": None,
        "settle_date": today,
        "run_date": today,
        "meta": {
            "slug": base,
            "title": str(trade.get("title", ""))[:160],
            "outcome_name": trade.get("outcome"),
            "size": size,
            "notional": trade_notional(trade),
            "ts": trade.get("timestamp"),
            "tx": tx,
            "asset": trade.get("asset"),
            "condition_id": trade.get("conditionId"),
            "wallet": str(trade.get("proxyWallet") or "")[:42],
            "name": str(trade.get("name") or trade.get("pseudonym") or "")[:48],
            "spike_mult": spike_mult,
            "baseline_med": baseline_med,
            "minutes_left": (round(minutes_left, 1) if minutes_left is not None else None),
            "duration_min": (round(duration_min, 1) if duration_min is not None else None),
            "endgame_window": (round(endgame_window, 1)
                               if endgame_window is not None else None),
            "endgame": bool(endgame),
            "copy_ask": copy_ask,
            "lag_bps": (_lag_bps(their_px, copy_ask, side)
                        if (their_px is not None and copy_ask is not None) else None),
        },
    }


def _lag_bps(their_px, copy_ask, side: str):
    try:
        their_px = float(their_px)
        copy_ask = float(copy_ask)
    except (TypeError, ValueError):
        return None
    if their_px <= 0:
        return None
    if side == "buy":
        return round((copy_ask - their_px) / their_px * 10000, 1)
    if side == "sell":
        return round((their_px - copy_ask) / their_px * 10000, 1)
    return None


def go_kill(n_settled: int, hit_rate: float | None, paper_pnl: float | None,
            *, min_n: int = 100, min_hit: float = 0.55) -> tuple[str, str]:
    """Conservative GO/KILL on settled paper results."""
    if n_settled < min_n:
        return "WATCH", f"need ≥{min_n} settled flags (have {n_settled})"
    if hit_rate is None or paper_pnl is None:
        return "INCONCLUSIVE", "missing hit_rate/paper_pnl"
    if paper_pnl > 0 and hit_rate >= min_hit:
        return "GO", (f"paper_pnl={paper_pnl:+.2f} hit_rate={hit_rate:.1%} on {n_settled} "
                      f"— still observe-only; US can't place .com")
    if paper_pnl <= 0 or hit_rate <= 0.52:
        return "KILL", f"paper_pnl={paper_pnl:+.2f} hit_rate={hit_rate} — no edge after lag"
    return "WATCH", f"marginal paper_pnl={paper_pnl:+.2f} hit_rate={hit_rate:.1%}"
