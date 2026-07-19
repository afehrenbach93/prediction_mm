"""
Pure helpers for same-venue deterministic arb detection (Polymarket US).

Flavors we can check from books alone (no cross-venue, no Kalshi):
  1) Binary complement — YES_ask + synthetic NO_ask < 1
     (synthetic NO_ask ≈ 1 − YES_bid when the book is Yes-sided)
  2) Exhaustive partition — Σ YES_asks across sibling outcome legs < 1
     (e.g. Liga MX home/away/draw)

READ-ONLY / paper. This module is the *detector*; the thesis is NOT proven by
unit tests. GO requires a measured sample of actionable opportunities with
executable depth that survive fees — see go_kill().

Crypto Up/Down complete-set arb is a CLOSED thesis — do not re-open it here.
"""


def synthetic_no_ask(yes_bid) -> float | None:
    try:
        yes_bid = float(yes_bid)
    except (TypeError, ValueError):
        return None
    if not (0.0 < yes_bid < 1.0):
        return None
    return round(1.0 - yes_bid, 6)


def binary_complement_edge(yes_bid, yes_ask, *, fee_buffer: float = 0.01,
                           yes_ask_size=None, yes_bid_size=None) -> dict | None:
    """Long-the-complement: buy YES @ ask + buy NO @ (1−bid).

    Edge > 0 means locked profit before fees if both legs fill at those prices.
    On a Yes-only book this only fires when the book is locked/crossed
    (ask < bid) or nearly so after buffer — rare, but O(1) to check.

    `depth` = min size across the two synthetic legs (contracts), when sizes given.
    """
    try:
        yes_bid = float(yes_bid)
        yes_ask = float(yes_ask)
        fee_buffer = float(fee_buffer)
    except (TypeError, ValueError):
        return None
    no_ask = synthetic_no_ask(yes_bid)
    if no_ask is None or not (0.0 < yes_ask < 1.0):
        return None
    cost = yes_ask + no_ask
    edge = 1.0 - cost - fee_buffer
    depth = None
    try:
        if yes_ask_size is not None and yes_bid_size is not None:
            depth = min(float(yes_ask_size), float(yes_bid_size))
    except (TypeError, ValueError):
        depth = None
    return {
        "kind": "binary_complement",
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "cost": round(cost, 6),
        "edge": round(edge, 6),
        "raw_edge": round(1.0 - cost, 6),  # before fee buffer
        "depth": depth,
        "actionable": edge > 0 and (depth is None or depth > 0),
    }


def partition_edge(asks: list[float], *, fee_buffer: float = 0.02,
                   sizes: list[float] | None = None) -> dict | None:
    """Long-the-book on an exhaustive set: Σ asks < 1 − fee_buffer.

    `depth` = min available size across legs (VWAP-at-touch; not book-walked).
    """
    try:
        asks = [float(a) for a in asks]
        fee_buffer = float(fee_buffer)
    except (TypeError, ValueError):
        return None
    if len(asks) < 2 or any(a <= 0 or a >= 1 for a in asks):
        return None
    cost = sum(asks)
    edge = 1.0 - cost - fee_buffer
    depth = None
    if sizes is not None:
        try:
            sz = [float(s) for s in sizes]
            if len(sz) == len(asks) and all(s >= 0 for s in sz):
                depth = min(sz) if sz else None
        except (TypeError, ValueError):
            depth = None
    raw = round(1.0 - cost, 6)
    # Huge underrounds are almost always incomplete partitions (missing "other"
    # / not-exhaustive sibling set) — not a free lunch. Flag; don't treat as GO fuel.
    suspect = raw > 0.10
    actionable = edge > 0 and (depth is None or depth > 0) and not suspect
    return {
        "kind": "partition",
        "n_legs": len(asks),
        "asks": [round(a, 6) for a in asks],
        "cost": round(cost, 6),
        "edge": round(edge, 6),
        "raw_edge": raw,
        "depth": depth,
        "suspect_incomplete": suspect,
        "actionable": actionable,
    }


def family_key(slug: str) -> str | None:
    """Strip the last '-segment' to group sibling outcome markets.

    `atc-lmx-aft-ame-2026-07-24-aft` → `atc-lmx-aft-ame-2026-07-24`
    Returns None if the slug is too short to be a family member.
    """
    if not slug or "-" not in slug:
        return None
    base, _tail = slug.rsplit("-", 1)
    if base.count("-") < 2:
        return None
    return base


def group_families(slugs: list[str]) -> dict[str, list[str]]:
    """slug → family; only keep families with ≥2 distinct members."""
    fam: dict[str, list[str]] = {}
    for s in slugs:
        k = family_key(s)
        if not k:
            continue
        fam.setdefault(k, [])
        if s not in fam[k]:
            fam[k].append(s)
    return {k: v for k, v in fam.items() if len(v) >= 2}


def summarize_edges(raw_edges: list[float]) -> dict:
    """Distribution of raw (pre-fee) edges. Negative = overround (normal)."""
    xs = [float(x) for x in raw_edges if x is not None]
    if not xs:
        return {"n": 0}
    xs.sort()
    n = len(xs)
    pos = [x for x in xs if x > 0]
    near = [x for x in xs if -0.05 <= x <= 0.05]  # within 5¢ of fair

    def pct(p: float) -> float:
        if n == 1:
            return xs[0]
        i = min(n - 1, max(0, int(round((p / 100.0) * (n - 1)))))
        return xs[i]

    return {
        "n": n,
        "min": round(xs[0], 6),
        "p25": round(pct(25), 6),
        "p50": round(pct(50), 6),
        "p75": round(pct(75), 6),
        "max": round(xs[-1], 6),
        "n_positive": len(pos),
        "n_near_fair": len(near),
        "best": round(xs[-1], 6),
    }


def paper_arb_record(kind: str, *, family: str, legs: list[str],
                     edge: float, cost: float, today: str,
                     detail: dict | None = None,
                     run_id: str = "") -> dict:
    """model_predictions row for an observed (paper) arb opportunity.

    `run_id` (e.g. HHMM) makes multiple observations/day unique under the
    (model, market_slug, settle_date, run_date) conflict key — we need a
    time series, not one flag per day.
    """
    tag = run_id or today
    uniq = f"{kind}|{family}|{tag}"[:120]
    return {
        "model": "arb-scan",
        "sport": "arb",
        "market_slug": uniq,
        "outcome": kind,
        "model_prob": None,
        "market_bid": None,
        "market_ask": cost,
        "edge": edge,
        "liquid": True,
        "settle_date": today,
        "run_date": today,
        "meta": {
            "kind": kind,
            "family": family,
            "legs": legs,
            "cost": cost,
            "edge": edge,
            "run_id": tag,
            **(detail or {}),
        },
    }


def go_kill(n_actionable: int, n_with_depth: int, median_edge: float | None,
            *, min_n: int = 30, min_depth_hits: int = 10,
            min_median_edge: float = 0.005, n_rules_ok: int | None = None
            ) -> tuple[str, str]:
    """Deprecated wrapper — prefer core.arbrules.go_kill (rules-complete only)."""
    from core import arbrules as ar
    n = n_rules_ok if n_rules_ok is not None else n_actionable
    return ar.go_kill(n, n_with_depth, median_edge, min_n=min_n,
                      min_depth_hits=min_depth_hits,
                      min_median_edge=min_median_edge)
