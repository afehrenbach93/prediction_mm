"""
Pure helpers for same-venue deterministic arb detection (Polymarket US).

Flavors we can check from books alone (no cross-venue, no Kalshi):
  1) Binary complement — YES_ask + synthetic NO_ask < 1
     (synthetic NO_ask ≈ 1 − YES_bid when the book is Yes-sided)
  2) Exhaustive partition — Σ YES_asks across sibling outcome legs < 1
     (e.g. Liga MX home/away/draw)

READ-ONLY / paper. Execution + rules-exhaustiveness verification are later.
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


def binary_complement_edge(yes_bid, yes_ask, *, fee_buffer: float = 0.01) -> dict | None:
    """Long-the-complement: buy YES @ ask + buy NO @ (1−bid).

    Edge > 0 means locked profit before fees if both legs fill at those prices.
    On a Yes-only book this only fires when the book is locked/crossed
    (ask < bid) or nearly so after buffer — rare, but O(1) to check.
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
    return {
        "kind": "binary_complement",
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "cost": round(cost, 6),
        "edge": round(edge, 6),
        "actionable": edge > 0,
    }


def partition_edge(asks: list[float], *, fee_buffer: float = 0.02) -> dict | None:
    """Long-the-book on an exhaustive set: Σ asks < 1 − fee_buffer."""
    try:
        asks = [float(a) for a in asks]
        fee_buffer = float(fee_buffer)
    except (TypeError, ValueError):
        return None
    if len(asks) < 2 or any(a <= 0 or a >= 1 for a in asks):
        return None
    cost = sum(asks)
    edge = 1.0 - cost - fee_buffer
    return {
        "kind": "partition",
        "n_legs": len(asks),
        "asks": [round(a, 6) for a in asks],
        "cost": round(cost, 6),
        "edge": round(edge, 6),
        "actionable": edge > 0,
    }


def family_key(slug: str) -> str | None:
    """Strip the last '-segment' to group sibling outcome markets.

    `atc-lmx-aft-ame-2026-07-24-aft` → `atc-lmx-aft-ame-2026-07-24`
    Returns None if the slug is too short to be a family member.
    """
    if not slug or "-" not in slug:
        return None
    base, _tail = slug.rsplit("-", 1)
    # need enough structure left (prefix + date-ish)
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


def paper_arb_record(kind: str, *, family: str, legs: list[str],
                     edge: float, cost: float, today: str,
                     detail: dict | None = None) -> dict:
    """model_predictions row for an observed (paper) arb opportunity."""
    uniq = f"{kind}|{family}|{today}"[:120]
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
            **(detail or {}),
        },
    }
