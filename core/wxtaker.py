"""
Weather sell-taker (LIVE, bounded) — the first settlement-validated edge.

Thesis (validated read-only, then re-validated against PM's OFFICIAL NWS Climatological
Report settlement): retail bids up unlikely temperature buckets (favorite-longshot bias),
so the YES *bid* sits above the forecast's true probability. Selling into that bid
(SELL_SHORT at the bid = short YES / bet NO), held to settlement, was +13c/contract net of
fee over the validation set.

This module is PURE (selection + sizing + a direction-safety check); poly_runner does the
I/O (book reads, order placement, position readback). Everything is bounded by a hard
collateral budget and a probe-first ramp so the never-before-run sell path is proven on a
tiny order before it scales.
"""

# A short YES sold at price p risks (1-p) per contract (you received p; owe 1 if YES wins).
def collateral(sell_price: float, qty: float) -> float:
    return max(0.0, (1.0 - sell_price)) * qty


def sell_candidates(buckets: list[dict], margin: float = 0.10,
                    min_price: float = 0.08, max_price: float = 0.85) -> list[dict]:
    """Buckets whose YES bid is at least `margin` above the model prob (overpriced),
    restricted to a sane price band (avoid 0/1 tails where fills+settlement are degenerate).
    `buckets`: dicts with slug, prob, bid (YES best bid), bid_qty. Best edge first."""
    out = []
    for b in buckets:
        bid, prob = b.get("bid"), b.get("prob")
        if bid is None or prob is None:
            continue
        if not (min_price <= bid <= max_price):
            continue
        edge = bid - prob
        if edge >= margin:
            out.append({"slug": b["slug"], "sell_price": bid, "prob": prob,
                        "edge": round(edge, 4), "bid_qty": b.get("bid_qty") or 0})
    out.sort(key=lambda c: c["edge"], reverse=True)
    return out


def allocate(candidates: list[dict], budget: float, used: float = 0.0,
             per_bucket: int = 10, probe: bool = False) -> list[dict]:
    """Assign a small SELL qty to each candidate so total collateral-at-risk (this run +
    already `used`) never exceeds `budget`. Per-bucket qty is capped by `per_bucket` AND the
    bucket's own bid size (don't sell more than rests). In `probe` mode, only the single best
    candidate gets at most 2 contracts (first-order direction check)."""
    orders, remaining = [], max(0.0, budget - used)
    cands = candidates[:1] if probe else candidates
    for c in cands:
        cap = 2 if probe else per_bucket
        qty = min(cap, int(c.get("bid_qty") or 0) or cap)
        if qty <= 0:
            continue
        # shrink qty so we never exceed the remaining collateral budget
        per = max(1e-6, (1.0 - c["sell_price"]))
        qty = min(qty, int(remaining // per))
        if qty <= 0:
            break
        orders.append({"slug": c["slug"], "sell_price": c["sell_price"], "qty": qty,
                       "edge": c["edge"]})
        remaining -= collateral(c["sell_price"], qty)
    return orders


def wrong_direction(positions: dict, slugs: set) -> list:
    """Safety: our intent is to go SHORT (netPosition <= 0) on every weather bucket we
    trade. Return any targeted slug that came back LONG (netPosition > 0) — that means the
    SELL_SHORT path did the opposite of what we expect and the bot must halt."""
    bad = []
    for slug in slugs:
        p = positions.get(slug)
        if not p:
            continue
        try:
            net = float(p.get("netPosition", p.get("net", 0)) or 0)
        except (TypeError, ValueError):
            net = 0.0
        if net > 0:
            bad.append(slug)
    return bad
